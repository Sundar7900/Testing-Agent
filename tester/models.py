from pydantic import BaseModel, Field


class PlannedStep(BaseModel):
    instruction: str = Field(min_length=1)
    expected: str = Field(min_length=1)


class FlowPlan(BaseModel):
    steps: list[PlannedStep] = Field(min_length=1)


class StepVerdict(BaseModel):
    passed: bool
    reason: str
    observed: str


class StepReport(BaseModel):
    instruction: str
    expected: str
    passed: bool
    reason: str
    observed: str
    screenshot: str | None = None


class RunReport(BaseModel):
    url: str
    flow: str
    passed: bool
    steps: list[StepReport]
    stopped_reason: str | None = None
