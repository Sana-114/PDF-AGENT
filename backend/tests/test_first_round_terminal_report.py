import json
from types import SimpleNamespace

import pytest

from scripts import evaluate_official_first_round as evaluator


@pytest.mark.asyncio
async def test_runtime_failure_is_terminal_and_not_retried(tmp_path, monkeypatch):
    output = tmp_path / "report.json"
    calls = []

    async def failure(args):
        calls.append(True)
        evaluator.write_report(output, {"status": "running", "model_calls": 0, "cases": []})
        args._created_report_this_run = True
        raise TimeoutError("sensitive diagnostics are not copied into the report")

    monkeypatch.setattr(evaluator, "_run_once", failure)
    with pytest.raises(TimeoutError):
        await evaluator.run(SimpleNamespace(output=output))
    saved = json.loads(output.read_text())
    assert calls == [True]
    assert saved["status"] == "stopped_runtime_error"
    assert saved["error_phase"] == "before_generation"
    assert saved["error_type"] == "TimeoutError"
    assert "sensitive" not in output.read_text()


@pytest.mark.asyncio
async def test_existing_report_is_never_changed_on_preflight_failure(tmp_path, monkeypatch):
    output = tmp_path / "report.json"
    original = '{"status":"running","model_calls":4}'
    output.write_text(original)

    async def failure(args):
        raise ValueError("Existing reports cannot be overwritten")

    monkeypatch.setattr(evaluator, "_run_once", failure)
    with pytest.raises(ValueError):
        await evaluator.run(SimpleNamespace(output=output))
    assert output.read_text() == original
