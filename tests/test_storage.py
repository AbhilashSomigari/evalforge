import json

import pytest

from evalforge.models import EvalRun, RunSummary
from evalforge.storage import load_run, save_run


def _run() -> EvalRun:
    return EvalRun(suite_name="s", agent_name="a", results=[], summary=RunSummary())


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
