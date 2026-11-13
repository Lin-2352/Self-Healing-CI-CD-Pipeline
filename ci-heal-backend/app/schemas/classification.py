"""Request and response models for classifier verdicts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import FailureCategory

# The model may only emit these four. `unknown` is our fallback, not a label it can choose.
ClassifierLabel = Literal[
    "dependency_error",
    "configuration_error",
    "test_failure",
    "flaky_or_network",
]


class ClassifierOutput(BaseModel):
    """The only JSON object the classifier model is allowed to return.

    Extra keys are rejected, so a log that tells the model to add a field cannot widen
    this schema. `unknown` is absent on purpose: the service uses it only after parsing fails.
    """

    model_config = ConfigDict(extra="forbid")

    category: ClassifierLabel
    confidence: float = Field(ge=0, le=1)
    reasoning: str = Field(min_length=1)


class ClassificationSample(BaseModel):
    """One classifier sample, kept so evaluation can compare signals later."""

    sample_index: int = Field(ge=0)
    category: FailureCategory | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    reasoning: str | None = None
    raw_output: str
    parse_status: Literal["ok", "invalid"]
    parse_attempts: int = Field(ge=1)


class ClassificationCreate(BaseModel):
    """A verdict to store against a failure."""

    failure_id: int = Field(ge=1)
    category: FailureCategory
    confidence: float = Field(ge=0, le=1)
    reasoning: str
    model: str = Field(min_length=1, max_length=255)
    raw_output: str
    samples: list[ClassificationSample] = Field(default_factory=list)
    parse_attempts: int = Field(default=1, ge=1)
    latency_ms: int | None = Field(default=None, ge=0)


class ClassificationRead(BaseModel):
    """A stored classifier verdict."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    failure_id: int
    category: FailureCategory
    confidence: float
    reasoning: str
    model: str
    raw_output: str
    samples: list[ClassificationSample]
    parse_attempts: int
    latency_ms: int | None
    created_at: datetime
