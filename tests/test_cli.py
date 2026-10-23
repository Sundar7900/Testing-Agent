import pytest

from tester.cli import main
from tester.models import RunReport, StepReport


def test_missing_api_key_exits(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("tester.cli.load_dotenv", lambda: None)
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "run",
                "--url",
                "https://app.example.com",
                "--username",
                "user@example.com",
                "--password",
                "s3cret-password",
                "--flow",
                "Confirm Pro",
            ]
        )
    assert caught.value.code == 2


def test_cli_exit_code_follows_the_report(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("tester.cli.load_dotenv", lambda: None)
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")
    monkeypatch.setattr("tester.cli.report_directory", lambda: tmp_path)

    async def fake_run(**kwargs: object) -> RunReport:
        assert kwargs["password"] == "s3cret-password"
        return RunReport(
            url=str(kwargs["url"]),
            flow="Confirm Pro",
            passed=False,
            steps=[
                StepReport(
                    instruction="Open Billing",
                    expected="Pro",
                    passed=False,
                    reason="missing s3cret-password",
                    observed="",
                )
            ],
        )

    monkeypatch.setattr("tester.cli.run_flow", fake_run)
    code = main(
        [
            "run",
            "--url",
            "https://app.example.com",
            "--username",
            "user@example.com",
            "--password",
            "s3cret-password",
            "--flow",
            "Confirm Pro",
        ]
    )
    assert code == 1
    assert "s3cret-password" not in (tmp_path / "index.html").read_text(encoding="utf-8")
