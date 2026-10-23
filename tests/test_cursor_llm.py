import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from tester.cursor_llm import CursorChatModel, complete, extract_json, render_prompt
from tester.models import FlowPlan, PlannedStep


def test_extract_json_ignores_markdown_fence() -> None:
    raw = '```json\n{"passed": true, "reason": "ok", "observed": "Pro"}\n```'
    assert extract_json(raw).startswith("{")


def test_render_prompt_forbids_file_edits_and_includes_schema() -> None:
    prompt = render_prompt(
        [SimpleNamespace(role="user", text="Open billing", tool_calls=[])],
        FlowPlan,
    )
    assert "Do not edit files" in prompt
    assert "Open billing" in prompt
    assert "instruction" in prompt


def test_complete_uses_cursor_api_key_without_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class Result:
        status = "finished"
        result = "ok"
        error_message = None

    def fake_prompt(message: str, options: object) -> Result:
        seen["message"] = message
        seen["api_key"] = options.api_key  # type: ignore[attr-defined]
        seen["model"] = options.model  # type: ignore[attr-defined]
        seen["tools"] = list(options.tools)  # type: ignore[attr-defined]
        seen["cwd"] = options.local.cwd  # type: ignore[attr-defined]
        assert Path(str(seen["cwd"])).is_dir()
        return Result()

    monkeypatch.setattr("tester.cursor_llm.Agent.prompt", fake_prompt)
    assert complete("hello", "test-key", "auto") == "ok"
    assert seen["api_key"] == "test-key"
    assert seen["model"] == "auto"
    assert seen["tools"] == []
    assert not Path(str(seen["cwd"])).exists()


def test_complete_raises_when_cursor_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    class Result:
        status = "error"
        result = ""
        error_message = "denied"

    monkeypatch.setattr("tester.cursor_llm.Agent.prompt", lambda message, options: Result())
    with pytest.raises(RuntimeError, match="denied"):
        complete("hello", "test-key", "auto")


def test_ainvoke_parses_structured_json(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = FlowPlan(
        steps=[PlannedStep(instruction="Open Billing", expected="Plan name Pro")]
    ).model_dump_json()
    monkeypatch.setattr(
        "tester.cursor_llm.complete",
        lambda prompt, api_key, model: f"Sure\n```json\n{payload}\n```",
    )
    model = CursorChatModel(api_key="test-key")
    result = asyncio.run(
        model.ainvoke([SimpleNamespace(role="user", text="flow", tool_calls=[])], output_format=FlowPlan)
    )
    assert isinstance(result.completion, FlowPlan)
    assert result.completion.steps[0].expected == "Plan name Pro"
