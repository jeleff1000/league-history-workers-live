from pathlib import Path

import yaml


WORKFLOW = Path(__file__).parents[1] / ".github/workflows/mfl_register_batch_campaign.yml"


def test_chunked_campaign_finalizes_canonical_cache_in_combine():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "merge_mfl_saved_chunk.py" in text
    assert "Save canonical successor index" in text


def test_next_wave_uses_canonical_successor_index():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "CANONICAL_INDEX_KEY" in text
    assert 'PRIOR_INDEX_KEY="$CANONICAL_INDEX_KEY"' in text


def test_finalizer_proof_is_required_before_canonical_save():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "canonical_append_proof.json" in text
    assert "fail-on-cache-miss: true" in text


def test_fetch_only_continuation_reads_the_chunk_index():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert '"$STORAGE_MODE" = fetch_only' in text
    assert "ROOT=index_state" in text


def test_intermediate_batch_receipts_expire_after_the_same_day_combine_window():
    """Batch receipts are combine-job transport, while the chunk is the durable output."""

    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["ingest"]["steps"]
    uploads = {step["name"]: step["with"] for step in steps if "with" in step}

    assert uploads["Upload batch receipts"]["retention-days"] == 1


def test_combine_removes_only_batch_transport_after_publishing_the_durable_chunk():
    """A completed combine must reclaim its transient matrix artifacts."""

    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["combine"]["steps"]
    steps_by_name = {step["name"]: step for step in steps}
    purge = steps_by_name["Purge consumed batch transport artifacts"]

    assert "mfl-register-batch-" in purge["run"]
    assert "actions/artifacts/$artifact_id" in purge["run"]
    assert "--method DELETE" in purge["run"]
