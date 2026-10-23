from __future__ import annotations

import asyncio
import json
import tempfile
from typing import Any

from browser_use.llm.views import ChatInvokeCompletion, ChatInvokeUsage
from cursor_sdk import Agent, AgentOptions, LocalAgentOptions
from pydantic import BaseModel

PREAMBLE = (
    "Answer the request below. Do not edit files, do not run tools, "
    "and do not add commentary outside JSON when a schema is present."
)


def empty_usage() -> ChatInvokeUsage:
    return ChatInvokeUsage(
        prompt_tokens=0,
        prompt_cached_tokens=0,
        prompt_cache_creation_tokens=0,
        prompt_image_tokens=0,
        completion_tokens=0,
        total_tokens=0,
    )


def message_block(message: Any) -> str:
    role = getattr(message, "role", "user")
    text = getattr(message, "text", "") or ""
    tool_calls = getattr(message, "tool_calls", None) or []
    rendered_calls = []
    for call in tool_calls:
        if hasattr(call, "model_dump_json"):
            rendered_calls.append(call.model_dump_json())
        else:
            rendered_calls.append(str(call))
    body = text
    if rendered_calls:
        body = f"{text}\n" + "\n".join(rendered_calls) if text else "\n".join(rendered_calls)
    return f"{role}:\n{body}"


def render_prompt(messages: list[Any], output_format: type[BaseModel] | None) -> str:
    parts = [PREAMBLE]
    parts.extend(message_block(message) for message in messages)
    if output_format is not None:
        schema = json.dumps(output_format.model_json_schema())
        parts.append(
            "Return only a JSON object matching this schema. "
            f"Do not wrap it in markdown.\n{schema}"
        )
    return "\n\n".join(parts)


def extract_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start_object = stripped.find("{")
    start_array = stripped.find("[")
    starts = [index for index in (start_object, start_array) if index >= 0]
    if not starts:
        raise ValueError("Model response did not contain JSON")
    start = min(starts)
    decoder = json.JSONDecoder()
    _value, end = decoder.raw_decode(stripped[start:])
    return stripped[start : start + end]


def complete(prompt: str, api_key: str, model: str) -> str:
    """Ask Cursor for one completion. The working directory is discarded."""
    with tempfile.TemporaryDirectory(prefix="cursor-llm-") as cwd:
        result = Agent.prompt(
            prompt,
            AgentOptions(
                api_key=api_key,
                model=model,
                local=LocalAgentOptions(cwd=cwd),
                tools=[],
            ),
        )
    if result.status != "finished":
        detail = result.error_message or result.status
        raise RuntimeError(f"Cursor agent failed: {detail}")
    return result.result or ""


class CursorChatModel:
    """Browser Use chat model backed by the Cursor Python SDK."""

    def __init__(self, api_key: str, model: str = "auto") -> None:
        self.api_key = api_key
        self.model = model

    @property
    def provider(self) -> str:
        return "cursor"

    @property
    def name(self) -> str:
        return self.model

    @property
    def model_name(self) -> str:
        return self.model

    async def ainvoke(
        self,
        messages: list[Any],
        output_format: type[BaseModel] | None = None,
        **kwargs: Any,
    ) -> ChatInvokeCompletion:
        prompt = render_prompt(messages, output_format)
        text = await asyncio.to_thread(complete, prompt, self.api_key, self.model)
        if output_format is None:
            completion: Any = text
        else:
            completion = output_format.model_validate_json(extract_json(text))
        return ChatInvokeCompletion(completion=completion, usage=empty_usage())
