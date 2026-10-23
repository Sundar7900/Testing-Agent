from __future__ import annotations

import base64
import os
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")

from browser_use import Agent, Browser

from tester.cursor_llm import CursorChatModel
from tester.models import PlannedStep, RunReport, StepReport, StepVerdict
from tester.planner import plan_flow
from tester.report import redact

MAX_ACTIONS = 25
TIME_BUDGET_SECONDS = 900
ACTIONS_PER_STEP = 8


def site_host(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must be an http(s) address with a host")
    return parsed.hostname


def credentials_for(url: str, username: str, password: str) -> dict[str, dict[str, str]]:
    host = site_host(url)
    return {f"https://{host}": {"x_user": username, "x_pass": password}}


def login_task(url: str, step: PlannedStep) -> str:
    host = site_host(url)
    return (
        f"Open {url}. If a login form is showing, sign in with x_user and x_pass. "
        f"Then do this: {step.instruction}. "
        f"Decide whether this expected result is true: {step.expected}. "
        f"Stay on {host}."
    )


def step_task(url: str, step: PlannedStep) -> str:
    host = site_host(url)
    return (
        "Continue on the already open site. "
        f"Do this: {step.instruction}. "
        f"Decide whether this expected result is true: {step.expected}. "
        f"Stay on {host}."
    )


def parse_verdict(raw: str | None) -> StepVerdict | None:
    if not raw:
        return None
    try:
        from tester.cursor_llm import extract_json

        return StepVerdict.model_validate_json(extract_json(raw))
    except (ValueError, TypeError):
        return None


def _history_length(history: object) -> int:
    return int(history.number_of_steps())  # type: ignore[attr-defined]


def _new_errors(history: object, seen: int) -> list[str]:
    errors = history.errors()  # type: ignore[attr-defined]
    return [error for error in errors[seen:] if error]


def _last_screenshot(history: object, seen: int) -> str | None:
    shots = history.screenshots(return_none_if_not_screenshot=True)  # type: ignore[attr-defined]
    fresh = [shot for shot in shots[seen:] if shot]
    return fresh[-1] if fresh else None


def save_screenshot(value: str | None, directory: Path, index: int) -> str | None:
    if not value:
        return None
    encoded = value.split(",", 1)[1] if value.startswith("data:") else value
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        try:
            payload = base64.b64decode(encoded)
        except (ValueError, TypeError):
            return None
    filename = f"step-{index}.png"
    (directory / filename).write_bytes(payload)
    return filename


def _step_ceiling(agent: object, cap: int) -> int:
    state = getattr(agent, "state", None)
    current = getattr(state, "n_steps", None)
    if isinstance(current, int):
        return current + cap - 1
    return cap


def _safe_step(step: PlannedStep, secrets: list[str]) -> PlannedStep:
    return PlannedStep(
        instruction=redact(step.instruction, secrets),
        expected=redact(step.expected, secrets),
    )


def _absorb_actions(counters: dict[str, int], before: int, taken: int) -> None:
    callback_added = counters["actions"] - before
    if taken > callback_added:
        counters["actions"] += taken - callback_added


def _record(
    step: PlannedStep,
    verdict: StepVerdict | None,
    screenshot: str | None,
    secrets: list[str],
    fallback_reason: str,
) -> StepReport:
    if verdict is None:
        passed = False
        reason = fallback_reason
        observed = ""
    else:
        passed = verdict.passed
        reason = verdict.reason
        observed = verdict.observed
    return StepReport(
        instruction=redact(step.instruction, secrets),
        expected=redact(step.expected, secrets),
        passed=passed,
        reason=redact(reason, secrets),
        observed=redact(observed, secrets),
        screenshot=screenshot,
    )


async def run_flow(
    *,
    url: str,
    username: str,
    password: str,
    flow: str,
    api_key: str,
    report_dir: Path,
    llm: CursorChatModel | None = None,
    max_actions: int = MAX_ACTIONS,
    time_budget_seconds: float = TIME_BUDGET_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> RunReport:
    host = site_host(url)
    secrets = [password, username]
    safe_flow = redact(flow, secrets)
    model = llm or CursorChatModel(api_key=api_key)
    plan = await plan_flow(model, safe_flow)
    safe_steps = [_safe_step(step, secrets) for step in plan.steps]
    report_dir.mkdir(parents=True, exist_ok=True)

    browser = Browser(allowed_domains=[host], headless=True, keep_alive=True)
    counters = {"actions": 0}
    started = clock()
    deadline = started + time_budget_seconds

    agent = Agent(
        task=login_task(url, safe_steps[0]),
        llm=model,
        browser=browser,
        sensitive_data=credentials_for(url, username, password),
        use_vision=False,
        output_model_schema=StepVerdict,
        use_judge=False,
        enable_planning=False,
        enable_signal_handler=False,
    )

    step_reports: list[StepReport] = []
    stopped_reason: str | None = None
    seen = 0

    try:
        for index, step in enumerate(safe_steps):
            if clock() >= deadline:
                stopped_reason = "time budget"
                break
            if counters["actions"] >= max_actions:
                stopped_reason = "action budget"
                break
            remaining = max_actions - counters["actions"]
            cap = min(ACTIONS_PER_STEP, remaining)
            if index > 0:
                agent.add_new_task(step_task(url, step))
            before_actions = counters["actions"]
            try:
                history = await agent.run(max_steps=_step_ceiling(agent, cap))
            except InterruptedError:
                history = getattr(agent, "history", None)
                if history is not None and _history_length(history) > seen:
                    _absorb_actions(counters, before_actions, _history_length(history) - seen)
                    verdict = parse_verdict(history.final_result())
                    screenshot = save_screenshot(
                        _last_screenshot(history, seen), report_dir, index + 1
                    )
                    step_reports.append(
                        _record(step, verdict, screenshot, secrets, "Stopped before a verdict")
                    )
                    seen = _history_length(history)
                stopped_reason = "time budget" if clock() >= deadline else "action budget"
                break
            except Exception as exc:
                step_reports.append(
                    _record(step, None, None, secrets, redact(str(exc), secrets))
                )
                stopped_reason = "step error"
                break

            taken = _history_length(history) - seen
            _absorb_actions(counters, before_actions, taken)
            verdict = parse_verdict(history.final_result())
            screenshot = save_screenshot(_last_screenshot(history, seen), report_dir, index + 1)
            errors = _new_errors(history, seen)
            seen = _history_length(history)
            if verdict is None and errors:
                step_reports.append(_record(step, None, screenshot, secrets, errors[-1]))
                stopped_reason = "step error"
                break
            step_reports.append(
                _record(step, verdict, screenshot, secrets, "The agent did not return a verdict")
            )
    finally:
        await browser.kill()

    finished = len(step_reports) == len(safe_steps) and stopped_reason is None
    passed = finished and all(item.passed for item in step_reports)
    return RunReport(
        url=url,
        flow=safe_flow,
        passed=passed,
        steps=step_reports,
        stopped_reason=stopped_reason,
    )
