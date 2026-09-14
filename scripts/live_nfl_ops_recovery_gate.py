#!/usr/bin/env python3
"""Allow the 03:47 ET live-NFL recovery only when its exact scope is unfinished."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


RECOVERY_SCHEDULES = frozenset(
    {
        "47 6 * 9,10 *",  # 03:47 EDT
        "47 7 * 11,12 *",  # 03:47 EST
        "47 7 * 1,2 *",  # 03:47 EST, 2027 postseason
    }
)
SCOPE_FIELDS = ("year", "week", "season_type", "game_date")


class RecoveryGateError(ValueError):
    """The recovery gate cannot safely establish whether the scope completed."""


class RecoveryDecision:
    """The observable decision used by both the CLI and its tests."""

    def __init__(self, should_refresh: bool, reason: str) -> None:
        self.should_refresh = should_refresh
        self.reason = reason


def _scope_from(document: Mapping[str, Any], *, completed: bool) -> dict[str, Any]:
    if completed:
        if document.get("status") != "completed":
            raise RecoveryGateError("completion receipt is not completed")
        invalid_message = "completion receipt does not contain a completed scope"
    else:
        if document.get("status") != "ready":
            raise RecoveryGateError("refresh scope is not ready")
        invalid_message = "refresh scope does not contain a ready scope"

    scope = document.get("scope")
    if not isinstance(scope, Mapping):
        raise RecoveryGateError(invalid_message)
    if any(field not in scope or scope[field] in (None, "") for field in SCOPE_FIELDS):
        raise RecoveryGateError(invalid_message)
    return {field: scope[field] for field in SCOPE_FIELDS}


def decide_refresh(
    *,
    event: str,
    event_schedule: str | None,
    scope_document: Mapping[str, Any],
    completion_receipt: Mapping[str, Any] | None,
) -> RecoveryDecision:
    """Return whether this invocation may build and promote the ready scope."""
    if event.strip() != "schedule" or (event_schedule or "").strip() not in RECOVERY_SCHEDULES:
        return RecoveryDecision(True, "not-a-recovery-schedule")

    target_scope = _scope_from(scope_document, completed=False)
    if completion_receipt is None:
        return RecoveryDecision(True, "missing-completion-receipt")

    completed_scope = _scope_from(completion_receipt, completed=True)
    if completed_scope == target_scope:
        return RecoveryDecision(False, "already-completed")
    return RecoveryDecision(True, "completion-receipt-is-stale")


def _load_json(path: Path, *, required: bool) -> Mapping[str, Any] | None:
    if not path.is_file():
        if required:
            raise RecoveryGateError(f"required scope receipt does not exist: {path}")
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RecoveryGateError(f"invalid JSON in {path}") from exc
    if not isinstance(value, Mapping):
        raise RecoveryGateError(f"JSON receipt must be an object: {path}")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True, help="GitHub event name")
    parser.add_argument("--schedule", default="", help="GitHub schedule cron expression")
    parser.add_argument("--scope", type=Path, required=True, help="ready live-NFL scope JSON")
    parser.add_argument("--receipt", type=Path, required=True, help="optional prior completion receipt")
    parser.add_argument("--github-output", type=Path, help="optional GitHub step output file")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    scope_document = _load_json(args.scope, required=True)
    assert scope_document is not None
    decision = decide_refresh(
        event=args.event,
        event_schedule=args.schedule,
        scope_document=scope_document,
        completion_receipt=_load_json(args.receipt, required=False),
    )
    lines = (
        f"should_refresh={'true' if decision.should_refresh else 'false'}",
        f"reason={decision.reason}",
    )
    if args.github_output:
        args.github_output.parent.mkdir(parents=True, exist_ok=True)
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI
    raise SystemExit(main())
