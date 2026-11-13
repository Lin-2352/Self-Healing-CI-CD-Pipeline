"""Request and response models for generated patches."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

_DEFAULT_BRANCHES = frozenset({"main", "master"})


class FixProposal(BaseModel):
    """The only JSON object the fixer model is allowed to return.

    One object edits one file. Extra keys are rejected, so a log that tells the model to
    add a field cannot widen this schema. `main` and `master` are not branch names we accept.
    """

    model_config = ConfigDict(extra="forbid")

    branch_name: str = Field(min_length=1, max_length=200)
    file_path: str = Field(min_length=1, max_length=1024)
    edits: str = Field(min_length=1)
    pr_title: str = Field(min_length=1, max_length=256)
    pr_body: str = Field(min_length=1, max_length=20000)

    @field_validator("branch_name")
    @classmethod
    def _branch_is_a_single_safe_segment(cls, value: str) -> str:
        """Reject the default branch and any name that is not one path segment."""
        cleaned = value.strip()
        unsafe = (
            cleaned.lower() in _DEFAULT_BRANCHES
            or "/" in cleaned
            or "\\" in cleaned
            or ".." in cleaned
            or any(character.isspace() for character in cleaned)
        )
        if unsafe or cleaned == "":
            raise ValueError("branch_name must be one segment and must not be main or master")
        return cleaned


class FixCandidateCreate(BaseModel):
    """One generated patch to store."""

    failure_id: int = Field(ge=1)
    candidate_index: int = Field(ge=0)
    file_path: str = Field(min_length=1, max_length=1024)
    edits: str
    applied_ok: bool = False
    error: str | None = None
    diff: str | None = None


class FixCandidateRead(BaseModel):
    """A stored patch candidate."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    failure_id: int
    candidate_index: int
    file_path: str
    edits: str
    applied_ok: bool
    error: str | None
    diff: str | None
    created_at: datetime
