from __future__ import annotations

import json
import logging

import pytest

from evalforge.logging_utils import JsonFormatter, get_logger, log, set_request_id


@pytest.fixture(autouse=True)
def _reset_evalforge_logger():
    # Other tests (e.g. the CLI ones) call configure_logging(), which sets
    # propagate=False on the "evalforge" logger so it doesn't double-log
    # through root in real use. That's correct for production but would
    # silently stop caplog (attached at the root logger) from seeing anything
    # logged here, depending on what ran earlier in the same test session.
    logger = logging.getLogger("evalforge")
    saved_handlers, saved_propagate, saved_level = logger.handlers[:], logger.propagate, logger.level
    logger.handlers = []
    logger.propagate = True
    yield
    logger.handlers = saved_handlers
    logger.propagate = saved_propagate
    logger.level = saved_level


def _make_record(logger_name: str = "evalforge.test") -> logging.LogRecord:
    return logging.LogRecord(
        name=logger_name,
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )


def test_get_logger_does_not_double_prefix_module_dunder_name():
    # __name__ inside this package is already "evalforge.xxx" (e.g. "evalforge.api"),
    # so get_logger(__name__) must not become "evalforge.evalforge.api".
    assert get_logger("evalforge.api").name == "evalforge.api"
    assert get_logger("api").name == "evalforge.api"


def test_format_produces_valid_json_with_core_fields():
    record = _make_record()
    parsed = json.loads(JsonFormatter().format(record))
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "evalforge.test"
    assert parsed["message"] == "hello"
    assert "ts" in parsed


def test_format_includes_request_id_when_set():
    set_request_id("req-123")
    try:
        parsed = json.loads(JsonFormatter().format(_make_record()))
        assert parsed["request_id"] == "req-123"
    finally:
        set_request_id(None)


def test_format_includes_extra_fields():
    logger = get_logger("test")
    record = logging.LogRecord(
        name=logger.name,
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="trial failed",
        args=(),
        exc_info=None,
    )
    record.extra_fields = {"task_id": "t1", "error": "boom"}
    parsed = json.loads(JsonFormatter().format(record))
    assert parsed["task_id"] == "t1"
    assert parsed["error"] == "boom"


def test_log_helper_attaches_extra_fields(caplog):
    logger = get_logger("helper-test")
    with caplog.at_level(logging.INFO, logger=logger.name):
        log(logger, logging.INFO, "run started", suite="s1", tasks=3)
    assert len(caplog.records) == 1
    assert caplog.records[0].extra_fields == {"suite": "s1", "tasks": 3}
