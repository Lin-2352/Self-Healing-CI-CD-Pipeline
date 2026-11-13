"""Ask the fixer model for several patches and score how much they agree.

Candidates are requested one after another. A model error or a proposal that will not
apply becomes an invalid candidate. It does not abort the rest. If every call fails, the
result says so. The orchestrator escalates when agreement reports insufficient candidates.

The prompt is built only from `prepare_context`. File bodies, the log, and the diff are
`RedactedText` inside the same untrusted fences the classifier uses. Patches are applied
to the original file text, because the model never sees an unredacted secret to copy.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import ValidationError

from app.config import Settings
from app.core.agreement import Agreement, PatchCandidate, score_agreement
from app.core.patch_apply import ApplyError, ApplyLimits, ApplyResult, apply_patch
from app.core.redacted import RedactedText
from app.llm.base import LLMClient, LLMError
from app.llm.prompts import build_fixer_messages
from app.llm.text import strip_model_text
from app.schemas.fix import FixCandidateCreate, FixProposal
from app.services.secret_redactor import prepare_context, redact, redact_outbound

_UNPARSED_PATH = "(unparsed)"


@dataclass(frozen=True)
class GeneratedFix:
    """One candidate, applicable or not, ready to score and to store."""

    index: int
    branch_name: str | None
    file_path: str
    edits: str
    pr_title: str | None
    pr_body: str | None
    applied_ok: bool
    error: str | None
    diff: str | None
    new_content: str | None

    def to_create(self, failure_id: int) -> FixCandidateCreate:
        """Build the row stored for this candidate. Edits and the diff are redacted again.

        Args:
            failure_id: The failure these candidates belong to.

        Returns:
            A create model. A secret the model wrote is stored as a placeholder.
        """
        diff = None if self.diff is None else redact(self.diff).text
        return FixCandidateCreate(
            failure_id=failure_id,
            candidate_index=self.index,
            file_path=self.file_path,
            edits=redact(self.edits).text,
            applied_ok=self.applied_ok,
            error=self.error,
            diff=diff,
        )


@dataclass(frozen=True)
class FixGeneration:
    """Every candidate plus the agreement score. `failed` means every model call raised."""

    failed: bool
    failure_reason: str | None
    candidates: tuple[GeneratedFix, ...]
    agreement: Agreement
    redaction_count: int

    def to_creates(self, failure_id: int) -> list[FixCandidateCreate]:
        """Build one stored row per candidate, including the ones that did not apply.

        Args:
            failure_id: The failure these candidates belong to.

        Returns:
            Rows in candidate order.
        """
        return [candidate.to_create(failure_id) for candidate in self.candidates]


class FixGenerator:
    """Generate N patches from one failure context."""

    def __init__(
        self,
        client: LLMClient,
        *,
        candidates: int = 3,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        limits: ApplyLimits | None = None,
    ) -> None:
        """Store the client and the sampling caps.

        Args:
            client: Fixer role. Calls are awaited one at a time.
            candidates: How many patches to request. Default 3.
            temperature: Sampling temperature. 0 repeats one patch, so agreement cannot move.
            max_tokens: Upper bound on each completion.
            limits: How large a patch may be, and whether workflow files are editable.
        """
        if candidates < 1:
            raise ValueError("candidates must be >= 1")
        self._client = client
        self._candidates = candidates
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._limits = limits or ApplyLimits()

    async def generate(
        self,
        *,
        log: str | None,
        diff: str | None = None,
        files: dict[str, str],
        failure_kind: str | None = None,
    ) -> FixGeneration:
        """Generate and score patches for one failure.

        Args:
            log: Raw CI log. Redacted inside this method before it reaches the model.
            diff: Raw commit diff. Redacted the same way.
            files: Original path to file text. The model sees the redacted copies.
            failure_kind: CI hint such as `code`. Passed through as untrusted data.

        Returns:
            Every candidate and the agreement result. `failed` is true only when every
            model call raised. Invalid proposals are still listed.
        """
        context = prepare_context(log=log, diff=diff, files=files)
        messages = build_fixer_messages(
            log=context.log,
            diff=context.diff,
            files=context.files,
            failure_kind=failure_kind,
        )
        _require_redacted(messages[0].content)
        generated: list[GeneratedFix] = []
        call_failures = 0
        for index in range(self._candidates):
            try:
                raw = await self._client.complete(
                    messages,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                )
            except LLMError:
                call_failures += 1
                generated.append(_failed_call(index))
                continue
            generated.append(self._from_reply(index, raw, files))
        agreement = score_agreement(_views(generated))
        failed = call_failures == self._candidates
        return FixGeneration(
            failed=failed,
            failure_reason="all_calls_failed" if failed else None,
            candidates=tuple(generated),
            agreement=agreement,
            redaction_count=context.redaction_count,
        )

    def _from_reply(self, index: int, raw: str, files: dict[str, str]) -> GeneratedFix:
        """Parse one reply and apply it. A bad reply is an invalid candidate."""
        proposal, error = _parse_proposal(raw)
        if proposal is None:
            return _invalid(index, redact(raw).text, error or ApplyError.MALFORMED_EDITS.value)
        applied = apply_patch(
            file_path=proposal.file_path,
            edits=proposal.edits,
            files=files,
            limits=self._limits,
        )
        return _from_apply(index, proposal, applied)


def build_fix_generator(settings: Settings, client: LLMClient) -> FixGenerator:
    """Build a generator from `FIX_CANDIDATES`, `FIX_TEMPERATURE`, and the patch caps.

    Args:
        settings: Process settings.
        client: The fixer-role client from `build_llm_clients`.

    Returns:
        A generator that uses that client.
    """
    return FixGenerator(
        client,
        candidates=settings.fix_candidates,
        temperature=settings.fix_temperature,
        max_tokens=settings.fix_max_tokens,
        limits=ApplyLimits(
            max_edits=settings.fix_max_edits,
            max_changed_lines=settings.fix_max_changed_lines,
            allow_workflows=settings.fix_allow_workflows,
        ),
    )


def _parse_proposal(raw: str) -> tuple[FixProposal | None, str | None]:
    """Validate one reply. The error string is a stable code, not the model text."""
    try:
        payload = _json_object(strip_model_text(raw))
    except json.JSONDecodeError:
        return None, "invalid_json"
    try:
        return FixProposal.model_validate(payload), None
    except ValidationError:
        return None, "invalid_schema"


def _json_object(text: str) -> object:
    """Return the JSON value in `text`, or the first `{...}` object inside it."""
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(stripped[start : end + 1])


def _from_apply(index: int, proposal: FixProposal, applied: ApplyResult) -> GeneratedFix:
    """Copy an apply result onto a candidate. Outbound title and body are redacted."""
    title = redact_outbound(proposal.pr_title).text
    body = redact_outbound(proposal.pr_body).text
    edits = proposal.edits
    if applied.error is ApplyError.SECRET_IN_REPLACEMENT:
        edits = redact(edits).text
    return GeneratedFix(
        index=index,
        branch_name=proposal.branch_name,
        file_path=proposal.file_path,
        edits=edits,
        pr_title=str(title),
        pr_body=str(body),
        applied_ok=applied.applied_ok,
        error=None if applied.error is None else applied.error.value,
        diff=applied.diff,
        new_content=applied.new_content,
    )


def _failed_call(index: int) -> GeneratedFix:
    """A candidate recorded when the model call itself failed."""
    return _invalid(index, "", "llm_error")


def _invalid(index: int, edits: str, error: str) -> GeneratedFix:
    """A candidate that produced no patch."""
    return GeneratedFix(
        index=index,
        branch_name=None,
        file_path=_UNPARSED_PATH,
        edits=edits,
        pr_title=None,
        pr_body=None,
        applied_ok=False,
        error=error,
        diff=None,
        new_content=None,
    )


def _views(candidates: Sequence[GeneratedFix]) -> list[PatchCandidate]:
    """The slice of each candidate the agreement scorer needs."""
    return [
        PatchCandidate(
            index=candidate.index,
            file_path=candidate.file_path,
            diff=candidate.diff,
            applied_ok=candidate.applied_ok,
        )
        for candidate in candidates
    ]


def _require_redacted(value: RedactedText) -> None:
    """Refuse to send a prompt whose content was not built as `RedactedText`."""
    if type(value) is not RedactedText:
        raise TypeError("fixer prompt content must be RedactedText")
