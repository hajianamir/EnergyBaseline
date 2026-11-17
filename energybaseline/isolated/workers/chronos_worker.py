"""Zero-shot Chronos-Bolt forecasting from verified local weights.

Runs inside the Chronos isolated runtime; it never imports the application. Usage:
``python chronos_worker.py <private-directory>``. No fine-tuning and no downloads occur:
Hugging Face offline mode is set by the parent and external sockets are refused here.
"""

import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import guard  # noqa: E402


def main():
    root = os.path.abspath(sys.argv[1])
    warnings.filterwarnings("ignore")
    guard.watch_parent(root)
    guard.deny_external_network()
    request = guard.read_request(root)
    import torch

    import chronos
    from chronos import BaseChronosPipeline

    torch.set_num_threads(1)
    torch.manual_seed(0)
    pipeline = BaseChronosPipeline.from_pretrained(
        request["paths"]["model"], device_map="cpu", torch_dtype=torch.float32, local_files_only=True
    )
    context = torch.tensor(guard.load(root, "context"), dtype=torch.float32)
    with torch.no_grad():
        quantiles, mean = pipeline.predict_quantiles(
            context, prediction_length=request["horizon"], quantile_levels=request["quantile_levels"]
        )
    model = pipeline.inner_model if hasattr(pipeline, "inner_model") else pipeline.model
    guard.write_result(
        root,
        {
            "engine": "chronos",
            "engine_version": chronos.__version__,
            "pipeline": type(pipeline).__name__,
            "parameters": int(sum(p.numel() for p in model.parameters())),
            "model_context_length": int(model.chronos_config.context_length),
            "torch_version": torch.__version__.split("+")[0],
            "quantiles": [guard.finite_list(row) for row in quantiles[0].tolist()],
        },
    )


if __name__ == "__main__":
    main()
