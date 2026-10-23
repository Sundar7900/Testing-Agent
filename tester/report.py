from __future__ import annotations

import html
from pathlib import Path

from tester.models import RunReport, StepReport


def redact(text: str, secrets: list[str]) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted


def redact_step(step: StepReport, secrets: list[str]) -> StepReport:
    return step.model_copy(
        update={
            "instruction": redact(step.instruction, secrets),
            "expected": redact(step.expected, secrets),
            "reason": redact(step.reason, secrets),
            "observed": redact(step.observed, secrets),
        }
    )


def redact_report(report: RunReport, secrets: list[str]) -> RunReport:
    return report.model_copy(
        update={
            "flow": redact(report.flow, secrets),
            "stopped_reason": redact(report.stopped_reason, secrets) if report.stopped_reason else None,
            "steps": [redact_step(step, secrets) for step in report.steps],
        }
    )


def render_html(report: RunReport) -> str:
    status = "pass" if report.passed else "fail"
    rows = []
    for index, step in enumerate(report.steps, start=1):
        step_status = "pass" if step.passed else "fail"
        screenshot = ""
        if step.screenshot:
            screenshot = (
                f'<img src="{html.escape(step.screenshot)}" alt="Screenshot for step {index}">'
            )
        rows.append(
            "<article>"
            f"<h2>Step {index}: {html.escape(step_status)}</h2>"
            f"<p><strong>Instruction:</strong> {html.escape(step.instruction)}</p>"
            f"<p><strong>Expected:</strong> {html.escape(step.expected)}</p>"
            f"<p><strong>Observed:</strong> {html.escape(step.observed)}</p>"
            f"<p><strong>Reason:</strong> {html.escape(step.reason)}</p>"
            f"{screenshot}"
            "</article>"
        )
    stopped = ""
    if report.stopped_reason:
        stopped = f"<p><strong>Stopped:</strong> {html.escape(report.stopped_reason)}</p>"
    body = "\n".join(rows) if rows else "<p>No steps were run.</p>"
    return (
        "<!DOCTYPE html>"
        "<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<title>Flow test report</title>"
        "<style>"
        "body{font-family:sans-serif;margin:2rem;}"
        "article{border-top:1px solid #ccc;padding:1rem 0;}"
        "img{max-width:100%;border:1px solid #ccc;}"
        ".pass{color:#0a7a2f}.fail{color:#a12622}"
        "</style></head><body>"
        f"<h1 class=\"{status}\">{html.escape(status)}</h1>"
        f"<p><strong>URL:</strong> {html.escape(report.url)}</p>"
        f"<p><strong>Flow:</strong> {html.escape(report.flow)}</p>"
        f"{stopped}"
        f"{body}"
        "</body></html>"
    )


def write_report(report: RunReport, directory: Path, secrets: list[str] | None = None) -> Path:
    safe = redact_report(report, secrets or [])
    directory.mkdir(parents=True, exist_ok=True)
    payload = safe.model_dump_json(indent=2)
    payload = redact(payload, secrets or [])
    (directory / "results.json").write_text(payload + "\n", encoding="utf-8")
    page = render_html(safe)
    page = redact(page, secrets or [])
    (directory / "index.html").write_text(page, encoding="utf-8")
    return directory
