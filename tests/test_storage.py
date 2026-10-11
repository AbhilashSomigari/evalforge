import json
import logging
import time

import pytest

from evalforge_agents.models import EvalRun, RunSummary
from evalforge_agents.storage import find_latest_on_branch, load_run, save_run, try_load_run


def _run(**overrides) -> EvalRun:
    defaults = {"suite_name": "s", "agent_name": "a", "results": [], "summary": RunSummary()}
    defaults.update(overrides)
    return EvalRun(**defaults)


def test_round_trip(tmp_path):
    path = tmp_path / "nested" / "run.json"
    saved = save_run(_run(), path)
    assert saved == path
    loaded = load_run(path)
    assert loaded.suite_name == "s"
    assert loaded.schema_version == 1


def test_load_run_rejects_wrong_schema_version(tmp_path):
    path = tmp_path / "run.json"
    data = json.loads(_run().model_dump_json())
    data["schema_version"] = 0
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="schema_version"):
        load_run(path)


def test_find_latest_on_branch_returns_none_for_empty_dir(tmp_path):
    assert find_latest_on_branch(tmp_path, "s", "main") is None


def test_find_latest_on_branch_filters_by_suite_and_branch(tmp_path):
    save_run(_run(suite_name="s", git_branch="main"), tmp_path / "a.json")
    save_run(_run(suite_name="other-suite", git_branch="main"), tmp_path / "b.json")
    save_run(_run(suite_name="s", git_branch="feature-x"), tmp_path / "c.json")
    found = find_latest_on_branch(tmp_path, "s", "main")
    assert found is not None
    assert found.suite_name == "s"
    assert found.git_branch == "main"


def test_find_latest_on_branch_picks_most_recent(tmp_path):
    older = _run(suite_name="s", git_branch="main")
    save_run(older, tmp_path / "older.json")
    time.sleep(0.01)
    newer = _run(suite_name="s", git_branch="main")
    save_run(newer, tmp_path / "newer.json")
    found = find_latest_on_branch(tmp_path, "s", "main")
    assert found.id == newer.id


def test_find_latest_on_branch_skips_unreadable_files(tmp_path):
    save_run(_run(suite_name="s", git_branch="main"), tmp_path / "a.json")
    (tmp_path / "corrupt.json").write_text("not json")
    found = find_latest_on_branch(tmp_path, "s", "main")
    assert found is not None


@pytest.fixture
def _evalforge_logs_propagate(monkeypatch):
    # configure_logging() (called by CLI tests earlier in the session) turns
    # propagation off, which would hide these records from caplog.
    monkeypatch.setattr(logging.getLogger("evalforge_agents"), "propagate", True)


def test_try_load_run_logs_why_a_file_was_skipped(tmp_path, caplog, _evalforge_logs_propagate):
    path = tmp_path / "old.json"
    data = json.loads(_run().model_dump_json())
    data["schema_version"] = 0
    path.write_text(json.dumps(data))
    with caplog.at_level(logging.WARNING, logger="evalforge_agents"):
        assert try_load_run(path) is None
    [record] = caplog.records
    assert record.message == "skipping unreadable run file"
    assert record.extra_fields["path"] == str(path)
    assert "schema_version" in record.extra_fields["error"]


def test_find_latest_on_branch_warns_instead_of_silently_skipping(
    tmp_path, caplog, _evalforge_logs_propagate
):
    save_run(_run(suite_name="s", git_branch="main"), tmp_path / "ok.json")
    (tmp_path / "list.json").write_text("[]")  # valid JSON, but not a run object
    with caplog.at_level(logging.WARNING, logger="evalforge_agents"):
        found = find_latest_on_branch(tmp_path, "s", "main")
    assert found is not None
    assert [r.extra_fields["path"] for r in caplog.records] == [str(tmp_path / "list.json")]
