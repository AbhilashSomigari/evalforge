from __future__ import annotations

import sys
import textwrap

import httpx
import pytest

from evalforge.adapters.command import CommandAgentAdapter
from evalforge.adapters.http import HttpAgentAdapter
from evalforge.models import TaskSpec

PYTHON = sys.executable


def _write_script(tmp_path, body: str):
    path = tmp_path / "agent.py"
    path.write_text(textwrap.dedent(body))
    return path


@pytest.mark.asyncio
async def test_command_adapter_success(tmp_path):
    script = _write_script(
        tmp_path,
        """
        import json, sys
        task = json.load(sys.stdin)
        json.dump({"output": f"got {task['input']}"}, sys.stdout)
        """,
    )
    adapter = CommandAgentAdapter(f"{PYTHON} {script}")
    output = await adapter.run(TaskSpec(id="t1", input="hello"))
    assert output.output == "got hello"


@pytest.mark.asyncio
async def test_command_adapter_nonzero_exit_raises(tmp_path):
    script = _write_script(
        tmp_path,
        """
        import sys
        sys.stderr.write("boom")
        sys.exit(1)
        """,
    )
    adapter = CommandAgentAdapter(f"{PYTHON} {script}")
    with pytest.raises(RuntimeError, match="boom"):
        await adapter.run(TaskSpec(id="t1", input="hello"))


@pytest.mark.asyncio
async def test_command_adapter_invalid_json_raises(tmp_path):
    script = _write_script(tmp_path, "import sys; sys.stdout.write('not json')")
    adapter = CommandAgentAdapter(f"{PYTHON} {script}")
    with pytest.raises(RuntimeError, match="not valid JSON"):
        await adapter.run(TaskSpec(id="t1", input="hello"))


@pytest.mark.asyncio
async def test_command_adapter_timeout_raises(tmp_path):
    script = _write_script(tmp_path, "import time; time.sleep(5)")
    adapter = CommandAgentAdapter(f"{PYTHON} {script}", timeout_s=0.2)
    with pytest.raises(RuntimeError, match="timed out"):
        await adapter.run(TaskSpec(id="t1", input="hello"))


@pytest.mark.asyncio
async def test_command_adapter_retries_then_succeeds(tmp_path):
    counter = tmp_path / "attempts"
    script = _write_script(
        tmp_path,
        f"""
        import json, sys
        from pathlib import Path
        counter = Path({str(counter)!r})
        n = int(counter.read_text()) if counter.exists() else 0
        counter.write_text(str(n + 1))
        if n == 0:
            sys.exit(1)
        json.dump({{"output": "ok"}}, sys.stdout)
        """,
    )
    adapter = CommandAgentAdapter(f"{PYTHON} {script}", retries=1, retry_backoff_s=0.01)
    output = await adapter.run(TaskSpec(id="t1", input="hello"))
    assert output.output == "ok"
    assert counter.read_text() == "2"


@pytest.mark.asyncio
async def test_command_adapter_exhausts_retries_then_raises(tmp_path):
    script = _write_script(tmp_path, "import sys; sys.exit(1)")
    adapter = CommandAgentAdapter(f"{PYTHON} {script}", retries=2, retry_backoff_s=0.01)
    with pytest.raises(RuntimeError):
        await adapter.run(TaskSpec(id="t1", input="hello"))


@pytest.mark.asyncio
async def test_command_adapter_no_retries_by_default(tmp_path):
    script = _write_script(tmp_path, "import sys; sys.exit(1)")
    adapter = CommandAgentAdapter(f"{PYTHON} {script}")
    assert adapter.retries == 0
    with pytest.raises(RuntimeError):
        await adapter.run(TaskSpec(id="t1", input="hello"))


@pytest.mark.asyncio
async def test_http_adapter_success():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output": "ok"})

    adapter = HttpAgentAdapter("http://agent.local/run", transport=httpx.MockTransport(handler))
    output = await adapter.run(TaskSpec(id="t1", input="hello"))
    assert output.output == "ok"


@pytest.mark.asyncio
async def test_http_adapter_retries_on_server_error_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"output": "ok"})

    adapter = HttpAgentAdapter(
        "http://agent.local/run",
        retries=1,
        retry_backoff_s=0.01,
        transport=httpx.MockTransport(handler),
    )
    output = await adapter.run(TaskSpec(id="t1", input="hello"))
    assert output.output == "ok"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_http_adapter_does_not_retry_client_error():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400)

    adapter = HttpAgentAdapter(
        "http://agent.local/run",
        retries=3,
        retry_backoff_s=0.01,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(httpx.HTTPStatusError):
        await adapter.run(TaskSpec(id="t1", input="hello"))
    assert calls["n"] == 1
