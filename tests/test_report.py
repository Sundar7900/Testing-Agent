from tester.models import RunReport, StepReport
from tester.report import redact, write_report


def test_redact_replaces_password() -> None:
    assert redact("signed in with s3cret-password", ["s3cret-password"]) == "signed in with [redacted]"


def test_report_hides_password(tmp_path) -> None:
    report = RunReport(
        url="https://app.example.com",
        flow="Confirm the plan",
        passed=False,
        stopped_reason=None,
        steps=[
            StepReport(
                instruction="Open Billing",
                expected="Plan name Pro",
                passed=False,
                reason="saw s3cret-password",
                observed="s3cret-password",
                screenshot=None,
            )
        ],
    )
    write_report(report, tmp_path, secrets=["s3cret-password"])
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    payload = (tmp_path / "results.json").read_text(encoding="utf-8")
    assert "fail" in html
    assert "Open Billing" in html
    assert "s3cret-password" not in html
    assert "s3cret-password" not in payload
    assert "[redacted]" in payload
