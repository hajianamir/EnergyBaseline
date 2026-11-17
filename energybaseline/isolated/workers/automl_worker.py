"""Regression search with TPOT, H2O AutoML, AutoGluon Tabular or auto-sklearn.

Runs inside one engine's isolated runtime; it never imports the application. Usage:
``python automl_worker.py <private-directory>``. Kept compatible with Python 3.9.

Every engine receives the inner fitting rows and the chronologically later inner
validation rows and selects on that single split. TPOT, AutoGluon and auto-sklearn then
refit the selection on both parts; H2O AutoML has no refit, so its leader stays trained on
the fitting rows. The selected model predicts the outer holdout features. Outer holdout
targets never reach this process.
"""

import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import guard  # noqa: E402


def run_tpot(root, request, data):
    import numpy as np
    from sklearn.model_selection import PredefinedSplit
    import tpot
    from tpot import TPOTRegressor

    x_fit, y_fit, x_val, y_val, x_test = data
    fold = np.r_[-np.ones(len(x_fit), dtype=int), np.zeros(len(x_val), dtype=int)]
    population = 2 * request["models"]
    import dask
    from distributed import Client, LocalCluster

    # TPOT's own cluster would publish a dashboard on every interface. Use an in-process
    # scheduler and worker instead: no TCP transport, HTTP routes removed, loopback binds only.
    dask.config.set(
        {
            "distributed.scheduler.http.routes": [],
            "distributed.worker.http.routes": [],
            "distributed.nanny.http.routes": [],
            "distributed.diagnostics.nvml": False,
        }
    )
    with LocalCluster(
        n_workers=1,
        threads_per_worker=1,
        processes=False,
        dashboard_address="127.0.0.1:0",
        worker_dashboard_address="127.0.0.1:0",
        silence_logs=50,
        memory_limit="1GB",
    ) as cluster, Client(cluster) as client:
        search = TPOTRegressor(
            search_space="linear",
            scorers=["neg_root_mean_squared_error"],
            scorers_weights=[1],
            cv=PredefinedSplit(fold),
            n_jobs=1,
            max_time_mins=None,
            max_eval_time_mins=1,
            early_stop=None,
            generations=2,
            population_size=population,
            random_state=request["seed"],
            client=client,
            verbose=0,
        )
        # TPOT refits the selected pipeline on every row passed here: fitting plus validation.
        search.fit(np.vstack([x_fit, x_val]), np.concatenate([y_fit, y_val]))
        predictions = guard.finite_list(search.predict(x_test))
    evaluated = search.evaluated_individuals
    # TPOT stores negated RMSE (larger is better); a pipeline that failed has an entry in
    # "Eval Error" and no finite score.
    scores = evaluated["root_mean_squared_error"].to_numpy(dtype=float)
    succeeded = np.isfinite(scores) & evaluated["Eval Error"].isna().to_numpy()
    valid = scores[succeeded]
    if not len(valid):
        raise RuntimeError("TPOT evaluated no valid pipeline.")
    steps = [type(step).__name__ for _, step in search.fitted_pipeline_.steps]
    return {
        "engine_version": tpot.__version__,
        "search_method": "TPOT genetic programming, linear pipeline search space",
        "selected_model": steps[-1],
        "selected_pipeline_steps": steps,
        "candidates_evaluated": int(len(scores)),
        "candidates_failed": int(len(scores) - len(valid)),
        "validation_rmse": float(-valid.max()),
        "budget": {"generations": 2, "population_size": population},
        "final_fit_rows": int(len(x_fit) + len(x_val)),
        "predictions": predictions,
    }


def _h2o_server(root, port, user, password):
    import subprocess

    import h2o
    import jdk4py

    realm = os.path.join(root, "h2o-realm.properties")
    descriptor = os.open(realm, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
        stream.write(user + ": " + password + "\n")
    jar = os.path.join(os.path.dirname(h2o.__file__), "backend", "bin", "h2o.jar")
    command = [
        str(jdk4py.JAVA), "-Xmx1g", "-Xms128m", "-Dsys.ai.h2o.telemetry.disabled=true",
        "-Djava.io.tmpdir=" + root, "-Duser.home=" + root, "-jar", jar,
        "-ip", "127.0.0.1", "-web_ip", "127.0.0.1", "-port", str(port), "-baseport", str(port),
        "-ice_root", os.path.join(root, "h2o-ice"), "-log_dir", os.path.join(root, "h2o-log"),
        "-log_level", "WARN", "-nthreads", "1", "-name", "eb-" + user,
        "-hash_login", "-login_conf", realm, "-quiet",
    ]  # fmt: skip
    log = open(os.path.join(root, "h2o-jvm.log"), "ab")
    return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, cwd=root)


def run_h2o(root, request, data):
    """Start one private JVM bound to loopback with one-time credentials, and stop it afterwards."""
    import secrets
    import socket
    import time

    import numpy as np
    import requests

    import h2o
    from h2o.automl import H2OAutoML

    x_fit, y_fit, x_val, y_val, x_test = data
    user, password = "u" + secrets.token_hex(8), secrets.token_hex(24)
    process = None
    try:
        for _ in range(3):
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            process = _h2o_server(root, port, user, password)
            url = "http://127.0.0.1:%d" % port
            deadline = time.monotonic() + 60
            healthy = False
            while time.monotonic() < deadline and process.poll() is None and not healthy:
                try:
                    reply = requests.get(url + "/3/Cloud", auth=(user, password), timeout=2)
                    healthy = reply.status_code == 200 and bool(reply.json().get("cloud_healthy"))
                except (requests.RequestException, ValueError):
                    pass
                if not healthy:
                    time.sleep(0.25)
            if healthy:
                break
            process.kill()
            process.wait()
            process = None
        if process is None:
            raise RuntimeError("H2O JVM did not become healthy.")
        if requests.get(url + "/3/Cloud", timeout=5).status_code != 401:
            raise RuntimeError("H2O accepted an unauthenticated request.")
        h2o.connect(url=url, auth=(user, password), verbose=False, strict_version_check=True)
        h2o.no_progress()
        features = ["f%d" % i for i in range(x_fit.shape[1])]

        def frame(x, y=None):
            values = x if y is None else np.column_stack([x, y])
            names = features if y is None else features + ["target"]
            return h2o.H2OFrame(values, column_names=names, column_types=["real"] * len(names))

        training, validation = frame(x_fit, y_fit), frame(x_val, y_val)
        search = H2OAutoML(
            max_models=request["models"],
            max_runtime_secs=0,
            seed=request["seed"],
            nfolds=0,
            # Deep learning is not reproducible; stacking needs cross-validation, disabled here.
            exclude_algos=["DeepLearning", "StackedEnsemble"],
            sort_metric="RMSE",
            verbosity=None,
        )
        search.train(x=features, y="target", training_frame=training, validation_frame=validation,
                     leaderboard_frame=validation)  # fmt: skip
        board = search.leaderboard.as_data_frame(use_pandas=False)
        if len(board) < 2:
            raise RuntimeError("H2O AutoML trained no model.")
        # A model that H2O started but could not train never reaches the leaderboard.
        events = search.event_log.as_data_frame(use_pandas=False)
        message = events[0].index("message")
        started = sum(
            row[message].startswith("AutoML: starting ") and row[message].endswith(" model training")
            for row in events[1:]
        )
        if started < len(board) - 1:
            raise RuntimeError("H2O event log is inconsistent with its leaderboard.")
        leader = search.leader
        validation_rmse = float(leader.model_performance(validation).rmse())
        # H2O AutoML has no refit: the leader stays trained on the inner fitting rows only.
        predicted = leader.predict(frame(x_test)).as_data_frame(use_pandas=False)
        return {
            "engine_version": h2o.__version__,
            "search_method": "H2O AutoML leaderboard on the chronological validation frame",
            "selected_model": str(leader.algo),
            "candidates_evaluated": int(started),
            "candidates_failed": int(started - (len(board) - 1)),
            "validation_rmse": validation_rmse,
            "budget": {"max_models": request["models"]},
            "final_fit_rows": int(len(x_fit)),
            "java_runtime": "jdk4py",
            "server": "loopback-only JVM with one-time credentials; unauthenticated request refused",
            "predictions": guard.finite_list([float(row[0]) for row in predicted[1:]]),
        }
    finally:
        if process is not None:
            try:
                h2o.cluster().shutdown()
                process.wait(timeout=20)
            except Exception:  # noqa: BLE001 - shutdown is best effort; the kill below is authoritative
                pass
            if process.poll() is None:
                process.kill()
                process.wait()


def run_autogluon(root, request, data):
    import numpy as np
    import pandas as pd

    import autogluon.tabular
    from autogluon.tabular import TabularPredictor

    x_fit, y_fit, x_val, y_val, x_test = data
    seed = request["seed"]
    features = ["f%d" % i for i in range(x_fit.shape[1])]

    def frame(x, y=None):
        table = pd.DataFrame(np.asarray(x), columns=features)
        if y is not None:
            table["target"] = np.asarray(y)
        return table

    families = [
        ("LR", {}),
        ("GBM", {"num_boost_round": 200, "seed": seed}),
        ("XGB", {"n_estimators": 200, "seed": seed}),
        ("CAT", {"iterations": 200, "random_seed": seed, "thread_count": 1}),
        ("RF", {"n_estimators": 100, "random_state": seed}),
        ("XT", {"n_estimators": 100, "random_state": seed}),
    ][: request["models"]]
    predictor = TabularPredictor(
        label="target",
        problem_type="regression",
        eval_metric="root_mean_squared_error",
        path=os.path.join(root, "autogluon"),
        verbosity=0,
    )
    predictor.fit(
        frame(x_fit, y_fit),
        tuning_data=frame(x_val, y_val),
        hyperparameters={name: [options] for name, options in families},
        num_bag_folds=0,
        num_stack_levels=0,
        auto_stack=False,
        dynamic_stacking=False,
        fit_weighted_ensemble=True,
        fit_strategy="sequential",
        time_limit=None,
        num_cpus=1,
        num_gpus=0,
        calibrate=False,
    )
    board = predictor.leaderboard(silent=True)
    best = predictor.model_best
    best_score = float(board.loc[board["model"] == best, "score_val"].iloc[0])
    base_models = int((~board["model"].str.startswith("WeightedEnsemble")).sum())
    # Retrain the selected model (and its ensemble members) on fitting plus validation rows.
    refit = predictor.refit_full(model=best, set_best_to_refit_full=True)
    predicted = predictor.predict(frame(x_test), model=refit[best])
    return {
        "engine_version": autogluon.tabular.__version__,
        "search_method": "AutoGluon Tabular model portfolio with a validation-weighted ensemble",
        "selected_model": str(best),
        "candidates_evaluated": int(len(board)),
        "candidates_failed": int(len(families) - base_models),
        # The weighted ensemble is fit on the validation rows, so its score is optimistic.
        "validation_rmse": -best_score,
        "budget": {"families": [name for name, _ in families]},
        "final_fit_rows": int(len(x_fit) + len(x_val)),
        "predictions": guard.finite_list(predicted.to_numpy()),
    }


def patch_autosklearn_log_server():
    """Work around a lock-starvation defect in auto-sklearn 0.15.0's start-up.

    ``AutoML._get_logger`` waits for its log-server process to publish a port with
    ``while True: with port.get_lock(): if port.value == -1: time.sleep(0.01)``. It sleeps
    while holding the shared lock and releases it only for an instant, and the server needs
    the same lock to publish the port. The lock is not fair, so on some machines the server
    never obtains it and the search waits until the wall-clock budget. Observed on hosted
    Linux machines; the thread stacks are in the stage evidence.

    The server therefore publishes the port without taking that lock. An aligned machine word
    is stored atomically, and the waiting side still reads it under its own lock.
    """
    import contextlib

    import autosklearn.automl as module

    upstream = module.start_log_server
    if getattr(upstream, "publishes_without_lock", False):
        return

    class UnlockedPort:
        def __init__(self, shared):
            self._raw = shared.get_obj()

        def get_lock(self):
            return contextlib.nullcontext()

        @property
        def value(self):
            return self._raw.value

        @value.setter
        def value(self, number):
            self._raw.value = number

    def start_log_server(**kwargs):
        kwargs["port"] = UnlockedPort(kwargs["port"])
        return upstream(**kwargs)

    start_log_server.publishes_without_lock = True
    module.start_log_server = start_log_server


def run_autosklearn(root, request, data):
    import numpy as np
    from sklearn.model_selection import PredefinedSplit

    import autosklearn
    from autosklearn.metrics import root_mean_squared_error
    from autosklearn.regression import AutoSklearnRegressor

    x_fit, y_fit, x_val, y_val, x_test = data
    x_all, y_all = np.vstack([x_fit, x_val]), np.concatenate([y_fit, y_val])
    fold = np.r_[-np.ones(len(x_fit), dtype=int), np.zeros(len(x_val), dtype=int)]
    search = AutoSklearnRegressor(
        time_left_for_this_task=45,
        per_run_time_limit=10,
        initial_configurations_via_metalearning=0,
        ensemble_kwargs={"ensemble_size": 1},
        seed=request["seed"],
        memory_limit=2048,
        n_jobs=1,
        metric=root_mean_squared_error,
        resampling_strategy=PredefinedSplit(fold),
        tmp_folder=os.path.join(root, "autosklearn"),
        delete_tmp_folder_after_terminate=True,
        smac_scenario_args={"runcount_limit": request["models"]},
    )
    patch_autosklearn_log_server()
    search.fit(x_all, y_all)
    if search.automl_._multiprocessing_context != "fork":
        # The replacement server function reaches the child only through a forked process.
        raise RuntimeError("auto-sklearn did not use the expected process start method.")
    status = list(search.cv_results_["status"])
    # With an ensemble of size one the single member is auto-sklearn's own selection.
    members = search.get_models_with_weights()
    if len(members) != 1 or float(members[0][0]) != 1.0:
        raise RuntimeError("auto-sklearn did not select exactly one model.")
    model = members[0][1]
    # The stored member was fit on the fitting rows only, so this is its true holdout RMSE.
    residual = np.asarray(y_val, dtype=float) - np.asarray(model.predict(x_val), dtype=float).reshape(-1)
    validation_rmse = float(np.sqrt(np.mean(residual**2)))
    if type(model).__name__ == "MyDummyRegressor":
        # No searched configuration beat auto-sklearn's mean predictor on the validation rows.
        selected = "dummy_mean"
    else:
        board = search.leaderboard(detailed=False, ensemble_only=True)
        selected = str(board["type"].iloc[0])
        if len(board) != 1 or abs(float(board["cost"].iloc[0]) - validation_rmse) > 1e-8 * max(1.0, validation_rmse):
            raise RuntimeError("auto-sklearn leaderboard disagrees with its selected model.")
    # Models were fit on the fitting rows only; refit the selected one on both parts.
    search.refit(x_all, y_all)
    return {
        "engine_version": autosklearn.__version__,
        "search_method": "auto-sklearn SMAC Bayesian optimisation without meta-learning warm start",
        "selected_model": selected,
        "candidates_evaluated": len(status),
        "candidates_failed": sum(value != "Success" for value in status),
        "validation_rmse": validation_rmse,
        "budget": {"runcount_limit": request["models"], "task_seconds": 45},
        "final_fit_rows": int(len(x_fit) + len(x_val)),
        "predictions": guard.finite_list(search.predict(x_test)),
    }


ENGINES = {"tpot": run_tpot, "h2o": run_h2o, "autogluon": run_autogluon, "autosklearn": run_autosklearn}


def main():
    root = os.path.abspath(sys.argv[1])
    warnings.filterwarnings("ignore")
    guard.watch_parent(root)
    guard.deny_external_network()
    request = guard.read_request(root)
    data = tuple(guard.load(root, name) for name in ("x_fit", "y_fit", "x_val", "y_val", "x_test"))
    result = ENGINES[request["engine"]](root, request, data)
    result["engine"] = request["engine"]
    guard.write_result(root, result)


if __name__ == "__main__":
    main()
