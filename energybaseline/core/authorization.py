"""Input-bound inference authorization; configuration flags are not credentials."""

from __future__ import annotations

import hashlib
import json

from .ingest import sha256_dataframe
from .vsg import ComplianceGateError


def assert_bound_vsg(frame, time_col, targets, vsg):
    vsg.assert_accepted()
    fingerprint = getattr(vsg, "input_frame_sha256", None)
    if fingerprint is None or sha256_dataframe(frame) != fingerprint:
        raise ComplianceGateError(
            "The accepted VSG is not bound to these input values, columns, dtypes and timestamps. Rebuild and accept the gates."
        )
    if vsg.time_col != time_col or not set(targets).issubset(vsg.targets):
        raise ComplianceGateError("The accepted VSG does not authorize these targets or timestamp column.")


def assert_bound_inference(frame, time_col, targets, vsg, tcp):
    assert_bound_vsg(frame, time_col, targets, vsg)
    assert_locked_inference(tcp)
    if tcp.vsg.vsg_sha256 != vsg.vsg_sha256:
        raise ComplianceGateError("The TCP is not bound to the supplied VSG.")


def assert_locked_inference(tcp):
    """Authorize diagnostics on new data without reusing the calibration input.

    UQ, drift and causal reporting consume different observations by design, but
    still require the accepted, unchanged VSG/TCP preregistration before compute.
    """
    tcp.assert_accepted()
    if not tcp.is_locked or not tcp.preregistration_sha256:
        raise ComplianceGateError("Inference requires a preregistration-locked TCP.")
    vsg = tcp.vsg
    bundle = {"dataset_sha256": vsg.dataset_sha256, "tcp_spec": tcp.canonical_spec_dict(), "vsg_sha256": vsg.vsg_sha256}
    expected = hashlib.sha256(
        json.dumps(bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    if expected != tcp.preregistration_sha256:
        raise ComplianceGateError(
            "The preregistered VSG/TCP specification has changed. Revalidate and explicitly lock an amendment."
        )


def authorize_legacy_executor(frame, vsg, tcp):
    """Freeze an explicitly accepted legacy panel before any primitive executes.

    Historical direct runners accepted TCP without a separate lock call. Keep
    that call shape, but never compute in an unlocked state, and reject mutation
    since acceptance. New orchestration requires an explicit lock up front.
    """
    assert_bound_vsg(frame, vsg.time_col, vsg.targets, vsg)
    tcp.assert_accepted()
    if not tcp.is_locked:
        import warnings

        accepted = getattr(tcp, "_accepted_spec_sha256", None)
        if accepted is None or accepted != tcp.tcp_sha256:
            raise ComplianceGateError("TCP changed since acceptance. Re-accept and explicitly lock the specification.")
        warnings.warn(
            "Direct test runners now freeze accepted TCP before execution. Call lock_preregistration explicitly; amendments require unlock_for_amendment.",
            DeprecationWarning,
            stacklevel=2,
        )
        tcp.lock_preregistration()
    assert_bound_inference(frame, vsg.time_col, vsg.targets, vsg, tcp)
