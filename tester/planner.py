from browser_use.llm.messages import UserMessage

from tester.cursor_llm import CursorChatModel, extract_json
from tester.models import FlowPlan

PLANNER_INSTRUCTIONS = (
    "Split this website test into ordered steps. "
    "Each step needs an instruction describing what to do, and an expected result "
    "that a person can verify on the page. Do not include passwords or usernames. "
    "Return only JSON matching the schema.\n\nFlow:\n"
)


def parse_flow_plan(text: str) -> FlowPlan:
    return FlowPlan.model_validate_json(extract_json(text))


async def plan_flow(llm: CursorChatModel, flow: str) -> FlowPlan:
    result = await llm.ainvoke(
        [UserMessage(content=PLANNER_INSTRUCTIONS + flow)],
        output_format=FlowPlan,
    )
    plan = result.completion
    if not isinstance(plan, FlowPlan):
        raise TypeError("Planner did not return a flow plan")
    return plan
