import pytest

from tester.models import PlannedStep
from tester.planner import parse_flow_plan


def test_parse_flow_plan_reads_json() -> None:
    plan = parse_flow_plan(
        '{"steps":[{"instruction":"Open Billing","expected":"Plan name Pro"}]}'
    )
    assert plan.steps == [PlannedStep(instruction="Open Billing", expected="Plan name Pro")]


def test_parse_flow_plan_reads_fenced_json() -> None:
    plan = parse_flow_plan(
        'Here is the plan:\n```json\n{"steps":[{"instruction":"Open Billing","expected":"Pro"}]}\n```'
    )
    assert plan.steps[0].instruction == "Open Billing"


def test_parse_flow_plan_rejects_an_empty_step_list() -> None:
    with pytest.raises(ValueError):
        parse_flow_plan('{"steps":[]}')
