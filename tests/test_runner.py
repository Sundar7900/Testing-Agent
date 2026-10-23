import asyncio
import base64
from types import SimpleNamespace

import pytest

from tester.agent_runner import credentials_for, login_task, run_flow
from tester.models import FlowPlan, PlannedStep, StepVerdict


PASSWORD = "s3cret-password"
USERNAME = "user@example.com"
URL = "https://app.example.com/login"


def _plan() -> FlowPlan:
    return FlowPlan(
        steps=[
            PlannedStep(instruction="Open Billing", expected="Plan name Pro"),
            PlannedStep(instruction="Read the plan name", expected="Plan name Pro"),
            PlannedStep(instruction="Leave billing", expected="Billing is closed"),
        ]
    )


class FakeHistory:
    def __init__(self) -> None:
        self.items: list[str] = []
        self._shots: list[str | None] = []
        self._errors: list[str | None] = []
        self._final: str | None = None

    def number_of_steps(self) -> int:
        return len(self.items)

    def final_result(self) -> str | None:
        return self._final

    def screenshots(self, n_last: int | None = None, return_none_if_not_screenshot: bool = True) -> list[str | None]:
        return list(self._shots)

    def errors(self) -> list[str | None]:
        return list(self._errors)


class FakeState:
    def __init__(self) -> None:
        self.n_steps = 1


class FakeAgent:
    instances: list["FakeAgent"] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.state = FakeState()
        self.history = FakeHistory()
        self.tasks = [str(kwargs["task"])]
        self.calls: list[int] = []
        self.outcomes: list[dict[str, object]] = []
        FakeAgent.instances.append(self)

    def add_new_task(self, task: str) -> None:
        self.tasks.append(task)

    async def run(self, max_steps: int, on_step_end: object = None) -> FakeHistory:
        self.calls.append(max_steps)
        outcome = self.outcomes[len(self.calls) - 1]
        cap = int(outcome.get("steps", max_steps - self.state.n_steps + 1))
        shot = outcome.get("screenshot")
        error = outcome.get("error")
        for _ in range(cap):
            self.history.items.append("step")
            self.history._shots.append(shot if isinstance(shot, str) else None)
            self.history._errors.append(error if isinstance(error, str) else None)
        verdict = outcome.get("verdict")
        self.history._final = verdict.model_dump_json() if isinstance(verdict, StepVerdict) else None
        self.state.n_steps = max_steps + 1
        return self.history


class FakeBrowser:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.killed = False

    async def kill(self) -> None:
        self.killed = True


class FakeLLM:
    async def ainvoke(self, messages: list[object], output_format: type[FlowPlan] | None = None, **kwargs: object) -> SimpleNamespace:
        assert output_format is FlowPlan
        text = messages[0].text  # type: ignore[attr-defined]
        assert PASSWORD not in text
        return SimpleNamespace(completion=_plan())


def _patch(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAgent.instances = []
    monkeypatch.setattr("tester.agent_runner.Agent", FakeAgent)
    monkeypatch.setattr("tester.agent_runner.Browser", FakeBrowser)


def _verdict(passed: bool) -> StepVerdict:
    return StepVerdict(passed=passed, reason="checked", observed="Pro")


def test_login_task_uses_placeholders_only() -> None:
    text = login_task(URL, PlannedStep(instruction="Open Billing", expected="Plan name Pro"))
    assert "x_user" in text
    assert "x_pass" in text
    assert PASSWORD not in text
    assert USERNAME not in text
    stored = credentials_for(URL, USERNAME, PASSWORD)
    assert stored == {"https://app.example.com": {"x_user": USERNAME, "x_pass": PASSWORD}}


def test_run_records_pass_and_hides_password(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)
    shot = base64.b64encode(b"png-bytes").decode()

    async def scenario() -> None:
        agent_holder: list[FakeAgent] = []

        class RecordingAgent(FakeAgent):
            def __init__(self, **kwargs: object) -> None:
                super().__init__(**kwargs)
                self.outcomes = [
                    {"verdict": _verdict(True), "steps": 1, "screenshot": shot},
                    {"verdict": _verdict(True), "steps": 1, "screenshot": shot},
                    {"verdict": _verdict(True), "steps": 1, "screenshot": shot},
                ]
                agent_holder.append(self)

        monkeypatch.setattr("tester.agent_runner.Agent", RecordingAgent)
        report = await run_flow(
            url=URL,
            username=USERNAME,
            password=PASSWORD,
            flow=f"Log in with {PASSWORD} and confirm Pro",
            api_key="test-key",
            report_dir=tmp_path,
            llm=FakeLLM(),
        )
        assert report.passed
        assert PASSWORD not in report.flow
        task = str(agent_holder[0].kwargs["task"])
        assert "x_user" in task and "x_pass" in task
        assert PASSWORD not in task
        assert agent_holder[0].kwargs["use_vision"] is False
        assert agent_holder[0].kwargs["sensitive_data"]["https://app.example.com"]["x_pass"] == PASSWORD
        browser = agent_holder[0].kwargs["browser"]
        assert browser.kwargs["allowed_domains"] == ["app.example.com"]
        assert browser.kwargs["headless"] is True
        assert browser.kwargs["keep_alive"] is True
        assert browser.killed
        assert (tmp_path / "step-1.png").read_bytes() == b"png-bytes"

    asyncio.run(scenario())


def test_failed_check_continues(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)

    class Scripted(FakeAgent):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(**kwargs)
            self.outcomes = [
                {"verdict": _verdict(False), "steps": 1},
                {"verdict": _verdict(True), "steps": 1},
                {"verdict": _verdict(True), "steps": 1},
            ]

    monkeypatch.setattr("tester.agent_runner.Agent", Scripted)
    report = asyncio.run(
        run_flow(
            url=URL,
            username=USERNAME,
            password=PASSWORD,
            flow="Confirm Pro",
            api_key="test-key",
            report_dir=tmp_path,
            llm=FakeLLM(),
        )
    )
    assert [step.passed for step in report.steps] == [False, True, True]
    assert report.passed is False
    assert report.stopped_reason is None


def test_action_budget_stops_later_steps(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)

    class BudgetAgent(FakeAgent):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(**kwargs)
            self.outcomes = [{}, {}, {}]

    monkeypatch.setattr("tester.agent_runner.Agent", BudgetAgent)
    report = asyncio.run(
        run_flow(
            url=URL,
            username=USERNAME,
            password=PASSWORD,
            flow="Confirm Pro",
            api_key="test-key",
            report_dir=tmp_path,
            llm=FakeLLM(),
            max_actions=10,
        )
    )
    agent = FakeAgent.instances[-1]
    assert agent.calls == [8, 10]
    assert report.stopped_reason == "action budget"
    assert report.passed is False
    assert len(report.steps) == 2


def test_time_budget_stops_before_the_next_step(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)
    ticks = iter([0, 10, 500])

    class ShortAgent(FakeAgent):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(**kwargs)
            self.outcomes = [{"verdict": _verdict(True), "steps": 1}]

    monkeypatch.setattr("tester.agent_runner.Agent", ShortAgent)
    report = asyncio.run(
        run_flow(
            url=URL,
            username=USERNAME,
            password=PASSWORD,
            flow="Confirm Pro",
            api_key="test-key",
            report_dir=tmp_path,
            llm=FakeLLM(),
            time_budget_seconds=30,
            clock=lambda: next(ticks),
        )
    )
    assert len(report.steps) == 1
    assert report.stopped_reason == "time budget"


def test_step_error_stops_the_run(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)

    class ErrorAgent(FakeAgent):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(**kwargs)
            self.outcomes = [{"error": f"blocked {PASSWORD}", "steps": 1}]

    monkeypatch.setattr("tester.agent_runner.Agent", ErrorAgent)
    report = asyncio.run(
        run_flow(
            url=URL,
            username=USERNAME,
            password=PASSWORD,
            flow="Confirm Pro",
            api_key="test-key",
            report_dir=tmp_path,
            llm=FakeLLM(),
        )
    )
    assert report.stopped_reason == "step error"
    assert len(report.steps) == 1
    assert PASSWORD not in report.steps[0].reason
    assert "[redacted]" in report.steps[0].reason
