from __future__ import annotations

import pytest

from archive_mfl_campaign_runs import (
    CampaignArchiveError,
    winscp_command_args,
    parse_remote_archive_output,
    plan_completed_chunk_run,
    recover_completed_chunk_run,
    run_artifacts_from_pages,
    verify_remote_chunk_archive,
)


def _run(*, conclusion: str = "success") -> dict[str, object]:
    return {"id": 456, "status": "completed", "conclusion": conclusion}


def _chunk(*, digest: str = "a" * 64) -> dict[str, object]:
    return {
        "id": 123,
        "name": "mfl-register-chunk-456",
        "digest": f"sha256:{digest}",
        "size_in_bytes": 987,
        "expired": False,
    }


def _supporting_artifacts() -> list[dict[str, object]]:
    return [
        {"id": 1, "name": "mfl-register-batch-0", "expired": False},
        {"id": 2, "name": "mfl-accepted-keys-456", "expired": False},
        {"id": 3, "name": "mfl-batch-plan-456", "expired": False},
        {"id": 4, "name": "mfl-wave-manifest-456", "expired": False},
        {"id": 5, "name": "mfl-pending-manifest-456", "expired": False},
    ]


def test_completed_chunk_run_plan_requires_one_unexpired_durable_chunk() -> None:
    plan = plan_completed_chunk_run(
        _run(),
        [_chunk(), *_supporting_artifacts()],
        remote_root="/home/kmffl/actions_artifacts/_github_archives",
    )

    assert plan.run_id == 456
    assert plan.artifact_id == 123
    assert plan.expected_sha256 == "a" * 64
    assert plan.remote_archive == "/home/kmffl/actions_artifacts/_github_archives/456/mfl-register-chunk-456-123.zip"


def test_completed_chunk_run_plan_refuses_unknown_or_incomplete_artifacts() -> None:
    with pytest.raises(CampaignArchiveError, match="unknown artifact"):
        plan_completed_chunk_run(
            _run(),
            [_chunk(), {"id": 9, "name": "not-an-mfl-artifact", "expired": False}],
            remote_root="/home/kmffl/actions_artifacts/_github_archives",
        )

    with pytest.raises(CampaignArchiveError, match="completed successful"):
        plan_completed_chunk_run(
            _run(conclusion="failure"),
            [_chunk(), *_supporting_artifacts()],
            remote_root="/home/kmffl/actions_artifacts/_github_archives",
        )


def test_remote_chunk_verification_requires_github_digest_and_complete_contract() -> None:
    members = {
        "cache_append_proof.json",
        "mfl_register_all_runs.json",
        "mfl_register_chunk.duckdb",
        "mfl_research_overlay.duckdb",
        "mfl_research_overlay_proof.json",
    }

    verify_remote_chunk_archive(expected_sha256="b" * 64, actual_sha256="b" * 64, members=members)

    with pytest.raises(CampaignArchiveError, match="SHA-256 mismatch"):
        verify_remote_chunk_archive(expected_sha256="b" * 64, actual_sha256="c" * 64, members=members)

    with pytest.raises(CampaignArchiveError, match="required files"):
        verify_remote_chunk_archive(
            expected_sha256="b" * 64,
            actual_sha256="b" * 64,
            members=members - {"mfl_register_chunk.duckdb"},
        )


def test_recovery_dry_run_has_no_remote_or_github_side_effects() -> None:
    plan = plan_completed_chunk_run(
        _run(),
        [_chunk(), *_supporting_artifacts()],
        remote_root="/home/kmffl/actions_artifacts/_github_archives",
    )

    result = recover_completed_chunk_run(
        plan,
        apply=False,
        signed_download_url=lambda _artifact_id: (_ for _ in ()).throw(AssertionError("dry run fetched a URL")),
        archive_remote=lambda _plan, _url: (_ for _ in ()).throw(AssertionError("dry run wrote remotely")),
        record_remote=lambda _plan: (_ for _ in ()).throw(AssertionError("dry run wrote a receipt")),
        delete_run=lambda _run_id: (_ for _ in ()).throw(AssertionError("dry run deleted a run")),
    )

    assert result == {"status": "dry_run", "run_id": 456, "artifact_id": 123}


def test_recovery_refuses_to_delete_when_remote_verification_fails() -> None:
    plan = plan_completed_chunk_run(
        _run(),
        [_chunk(), *_supporting_artifacts()],
        remote_root="/home/kmffl/actions_artifacts/_github_archives",
    )
    deleted: list[int] = []

    with pytest.raises(CampaignArchiveError, match="SHA-256 mismatch"):
        recover_completed_chunk_run(
            plan,
            apply=True,
            signed_download_url=lambda _artifact_id: "https://signed.example.invalid/archive",
            archive_remote=lambda _plan, _url: ("b" * 64, {"mfl_register_chunk.duckdb"}),
            record_remote=lambda _plan: (_ for _ in ()).throw(AssertionError("invalid archive wrote a receipt")),
            delete_run=deleted.append,
        )

    assert deleted == []


def test_recovery_records_remote_provenance_before_requesting_run_deletion() -> None:
    plan = plan_completed_chunk_run(
        _run(),
        [_chunk(), *_supporting_artifacts()],
        remote_root="/home/kmffl/actions_artifacts/_github_archives",
    )
    events: list[str] = []
    members = {
        "cache_append_proof.json",
        "mfl_register_all_runs.json",
        "mfl_register_chunk.duckdb",
        "mfl_research_overlay.duckdb",
        "mfl_research_overlay_proof.json",
    }

    recover_completed_chunk_run(
        plan,
        apply=True,
        signed_download_url=lambda _artifact_id: "https://signed.example.invalid/archive",
        archive_remote=lambda _plan, _url: ("a" * 64, members),
        record_remote=lambda _plan: events.append("receipt"),
        delete_run=lambda _run_id: events.append("delete"),
    )

    assert events == ["receipt", "delete"]


def test_run_artifacts_reads_every_github_page_before_planning() -> None:
    pages_seen: list[int] = []

    def page(page_number: int) -> dict[str, object]:
        pages_seen.append(page_number)
        if page_number == 1:
            return {"total_count": 101, "artifacts": [{"id": 1}] * 100}
        return {"total_count": 101, "artifacts": [{"id": 2}]}

    artifacts = run_artifacts_from_pages(page)

    assert len(artifacts) == 101
    assert pages_seen == [1, 2]


def test_remote_archive_output_requires_a_single_hash_and_safe_required_members() -> None:
    output = "\n".join(
        [
            "a" * 64 + "  /home/kmffl/archive.zip",
            "cache_append_proof.json",
            "mfl_register_all_runs.json",
            "mfl_register_chunk.duckdb",
            "mfl_research_overlay.duckdb",
            "mfl_research_overlay_proof.json",
        ]
    )

    digest, members = parse_remote_archive_output(output)

    assert digest == "a" * 64
    assert set(members) == {
        "cache_append_proof.json",
        "mfl_register_all_runs.json",
        "mfl_register_chunk.duckdb",
        "mfl_research_overlay.duckdb",
        "mfl_research_overlay_proof.json",
    }


def test_winscp_command_uses_the_saved_site_without_literal_quotes() -> None:
    command = winscp_command_args(
        winscp="WinSCP.com",
        sftp_site="kmffl@56.lw.itsby.design",
        remote_command="echo ok",
    )

    assert command == ["WinSCP.com", "/command", "open kmffl@56.lw.itsby.design", "call echo ok", "exit"]
