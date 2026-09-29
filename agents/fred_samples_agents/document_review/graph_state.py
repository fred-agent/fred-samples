"""Small, explicit wire models for the application-owned review workflow.

These are MCP boundary models, not imports from the application package. Tokens
never enter agent inputs or state; Fred's runtime owns bearer propagation.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

StageId = Literal["analyst", "risk_reviewer", "action_planner", "summary"]
STAGE_IDS: tuple[StageId, ...] = (
    "analyst",
    "risk_reviewer",
    "action_planner",
    "summary",
)
AGENT_IDS = {
    "coordinator": "fred.samples.document_review.coordinator",
    "analyst": "fred.samples.document_review.analyst",
    "risk_reviewer": "fred.samples.document_review.risk_reviewer",
    "action_planner": "fred.samples.document_review.action_planner",
}
TASK_PATTERN = r"^task-[0-9a-f]{32}$"


_LENGTH_KEYWORDS = frozenset({"minLength", "maxLength", "minItems", "maxItems"})


def _without_length_bounds(node: object) -> object:
    if isinstance(node, dict):
        return {
            key: _without_length_bounds(value)
            for key, value in node.items()
            if key not in _LENGTH_KEYWORDS
        }
    if isinstance(node, list):
        return [_without_length_bounds(value) for value in node]
    return node


class DecisionModel(BaseModel):
    # Match the API's canonicalization so its persisted result compares equal
    # to the value we sent, including model responses with incidental padding.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        # Length bounds stay enforced when parsing a response, but a schema
        # carrying them is rejected by Ollama's grammar compiler, which fails
        # the whole request rather than ignoring what it cannot express.
        return _without_length_bounds(handler(core_schema))  # type: ignore[return-value]


class Evidence(DecisionModel):
    location: str = Field(min_length=1, max_length=200)
    excerpt: str = Field(min_length=1, max_length=1000)


class Finding(DecisionModel):
    title: str = Field(min_length=1, max_length=200)
    severity: Literal["info", "low", "medium", "high", "critical"]
    detail: str = Field(min_length=1, max_length=2000)
    evidence: list[Evidence] = Field(default_factory=list, max_length=20)


class RecommendedAction(DecisionModel):
    title: str = Field(min_length=1, max_length=200)
    priority: Literal["low", "medium", "high"]
    detail: str = Field(min_length=1, max_length=2000)


class ReviewDecision(DecisionModel):
    """A concise, attributable decision, never the model's hidden reasoning."""

    decision: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=4000)
    evidence: list[Evidence] = Field(default_factory=list, max_length=20)
    findings: list[Finding] = Field(default_factory=list, max_length=32)
    actions: list[RecommendedAction] = Field(default_factory=list, max_length=32)
    limitations: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=20
    )
    outcome: Literal["accepted", "needs_attention", "blocked"] | None = None


class ReviewStage(BaseModel):
    stage_id: StageId
    status: str
    result: ReviewDecision | None = None


class ReviewRun(BaseModel):
    run_id: str = Field(min_length=1)
    claim_id: str = Field(min_length=1)
    source_hash: str = Field(min_length=1)
    status: str
    stages: list[ReviewStage]

    def stage(self, stage_id: StageId) -> ReviewStage:
        matches = [stage for stage in self.stages if stage.stage_id == stage_id]
        if len(matches) != 1:
            raise ValueError("The application returned an invalid review stage plan.")
        return matches[0]


class SourceDocument(BaseModel):
    tag_id: str
    document_uid: str
    document_name: str


class ReviewContext(BaseModel):
    task_id: str = Field(pattern=TASK_PATTERN)
    title: str
    document: SourceDocument
    source_hash: str
    content: str = Field(min_length=1, max_length=120_000)
    run: ReviewRun


class WorkItem(BaseModel):
    """Only the coordinator supplies this exact child-agent input."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(pattern=TASK_PATTERN)
    run_id: str = Field(min_length=1, max_length=128)
    claim_id: str = Field(min_length=1, max_length=128)


class StageReceipt(BaseModel):
    """Acknowledgement of a saved result, not a second model-generated result."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    run_id: str
    stage_id: StageId
    status: Literal["completed", "failed"]


class ReviewInput(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)


class ReviewState(BaseModel):
    latest_user_text: str
    task_id: str = ""
    run_id: str = ""
    claim_id: str = ""
    review_context: ReviewContext | None = None
    result: ReviewDecision | None = None
    final_text: str = ""
