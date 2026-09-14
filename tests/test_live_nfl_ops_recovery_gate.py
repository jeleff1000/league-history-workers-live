import importlib.util

import pytest


ROOT = __file__.replace("\\", "/").rsplit("/tests/", 1)[0]
GATE = f"{ROOT}/scripts/live_nfl_ops_recovery_gate.py"


READY_SCOPE = {
    "status": "ready",
    "scope": {
        "year": 2026,
        "week": 1,
        "season_type": "REG",
        "game_date": "2026-09-14",
    },
}


def _module():
    spec = importlib.util.spec_from_file_location("live_nfl_ops_recovery_gate", GATE)
    assert spec is not None and spec.loader is not None, "the retry needs a testable completion gate"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _completed_receipt(scope: dict) -> dict:
    return {"status": "completed", "scope": scope["scope"], "run_id": "123"}


def test_recovery_schedule_skips_an_exact_completed_refresh() -> None:
    """A valid receipt prevents the 03:47 retry from rebuilding or writing Fly."""
    module = _module()

    decision = module.decide_refresh(
        event="schedule",
        event_schedule="47 6 * 9,10 *",
        scope_document=READY_SCOPE,
        completion_receipt=_completed_receipt(READY_SCOPE),
    )

    assert decision.should_refresh is False
    assert decision.reason == "already-completed"


def test_recovery_schedule_runs_when_the_primary_left_no_completion_receipt() -> None:
    """A failed or not-yet-final 02:17 attempt receives one recovery attempt."""
    module = _module()

    decision = module.decide_refresh(
        event="schedule",
        event_schedule="47 6 * 9,10 *",
        scope_document=READY_SCOPE,
        completion_receipt=None,
    )

    assert decision.should_refresh is True
    assert decision.reason == "missing-completion-receipt"


def test_recovery_schedule_runs_when_the_receipt_is_for_a_different_game_date() -> None:
    """Yesterday's completed slate cannot suppress tonight's recovery attempt."""
    module = _module()
    stale_scope = {
        **READY_SCOPE,
        "scope": {**READY_SCOPE["scope"], "game_date": "2026-09-13"},
    }

    decision = module.decide_refresh(
        event="schedule",
        event_schedule="47 6 * 9,10 *",
        scope_document=READY_SCOPE,
        completion_receipt=_completed_receipt(stale_scope),
    )

    assert decision.should_refresh is True
    assert decision.reason == "completion-receipt-is-stale"


def test_recovery_schedule_rejects_an_untrustworthy_completion_receipt() -> None:
    """A damaged receipt cannot be treated as proof that Fly was refreshed."""
    module = _module()

    with pytest.raises(module.RecoveryGateError, match="completed scope"):
        module.decide_refresh(
            event="schedule",
            event_schedule="47 6 * 9,10 *",
            scope_document=READY_SCOPE,
            completion_receipt={"status": "completed"},
        )


def test_primary_schedule_never_suppresses_its_own_refresh() -> None:
    module = _module()

    decision = module.decide_refresh(
        event="schedule",
        event_schedule="17 6 * 9,10 *",
        scope_document=READY_SCOPE,
        completion_receipt=_completed_receipt(READY_SCOPE),
    )

    assert decision.should_refresh is True
    assert decision.reason == "not-a-recovery-schedule"
