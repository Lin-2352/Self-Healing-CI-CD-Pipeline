"""Classify a CI failure into one category, with optional self-consistency.

Samples are taken one after another. Each sample may be re-asked when its JSON does not
match the schema, up to `CLASSIFIER_MAX_RETRIES`. If every sample fails, the verdict is
`unknown` with confidence 0, which the decision engine treats as ESCALATE.

With more than one valid sample the stored confidence is the smaller of two signals:
the mean of the self-reported confidences, and the fraction of samples that voted for
the winning category (invalid samples count in that fraction, so a parse failure lowers
it). Each sample is kept unchanged, so evaluation can compare the two signals later.
A tie has no winning category and falls back to `unknown`.
"""

import json
import time
from dataclasses import dataclass

from pydantic import ValidationError

from app.config import Settings
from app.core.enums import FailureCategory
from app.core.redacted import RedactedText
from app.llm.base import ChatMessage, LLMClient
from app.llm.prompts import build_classifier_messages, build_retry_message
from app.schemas.classification import (
    ClassificationCreate,
    ClassificationSample,
    ClassifierOutput,
)
from app.services.secret_redactor import redact


@dataclass(frozen=True)
class ClassificationResult:
    """The aggregate verdict plus every sample that produced it."""

    category: FailureCategory
    confidence: float
    reasoning: str
    model: str
    raw_output: str
    samples: tuple[ClassificationSample, ...]
    parse_attempts: int
    latency_ms: int

    def to_create(self, failure_id: int) -> ClassificationCreate:
        """Build the row the repository stores for this verdict.

        Args:
            failure_id: The failure this classification belongs to.

        Returns:
            A create model, including every sample.
        """
        return ClassificationCreate(
            failure_id=failure_id,
            category=self.category,
            confidence=self.confidence,
            reasoning=self.reasoning,
            model=self.model,
            raw_output=self.raw_output,
            samples=list(self.samples),
            parse_attempts=self.parse_attempts,
            latency_ms=self.latency_ms,
        )


class Classifier:
    """Ask the classifier model what kind of failure this is."""

    def __init__(
        self,
        client: LLMClient,
        *,
        model: str,
        samples: int = 1,
        temperature: float = 0.0,
        max_retries: int = 2,
        max_tokens: int = 512,
    ) -> None:
        """Store the client and the sampling settings.

        Args:
            client: Classifier role. Calls are awaited one at a time.
            model: Name recorded on the verdict. Not sent again; the client already has it.
            samples: How many times to classify. 1 by default, 3 for an eval or a demo.
            temperature: Sampling temperature, shared by every sample.
            max_retries: Re-asks after a sample fails schema validation. Not HTTP retries.
            max_tokens: Upper bound on each completion.
        """
        if samples < 1:
            raise ValueError("samples must be >= 1")
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self._client = client
        self._model = model
        self._samples = samples
        self._temperature = temperature
        self._max_retries = max_retries
        self._max_tokens = max_tokens

    async def classify(
        self,
        *,
        log: RedactedText,
        diff: RedactedText | None = None,
        failure_kind: str | None = None,
    ) -> ClassificationResult:
        """Classify one failure.

        Args:
            log: Redacted log from `prepare_log` or `prepare_context`.
            diff: Redacted diff. Omitted when the caller has no diff yet.
            failure_kind: CI hint such as `code` or an exit-75 infra label. Passed through
                as untrusted data, not as a skip.

        Returns:
            The aggregate verdict. `category=unknown` and `confidence=0` means parsing
            failed or the samples tied; the decision engine escalates that case.
        """
        _require_redacted(log, "log")
        redacted_diff = diff if diff is not None else redact("").text
        _require_redacted(redacted_diff, "diff")
        started = time.perf_counter()
        base = build_classifier_messages(log=log, diff=redacted_diff, failure_kind=failure_kind)
        samples: list[ClassificationSample] = []
        for index in range(self._samples):
            samples.append(await self._one_sample(index, list(base)))
        return _aggregate(samples, model=self._model, started=started)

    async def _one_sample(
        self,
        index: int,
        messages: list[ChatMessage],
    ) -> ClassificationSample:
        """Take one sample, re-asking until it parses or the re-ask budget is spent."""
        last_raw = ""
        attempts = self._max_retries + 1
        for attempt in range(attempts):
            last_raw = await self._client.complete(
                messages,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
            try:
                parsed = parse_classifier_output(last_raw)
            except (json.JSONDecodeError, ValidationError):
                if attempt >= self._max_retries:
                    break
                messages.append(build_retry_message(last_raw))
                continue
            return ClassificationSample(
                sample_index=index,
                category=FailureCategory(parsed.category),
                confidence=parsed.confidence,
                reasoning=parsed.reasoning,
                raw_output=last_raw,
                parse_status="ok",
                parse_attempts=attempt + 1,
            )
        return ClassificationSample(
            sample_index=index,
            raw_output=last_raw,
            parse_status="invalid",
            parse_attempts=attempts,
        )


def build_classifier(settings: Settings, client: LLMClient) -> Classifier:
    """Build a classifier from `CLASSIFIER_SAMPLES` and the related settings.

    Args:
        settings: Process settings. The sample count, temperature, and parse retries live here.
        client: The classifier-role client from `build_llm_clients`.

    Returns:
        A classifier that uses that client.
    """
    return Classifier(
        client,
        model=settings.classifier_llm_model,
        samples=settings.classifier_samples,
        temperature=settings.classifier_temperature,
        max_retries=settings.classifier_max_retries,
        max_tokens=settings.classifier_max_tokens,
    )


def parse_classifier_output(text: str) -> ClassifierOutput:
    """Validate one model reply against the classifier schema.

    Args:
        text: Assistant text. A JSON object may be wrapped in a short preamble.

    Returns:
        The validated object.

    Raises:
        json.JSONDecodeError: The reply contains no JSON object.
        ValidationError: The object is not the required schema.
    """
    return ClassifierOutput.model_validate(_json_object(text))


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


def _require_redacted(value: RedactedText, name: str) -> None:
    """Reject a plain string so an unredacted log cannot be classified by accident."""
    if type(value) is not RedactedText:
        raise TypeError(f"{name} must be RedactedText from the redactor")


def _aggregate(
    samples: list[ClassificationSample],
    *,
    model: str,
    started: float,
) -> ClassificationResult:
    """Combine samples into one verdict. See the module docstring for the rule."""
    latency_ms = max(0, int((time.perf_counter() - started) * 1000))
    attempts = sum(sample.parse_attempts for sample in samples)
    valid = [sample for sample in samples if sample.parse_status == "ok" and sample.category]
    if not valid:
        return _fallback(
            samples,
            model=model,
            attempts=attempts,
            latency_ms=latency_ms,
            reasoning="No classifier sample matched the required JSON schema.",
        )
    counts: dict[FailureCategory, int] = {}
    for sample in valid:
        category = sample.category
        if category is None:
            continue
        counts[category] = counts.get(category, 0) + 1
    top = max(counts.values())
    leaders = [category for category, count in counts.items() if count == top]
    if len(leaders) != 1:
        return _fallback(
            samples,
            model=model,
            attempts=attempts,
            latency_ms=latency_ms,
            reasoning="Classifier samples tied, so there is no majority category.",
        )
    category = leaders[0]
    mean = sum(sample.confidence or 0.0 for sample in valid) / len(valid)
    fraction = top / len(samples)
    chosen = next(sample for sample in valid if sample.category == category)
    return ClassificationResult(
        category=category,
        confidence=min(mean, fraction),
        reasoning=chosen.reasoning or "",
        model=model,
        raw_output=chosen.raw_output,
        samples=tuple(samples),
        parse_attempts=attempts,
        latency_ms=latency_ms,
    )


def _fallback(
    samples: list[ClassificationSample],
    *,
    model: str,
    attempts: int,
    latency_ms: int,
    reasoning: str,
) -> ClassificationResult:
    """Return the escalate-bound verdict used when samples do not agree."""
    raw = samples[-1].raw_output if samples else ""
    return ClassificationResult(
        category=FailureCategory.UNKNOWN,
        confidence=0.0,
        reasoning=reasoning,
        model=model,
        raw_output=raw,
        samples=tuple(samples),
        parse_attempts=attempts,
        latency_ms=latency_ms,
    )
