"""Public analysis entry point with explicit, versioned domain policy.

The low-level historical core entry point remains a deprecation facade for old
energy projects. This entry point never infers the domain from column names.
"""

from __future__ import annotations

from .core.pipeline import run_analysis


def analyze(df, time_col, targets, config=None, *, domain_pack="neutral", **kwargs):
    """Analyze temporal regression data; energy rules require explicit opt-in.

    Return the existing analysis/bundle pair. Accepted VSG and locked TCP must be
    supplied to enable the separately gated inferential workflow.
    """
    from .contracts import AnalysisRequest

    request = AnalysisRequest(time_col, targets, domain_pack, {} if config is None else config)
    missing = {request.time_col, *request.targets} - set(df.columns)
    if missing:
        raise ValueError("Requested columns are absent: " + ", ".join(sorted(missing)))
    cfg = {**request.config, "domain_pack": request.domain_pack}
    return run_analysis(df, request.time_col, list(request.targets), cfg, **kwargs)


def analyze_request(df, request, **kwargs):
    """Execute a versioned AnalysisRequest and return a typed AnalysisResponse."""
    from .contracts import AnalysisRequest, AnalysisResponse

    if isinstance(request, dict):
        request = AnalysisRequest.from_dict(request)
    if not isinstance(request, AnalysisRequest):
        raise TypeError("request must be an AnalysisRequest or its versioned dictionary.")
    missing = {request.time_col, *request.targets} - set(df.columns)
    if missing:
        raise ValueError("Requested columns are absent: " + ", ".join(sorted(missing)))
    run, bundles = analyze(
        df, request.time_col, list(request.targets), request.config, domain_pack=request.domain_pack, **kwargs
    )
    return AnalysisResponse(run, bundles)
