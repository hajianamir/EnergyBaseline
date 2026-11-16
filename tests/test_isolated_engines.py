"""Isolated-engine and model-asset contracts that need no provisioned runtime.

Everything here uses real files, real interpreters and real child processes. Execution of
the engines themselves is verified by ``provisioned_tests`` against provisioned runtimes.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import numpy as np
import pandas as pd
import pytest

from energybaseline import assets, isolated
from energybaseline.workbench import OPERATIONS, SPEC_OPERATIONS, pretrained, run_workbench

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples/isolated"
GUARD = isolated.WORKERS / "guard.py"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("EB_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def _cli(*arguments, home):
    environment = dict(os.environ, EB_HOME=str(home), EB_NONINTERACTIVE="1")
    return subprocess.run(
        [sys.executable, "-m", "energybaseline.cli", *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _alive(pid):
    if sys.platform == "win32":
        listing = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=60
        ).stdout
        return f'"{pid}"' in listing
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A killed child that nobody reaped yet is a zombie, not a running process.
    state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    return state != "Z"


def _wait_dead(pids, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and any(_alive(pid) for pid in pids):
        time.sleep(0.2)
    return [pid for pid in pids if _alive(pid)]


@pytest.mark.parametrize("name", sorted(isolated.ENGINES))
@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_locks_pin_and_hash_every_distribution_for_each_platform(name, platform):
    spec = isolated.ENGINES[name]
    text = isolated.lock_path(name).read_text(encoding="utf-8")
    assert "\r" not in text
    blocks = re.split(r"\n(?=[A-Za-z0-9])", text)
    pinned = [block for block in blocks if re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*==", block)]
    assert pinned and all("--hash=sha256:" in block for block in pinned)
    assert not re.search(r"^\s*(-e|--index-url|--extra-index-url|--find-links|https?:|git\+)", text, re.M)
    pins = isolated.locked_distributions(name, platform)
    assert pins[isolated.normalize(spec.distribution)] == spec.version
    assert len(pins) <= len(pinned)
    assert ("colorama" in pins) == (platform == "win32" and name in ("tpot", "autogluon", "chronos"))
    if name == "chronos":
        assert pins["torch"] == "2.15.0"
        assert any(key.startswith("nvidia-") for key in pins) == (platform == "linux")
    if name == "h2o":
        assert pins["jdk4py"] == "17.0.9.2"
    if name == "autosklearn":
        assert spec.platforms == ("linux",) and spec.python == (3, 9) and pins["scikit-learn"] == "0.24.2"


def test_core_environment_never_contains_the_isolated_engines():
    from importlib import metadata

    installed = {isolated.normalize(d.metadata["Name"]) for d in metadata.distributions()}
    assert not installed & {"tpot", "h2o", "autogluon-tabular", "auto-sklearn", "chronos-forecasting"}
    for module in ("tpot", "h2o", "autogluon", "autosklearn", "chronos"):
        assert module not in sys.modules


def test_absent_runtime_fails_closed_with_operator_guidance_everywhere(home):
    for name in isolated.ENGINES:
        status = isolated.engine_status(name)
        expected = (
            "not-installed" if isolated._platform() in isolated.ENGINES[name].platforms else "unsupported-platform"
        )
        assert status["state"] == expected and status["lock_sha256"] == isolated.lock_sha256(name)
        guidance = f"engines install {name}" if expected == "not-installed" else "no release for this operating system"
        with pytest.raises(isolated.EngineUnavailable, match=guidance):
            isolated.run_engine(name, {}, {}, timeout=5)
    frame = pd.read_csv(EXAMPLES / "series.csv")
    request = json.loads((EXAMPLES / "automl-tpot.json").read_text())
    with pytest.raises(isolated.ProvisioningRequired, match="engines install tpot"):
        run_workbench(request, frame)
    for request_name, needle in (("chronos-bolt.json", "chronos-bolt-tiny"), ("ghostnet.json", "ghostnet-imagenet")):
        request = json.loads((EXAMPLES / request_name).read_text())
        with pytest.raises(isolated.ProvisioningRequired, match=f"assets install {needle}"):
            run_workbench(request, None if request_name == "ghostnet.json" else frame)
    assert not (home / "engines").exists() and not (home / "model-assets").exists()


def test_cli_reports_status_and_refuses_unprovisioned_work(home, tmp_path):
    out = tmp_path / "status.json"
    completed = _cli("engines", "status", "--out", str(out), home=home)
    assert completed.returncode == 0, completed.stderr
    engines = {row["engine"]: row for row in json.loads(out.read_text())["engines"]}
    assert sorted(engines) == sorted(isolated.ENGINES)
    assert engines["h2o"]["state"] == "not-installed" and engines["h2o"]["version"] == "3.46.0.12"
    completed = _cli("assets", "status", "ghostnet-imagenet", home=home)
    row = json.loads(completed.stdout)["assets"][0]
    assert row["state"] == "not-installed" and [f["bytes"] for f in row["files"]] == [20923722, 10472]
    completed = _cli(
        "workbench", str(EXAMPLES / "automl-h2o.json"), "--dataset", str(EXAMPLES / "series.csv"), home=home
    )
    assert completed.returncode == 2 and not completed.stdout.strip()
    error = json.loads(completed.stderr.strip().splitlines()[-1])
    assert error["error_type"] == "EngineUnavailable" and "engines install h2o" in error["error"]
    completed = _cli("engines", "install", home=home)
    assert completed.returncode == 2 and "exactly one engine" in completed.stderr
    completed = _cli("engines", "install", "tpot", "--python", str(tmp_path / "absent-python"), home=home)
    assert completed.returncode == 2 and "Base interpreter does not exist" in completed.stderr
    completed = _cli("assets", "install", "chronos-bolt-tiny", "--source", str(tmp_path), home=home)
    assert completed.returncode == 2 and "lacks config.json" in completed.stderr
    assert not (home / "model-assets" / "chronos-bolt-tiny").exists()


def test_runtime_that_differs_from_its_lock_is_refused_before_any_engine_code_runs(home):
    """A real virtual environment without the locked packages must never execute a worker."""
    root = isolated.runtime_dir("tpot")
    assert root.name == "tpot-" + isolated.lock_sha256("tpot")[:16]
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root)], check=True, timeout=300)
    manifest = root / isolated.MANIFEST_NAME
    manifest.write_text(json.dumps({"lock_sha256": "0" * 64}), encoding="utf-8")
    assert isolated.engine_status("tpot")["state"] == "stale"
    with pytest.raises(isolated.EngineUnavailable, match="stale"):
        isolated.run_engine("tpot", {}, {}, timeout=5)
    manifest.write_text(json.dumps({"lock_sha256": isolated.lock_sha256("tpot"), "python": "3.12"}), encoding="utf-8")
    assert isolated.engine_status("tpot")["state"] == "installed"
    expected = len(isolated.locked_distributions("tpot"))
    with pytest.raises(isolated.EngineUnavailable, match=rf"unlocked=0, missing={expected}, changed=0"):
        isolated.run_engine("tpot", {"seed": 1, "models": 2}, {"x_fit": np.zeros((2, 1))}, timeout=5)
    assert isolated.remove_engine("tpot")["state"] == "not-installed" and not root.exists()


def test_engine_request_validation_precedes_execution(home):
    with pytest.raises(ValueError, match="Unknown isolated engine"):
        isolated.engine_status("flaml")
    with pytest.raises(ValueError, match="Unknown isolated engine"):
        isolated.install_engine("../tpot")
    with pytest.raises(ValueError, match="Unknown model asset"):
        assets.install_asset("../ghostnet-imagenet")
    frame = pd.read_csv(EXAMPLES / "series.csv")
    base = json.loads((EXAMPLES / "automl-autogluon.json").read_text())["parameters"]
    for change in (
        {"engine": "flaml"},
        {"engine": ["tpot"]},
        {"features": ["x1", "y"]},
        {"features": []},
        {"train_rows": 288},
        {"validation_rows": 230},
        {"gap": 500},
        {"models": 1},
        {"models": 7},
        {"seed": -1},
        {"unknown": 1},
    ):
        with pytest.raises(ValueError):
            pretrained.external_automl(frame, {**base, **change})
    forecast = json.loads((EXAMPLES / "chronos-bolt.json").read_text())["parameters"]
    for change in ({"horizon": 0}, {"horizon": 65}, {"train_rows": 8}, {"target": "time"}, {"quantiles": [0.5]}):
        with pytest.raises(ValueError):
            pretrained.foundation_forecast(frame, {**forecast, **change})
    vision = json.loads((EXAMPLES / "ghostnet.json").read_text())["parameters"]
    for change in ({"top_k": 0}, {"top_k": 6}, {"image_files": []}, {"image_files": ["../cat.jpg"]}):
        with pytest.raises(ValueError):
            pretrained.pretrained_image_classification({**vision, **change})
    assert not (home / "engines").exists()
    assert {"external-automl", "foundation-forecast"} <= set(OPERATIONS)
    assert "pretrained-image-classification" in SPEC_OPERATIONS


def test_audit_hook_refuses_external_resolution_connections_and_wildcard_listeners(tmp_path):
    script = tmp_path / "probe.py"
    script.write_text(
        "import json, socket, sys\n"
        f"sys.path.insert(0, {str(isolated.WORKERS)!r})\n"
        "import guard\n"
        "guard.deny_external_network()\n"
        "out = {}\n"
        "def attempt(name, call):\n"
        "    try:\n"
        "        call(); out[name] = 'allowed'\n"
        "    except PermissionError as exc:\n"
        "        out[name] = 'refused: ' + str(exc)\n"
        "attempt('resolve', lambda: socket.getaddrinfo('example.com', 443))\n"
        "attempt('resolve_host', lambda: socket.gethostbyname('example.com'))\n"
        "attempt('connect', lambda: socket.socket().connect(('192.0.2.1', 9)))\n"
        "attempt('send', lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b'x', ('192.0.2.1', 9)))\n"
        "attempt('bind_any', lambda: socket.socket().bind(('0.0.0.0', 0)))\n"
        "attempt('bind_empty', lambda: socket.socket().bind(('', 0)))\n"
        "server = socket.socket()\n"
        "attempt('bind_loopback', lambda: server.bind(('127.0.0.1', 0)))\n"
        "server.listen(1)\n"
        "attempt('connect_loopback', lambda: socket.socket().connect(server.getsockname()))\n"
        "attempt('resolve_loopback', lambda: socket.getaddrinfo('127.0.0.1', 80))\n"
        "print(json.dumps(out))\n",
        encoding="utf-8",
    )
    completed = subprocess.run([sys.executable, "-s", "-B", str(script)], capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    out = json.loads(completed.stdout)
    for name in ("resolve", "resolve_host", "connect", "send", "bind_any", "bind_empty"):
        assert out[name].startswith("refused: "), (name, out[name])
    assert out["bind_loopback"] == out["connect_loopback"] == out["resolve_loopback"] == "allowed"


def test_worker_result_envelope_lists_only_site_packages_and_flags_foreign_code(tmp_path):
    foreign = tmp_path / "elsewhere"
    foreign.mkdir()
    (foreign / "planted.py").write_text("VALUE = 1\n", encoding="utf-8")
    # Passed with -c so that the probe itself is not a module file outside the runtime.
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(isolated.WORKERS)!r})\n"
        "import guard\n"
        "root = sys.argv[1]\n"
        "guard.write_result(root, {'engine': 'x', 'value': guard.finite_list([1, 2.5])})\n"
        "import json, os\n"
        "first = json.load(open(os.path.join(root, 'result.json')))\n"
        f"sys.path.insert(0, {str(foreign)!r})\n"
        "import planted\n"
        "print(json.dumps({'first': first['foreign_modules'], 'second': guard.foreign_modules(),\n"
        "                  'names': [n for n, _ in first['distributions']], 'value': first['value']}))\n"
        "try:\n"
        "    guard.finite_list([float('nan')])\n"
        "except ValueError:\n"
        "    print('nonfinite refused')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-s", "-B", "-c", code, str(tmp_path)], capture_output=True, text=True, timeout=120
    )
    assert completed.returncode == 0, completed.stderr
    lines = completed.stdout.strip().splitlines()
    report = json.loads(lines[0])
    assert report["first"] == 0 and report["second"] == 1 and report["value"] == [1.0, 2.5]
    assert "numpy" in report["names"] and len(report["names"]) == len(set(report["names"]))
    assert lines[1] == "nonfinite refused" and not (tmp_path / "result.json.partial").exists()


def _tree_script(tmp_path, *, watch):
    """A child that starts a long-lived grandchild and records both process identifiers."""
    script = tmp_path / "tree.py"
    script.write_text(
        "import os, subprocess, sys, time\n"
        f"sys.path.insert(0, {str(isolated.WORKERS)!r})\n"
        "import guard\n"
        "root = sys.argv[1]\n"
        + ("guard.watch_parent(root)\n" if watch else "")
        + "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'],\n"
        "                         stdin=subprocess.DEVNULL)\n"
        "open(os.path.join(root, 'pids.partial'), 'w').write('%d %d' % (os.getpid(), child.pid))\n"
        "os.replace(os.path.join(root, 'pids.partial'), os.path.join(root, 'pids'))\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    return script


def test_budget_expiry_terminates_the_whole_process_tree(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    script = _tree_script(tmp_path, watch=False)
    started = time.monotonic()
    with pytest.raises(isolated.EngineFailure, match="wall-clock budget"):
        isolated._run(
            [sys.executable, "-s", "-B", script, work],
            cwd=work,
            env=isolated._base_environment(work),
            timeout=6,
            log=work / "log.txt",
        )
    assert time.monotonic() - started < 60
    pids = [int(value) for value in (work / "pids").read_text().split()]
    assert len(pids) == 2 and _wait_dead(pids) == []


def test_death_of_the_application_stops_the_engine_tree(tmp_path):
    """Kill a real intermediate parent mid-run: the worker and its child must not survive it."""
    work = tmp_path / "work"
    work.mkdir()
    script = _tree_script(tmp_path, watch=True)
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from energybaseline import isolated\n"
        "work = sys.argv[2]\n"
        "isolated._run([sys.executable, '-s', '-B', sys.argv[1], work], cwd=work,\n"
        "              env=isolated._base_environment(work), timeout=110, log=work + '/log.txt', lifeline=True)\n",
        encoding="utf-8",
    )
    process = subprocess.Popen([sys.executable, str(parent), str(script), str(work)], stdin=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 60
        while not (work / "pids").is_file() and time.monotonic() < deadline:
            time.sleep(0.1)
        pids = [int(value) for value in (work / "pids").read_text().split()]
        assert all(_alive(pid) for pid in pids)
    finally:
        process.kill()
        process.wait(timeout=30)
    assert _wait_dead(pids) == []
    if sys.platform != "win32":
        # POSIX: a detached helper removes the private workspace after the group is killed.
        deadline = time.monotonic() + 20
        while work.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not work.exists()


def test_child_environment_is_rebuilt_without_inherited_secrets(tmp_path, monkeypatch):
    for key in ("HTTPS_PROXY", "PYTHONPATH", "AWS_SECRET_ACCESS_KEY", "HF_TOKEN", "DATABASE_URL"):
        monkeypatch.setenv(key, "sentinel-value")
    env = isolated._base_environment(tmp_path)
    assert "sentinel-value" not in env.values()
    assert not {"HTTPS_PROXY", "PYTHONPATH", "AWS_SECRET_ACCESS_KEY", "HF_TOKEN", "DATABASE_URL"} & set(env)
    assert env["HF_HUB_OFFLINE"] == env["TRANSFORMERS_OFFLINE"] == env["OMP_NUM_THREADS"] == "1"
    assert env["HOME"] == env["TMP"] == env["HF_HOME"] == str(tmp_path) and env["PYTHONHASHSEED"] == "0"
    completed = subprocess.run(
        [sys.executable, "-s", "-c", "import os, json; print(json.dumps(dict(os.environ)))"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0 and "sentinel-value" not in completed.stdout


def test_stale_private_workspaces_are_swept_and_live_ones_kept():
    first = isolated._workspace(isolated.RUN_PREFIX)
    stale = first.parent / (isolated.RUN_PREFIX + "abandoned")
    stale.mkdir()
    (stale / "x_fit.npy").write_bytes(b"rows")
    old = time.time() - isolated.MAX_RUN_SECONDS - 301
    os.utime(stale, (old, old))
    recent = first.parent / (isolated.INSTALL_PREFIX + "recent")
    recent.mkdir()
    os.utime(recent, (old, old))
    try:
        second = isolated._workspace(isolated.RUN_PREFIX)
        assert not stale.exists() and first.is_dir() and second.is_dir() and recent.is_dir()
        if sys.platform != "win32":
            assert first.stat().st_mode & 0o077 == 0
    finally:
        for path in (first, second, recent):
            isolated._remove(path)
    with pytest.raises(ValueError, match="timeout"):
        isolated.run_engine("tpot", {}, {}, timeout=isolated.MAX_RUN_SECONDS + 1)


def test_asset_installation_accepts_only_the_approved_bytes(home, tmp_path):
    asset = assets.ASSETS["ghostnet-imagenet"]
    for item in (i for a in assets.ASSETS.values() for i in a.files):
        assert item.url.startswith("https://") and re.fullmatch(r"[0-9a-f]{64}", item.sha256) and item.size > 0
        # Sources name an immutable revision, never a moving branch.
        assert re.search(r"/[0-9a-f]{40}/", item.url)
    source = tmp_path / "source"
    source.mkdir()
    for item in asset.files:
        (source / item.name).write_bytes(b"\0" * 64)
    with pytest.raises(isolated.ProvisioningRequired, match="approved size and SHA-256"):
        assets.install_asset(asset.name, source)
    root = home / "model-assets" / asset.name
    assert not root.exists() and list((home / "model-assets").iterdir()) == []
    assert assets.asset_status(asset.name)["state"] == "not-installed"
    # Right size, wrong content: only the hash can tell.
    labels = asset.files[1]
    (source / labels.name).write_bytes(b"a" * labels.size)
    assert not assets._matches(source / labels.name, labels)
    root.mkdir(parents=True)
    (root / labels.name).write_bytes(b"a" * labels.size)
    assert assets.asset_status(asset.name)["state"] == "invalid"
    with pytest.raises(isolated.ProvisioningRequired, match="assets install ghostnet-imagenet"):
        assets.verified_asset(asset.name)
    assert assets.remove_asset(asset.name)["state"] == "not-installed" and not root.exists()


def test_vendored_ghostnet_is_the_upstream_definition_with_one_documented_correction():
    from energybaseline.workbench.vendor import ghostnet

    source = Path(ghostnet.__file__).read_bytes()
    assert b"\r" not in source and hashlib.sha256(source).hexdigest() == pretrained.GHOSTNET_SOURCE_SHA256
    lines = source.decode("utf-8").split("\n")
    assert lines[0].startswith("# Modified by EnergyBaseline") and lines[1].startswith("#")
    upstream = "\n".join(lines[2:]).replace("__all__ = ['ghostnet']", "__all__ = ['ghost_net']")
    assert hashlib.sha256(upstream.encode("utf-8")).hexdigest() == pretrained.GHOSTNET_UPSTREAM_SHA256
    assert "Apache License" in (ROOT / "docs/licenses/GhostNet-Apache-2.0.txt").read_text(encoding="utf-8")


def test_ghostnet_architecture_and_imagenet_preprocessing_match_the_publication():
    import torch

    from energybaseline.workbench.vendor import ghostnet

    torch.manual_seed(0)
    model = ghostnet.ghostnet(num_classes=1000, width=1.0, dropout=0.2).eval()
    # GhostNet 1.0x: 5.2 million parameters (Han et al., CVPR 2020, Table 7).
    assert sum(p.numel() for p in model.parameters()) == 5_182_508
    with torch.no_grad():
        logits = model(torch.zeros(2, 3, 224, 224))
    assert logits.shape == (2, 1000) and bool(torch.isfinite(logits).all())

    colour = np.array([30, 120, 240], dtype=np.uint8)  # blue, green, red
    tensor = pretrained._imagenet_tensor(np.broadcast_to(colour, (300, 451, 3)).copy())
    assert tensor.shape == (3, 224, 224) and tensor.dtype == np.float32
    expected = (colour[::-1] / 255 - np.array([0.485, 0.456, 0.406])) / np.array([0.229, 0.224, 0.225])
    assert np.allclose(tensor.reshape(3, -1).T, expected, atol=1e-6)
    # The crop is centred: a bright centre pixel block of a tall image stays in the middle.
    tall = np.zeros((512, 256, 3), dtype=np.uint8)
    tall[224:288, 96:160] = 255
    centred = pretrained._imagenet_tensor(tall)[0]
    rows, columns = np.where(centred > 0)
    assert abs(rows.mean() - 111.5) < 1 and abs(columns.mean() - 111.5) < 1


def test_examples_are_small_documented_and_bound_to_registered_operations():
    manifest = json.loads((EXAMPLES / "manifest.json").read_text())
    assert len(manifest) == 6
    for job in manifest:
        request = json.loads((EXAMPLES / job["request"]).read_text())
        assert request["version"] == 1 and request["operation"] in {**OPERATIONS, **SPEC_OPERATIONS}
        assert job.get("engine") is None or job["engine"] in isolated.ENGINES
        assert job.get("asset") is None or job["asset"] in assets.ASSETS
    frame = pd.read_csv(EXAMPLES / "series.csv")
    assert frame.shape == (288, 6) and np.isfinite(frame.drop(columns="time").to_numpy()).all()
    digests = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (EXAMPLES / "images").iterdir()}
    assert digests == {
        "cat.jpg": "4d0169c5aee1e7aa69698d7465766522f455c85a521f484bb29876453e8b3b1a",
        "espresso.jpg": "52daa39473f2bb8817f03f5ccafa7bb874cf2b200ef74dc1cdf513fd1c06d594",
    }
