import json
import time

import pytest

from evalforge.models import EvalRun, RunSummary
from evalforge.storage import find_latest_on_branch, load_run, save_run


def _run(**overrides) -> EvalRun:
    defaults = dict(suite_name="s", agent_name="a", results=[], summary=RunSummary())
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
