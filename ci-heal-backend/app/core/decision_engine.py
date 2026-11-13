"""Pure RETRY / FIX / ESCALATE rules.

No database, no clock, and no settings. The caller passes a `Thresholds` object.
`failure_kind` is not an input: a CI hint must not change the action.

First match wins:

1. Category is missing or `unknown`, or a score/count is missing, NaN, or outside
   its range. Scores must be in [0, 1]. Counts (`attempt`, `retries_done`) must be
   integers >= 0. `agreement_score=None` is allowed at stage 1; a present score that
   is NaN or outside [0, 1] is invalid at either stage.
2. `attempt >= max_heal_attempts` -> ESCALATE.
3. `flaky_or_network`: RETRY when `retries_done < max_retries` and
   `confidence >=` the flaky gate, otherwise ESCALATE. An exhausted retry is reported
   before a low confidence, when both are true.
4. Fixable categories (`dependency_error`, `configuration_error`, `test_failure`):
   stage 1 returns CANDIDATE_FIX when `confidence >=` the classify gate, else ESCALATE.
   Stage 2 also needs `agreement_score >=` the agreement gate to return FIX.
   Stage 2 with no agreement score returns ESCALATE. Stage 1 ignores agreement.
5. Any other category -> ESCALATE.

Comparisons are inclusive (`>=`). A per-category entry in `Thresholds.by_category`
replaces that category's confidence gate only. It does not change the agreement gate.
Stage 2 does not turn a flaky result into FIX; rule 3 still matches first.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from types import MappingProxyType

from app.core.enums import FailureCategory

_FIXABLE = frozenset(
    {
        FailureCategory.DEPENDENCY_ERROR.value,
        FailureCategory.CONFIGURATION_ERROR.value,
        FailureCategory.TEST_FAILURE.value,
    }
)
_FLAKY = FailureCategory.FLAKY_OR_NETWORK.value
_UNKNOWN = FailureCategory.UNKNOWN.value
_CATEGORIES = frozenset(item.value for item in FailureCategory)


class Stage(IntEnum):
    """Which inputs the caller has. Stage 2 is classification plus an agreement score."""

    CLASSIFICATION = 1
    AGREEMENT = 2


class Action(StrEnum):
    """What the engine decided. `candidate_fix` is stage 1 only and is not stored as an action."""

    RETRY = "retry"
    ESCALATE = "escalate"
    CANDIDATE_FIX = "candidate_fix"
    FIX = "fix"


class ReasonCode(StrEnum):
    """Which rule fired. The explanation is the human-readable form of the same fact."""

    INVALID_INPUT = "invalid_input"
    UNKNOWN_CATEGORY = "unknown_category"
    HEAL_ATTEMPTS_EXHAUSTED = "heal_attempts_exhausted"
    FLAKY_RETRY = "flaky_retry"
    FLAKY_RETRIES_EXHAUSTED = "flaky_retries_exhausted"
    FLAKY_BELOW_THRESHOLD = "flaky_below_threshold"
    BELOW_CLASSIFY_THRESHOLD = "below_classify_threshold"
    CANDIDATE_FIX = "candidate_fix"
    AGREEMENT_MISSING = "agreement_missing"
    BELOW_AGREEMENT_THRESHOLD = "below_agreement_threshold"
    FIX = "fix"
    NO_RULE_MATCHED = "no_rule_matched"


@dataclass(frozen=True)
class Thresholds:
    """Gates for one call. Built from settings at the edge, then passed in by value."""

    classify: float = 0.75
    agreement: float = 0.6
    flaky: float = 0.75
    max_retries: int = 1
    max_heal_attempts: int = 3
    by_category: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Reject a gate outside [0, 1] or a category name the classifier cannot emit."""
        _require_unit("classify", self.classify)
        _require_unit("agreement", self.agreement)
        _require_unit("flaky", self.flaky)
        _require_count("max_retries", self.max_retries, minimum=0)
        _require_count("max_heal_attempts", self.max_heal_attempts, minimum=1)
        cleaned: dict[str, float] = {}
        for key, value in self.by_category.items():
            if key not in _CATEGORIES:
                raise ValueError(f"unknown category threshold: {key}")
            _require_unit(key, value)
            cleaned[key] = float(value)
        object.__setattr__(self, "by_category", MappingProxyType(cleaned))

    def confidence_gate(self, category: str) -> float:
        """Return the confidence required for this category, including any override.

        Args:
            category: A category label. Unknown labels use the classify default.

        Returns:
            The inclusive minimum confidence for that category.
        """
        override = self.by_category.get(category)
        if override is not None:
            return float(override)
        if category == _FLAKY:
            return self.flaky
        return self.classify


@dataclass(frozen=True)
class Decision:
    """The engine's answer. Frozen, and safe to store or show on the dashboard."""

    action: Action
    stage: Stage
    reason_code: ReasonCode
    explanation: str
    thresholds_snapshot: Mapping[str, object]


def decide(
    *,
    stage: Stage | int,
    category: FailureCategory | str | None,
    confidence: float | None,
    attempt: int | None,
    retries_done: int | None,
    thresholds: Thresholds,
    agreement_score: float | None = None,
) -> Decision:
    """Apply the rules and return the first match.

    Args:
        stage: 1 when only the classification is known, 2 once agreement is measured.
        category: Classifier label. `unknown` always escalates.
        confidence: Combined classifier confidence, in [0, 1].
        attempt: Heal attempts already used on this failure, including this run's trailer.
        retries_done: Flaky retries already requested for this failure.
        thresholds: Gates for this call.
        agreement_score: Patch agreement in [0, 1], or None when it has not been measured.

    Returns:
        A frozen decision. FIX is only possible at stage 2 for a fixable category that
        clears both gates with `attempt` still under the cap.
    """
    resolved_stage = _read_stage(stage)
    if resolved_stage is None:
        return _decision(
            action=Action.ESCALATE,
            stage=Stage.CLASSIFICATION,
            reason=ReasonCode.INVALID_INPUT,
            explanation="stage must be 1 or 2.",
            thresholds=thresholds,
            category=None,
        )

    label, category_problem = _read_category(category)
    if category_problem == "invalid":
        return _invalid(resolved_stage, "category is not a label.", thresholds, label)
    if category_problem == "unknown":
        return _decision(
            action=Action.ESCALATE,
            stage=resolved_stage,
            reason=ReasonCode.UNKNOWN_CATEGORY,
            explanation="Category unknown has no automatic action.",
            thresholds=thresholds,
            category=_UNKNOWN,
        )

    score_problem = _score_problem(confidence, "confidence")
    if score_problem is not None:
        return _invalid(resolved_stage, score_problem, thresholds, label)
    if _count_problem(attempt):
        return _invalid(resolved_stage, "attempt must be an integer >= 0.", thresholds, label)
    if _count_problem(retries_done):
        return _invalid(resolved_stage, "retries_done must be an integer >= 0.", thresholds, label)
    if agreement_score is not None:
        agreement_problem = _score_problem(agreement_score, "agreement_score")
        if agreement_problem is not None:
            return _invalid(resolved_stage, agreement_problem, thresholds, label)

    if label is None or confidence is None or attempt is None or retries_done is None:
        return _invalid(resolved_stage, "A required input was missing.", thresholds, label)
    confidence_value = float(confidence)
    attempt_value = attempt
    retries_value = retries_done
    gate = thresholds.confidence_gate(label)

    if attempt_value >= thresholds.max_heal_attempts:
        return _decision(
            action=Action.ESCALATE,
            stage=resolved_stage,
            reason=ReasonCode.HEAL_ATTEMPTS_EXHAUSTED,
            explanation=(
                f"Heal attempt {attempt_value} has reached the cap of "
                f"{thresholds.max_heal_attempts}."
            ),
            thresholds=thresholds,
            category=label,
        )

    if label == _FLAKY:
        return _flaky(
            stage=resolved_stage,
            confidence=confidence_value,
            retries_done=retries_value,
            gate=gate,
            thresholds=thresholds,
        )

    if label in _FIXABLE:
        return _fixable(
            stage=resolved_stage,
            label=label,
            confidence=confidence_value,
            agreement_score=agreement_score,
            gate=gate,
            thresholds=thresholds,
        )

    return _decision(
        action=Action.ESCALATE,
        stage=resolved_stage,
        reason=ReasonCode.NO_RULE_MATCHED,
        explanation=f"Category {label} has no retry or fix rule.",
        thresholds=thresholds,
        category=label,
    )


def _flaky(
    *,
    stage: Stage,
    confidence: float,
    retries_done: int,
    gate: float,
    thresholds: Thresholds,
) -> Decision:
    """Rule 3. Retry only when a retry remains and confidence clears the flaky gate."""
    if retries_done >= thresholds.max_retries:
        return _decision(
            action=Action.ESCALATE,
            stage=stage,
            reason=ReasonCode.FLAKY_RETRIES_EXHAUSTED,
            explanation=(
                f"Flaky retries are exhausted ({retries_done} of {thresholds.max_retries})."
            ),
            thresholds=thresholds,
            category=_FLAKY,
        )
    if confidence < gate:
        return _decision(
            action=Action.ESCALATE,
            stage=stage,
            reason=ReasonCode.FLAKY_BELOW_THRESHOLD,
            explanation=(f"Flaky confidence {_fmt(confidence)} is below the gate {_fmt(gate)}."),
            thresholds=thresholds,
            category=_FLAKY,
        )
    return _decision(
        action=Action.RETRY,
        stage=stage,
        reason=ReasonCode.FLAKY_RETRY,
        explanation=(
            f"Flaky confidence {_fmt(confidence)} still has a retry "
            f"({retries_done} of {thresholds.max_retries} used)."
        ),
        thresholds=thresholds,
        category=_FLAKY,
    )


def _fixable(
    *,
    stage: Stage,
    label: str,
    confidence: float,
    agreement_score: float | None,
    gate: float,
    thresholds: Thresholds,
) -> Decision:
    """Rule 4. Stage 1 can only nominate a fix. Stage 2 accepts it when agreement clears."""
    if confidence < gate:
        return _decision(
            action=Action.ESCALATE,
            stage=stage,
            reason=ReasonCode.BELOW_CLASSIFY_THRESHOLD,
            explanation=(f"Confidence {_fmt(confidence)} is below the {label} gate {_fmt(gate)}."),
            thresholds=thresholds,
            category=label,
        )
    if stage is Stage.CLASSIFICATION:
        return _decision(
            action=Action.CANDIDATE_FIX,
            stage=stage,
            reason=ReasonCode.CANDIDATE_FIX,
            explanation=(
                f"Confidence {_fmt(confidence)} clears the {label} gate {_fmt(gate)}. "
                "Agreement has not been measured yet."
            ),
            thresholds=thresholds,
            category=label,
        )
    if agreement_score is None:
        return _decision(
            action=Action.ESCALATE,
            stage=stage,
            reason=ReasonCode.AGREEMENT_MISSING,
            explanation="Agreement was not measured, so there is no patch to apply.",
            thresholds=thresholds,
            category=label,
        )
    if float(agreement_score) < thresholds.agreement:
        return _decision(
            action=Action.ESCALATE,
            stage=stage,
            reason=ReasonCode.BELOW_AGREEMENT_THRESHOLD,
            explanation=(
                f"Agreement {_fmt(float(agreement_score))} is below the gate "
                f"{_fmt(thresholds.agreement)}."
            ),
            thresholds=thresholds,
            category=label,
        )
    return _decision(
        action=Action.FIX,
        stage=stage,
        reason=ReasonCode.FIX,
        explanation=(
            f"Confidence {_fmt(confidence)} and agreement {_fmt(float(agreement_score))} "
            f"both clear the {label} gates."
        ),
        thresholds=thresholds,
        category=label,
    )


def _decision(
    *,
    action: Action,
    stage: Stage,
    reason: ReasonCode,
    explanation: str,
    thresholds: Thresholds,
    category: str | None,
) -> Decision:
    """Build a frozen decision, including a copy of the gates that produced it."""
    return Decision(
        action=action,
        stage=stage,
        reason_code=reason,
        explanation=explanation,
        thresholds_snapshot=_snapshot(thresholds, category),
    )


def _invalid(
    stage: Stage, explanation: str, thresholds: Thresholds, category: str | None
) -> Decision:
    """Rule 1 for a score or count that cannot be compared."""
    return _decision(
        action=Action.ESCALATE,
        stage=stage,
        reason=ReasonCode.INVALID_INPUT,
        explanation=explanation,
        thresholds=thresholds,
        category=category,
    )


def _read_stage(stage: Stage | int) -> Stage | None:
    """Return a stage of 1 or 2, or None when the value is not one of those."""
    if isinstance(stage, bool):
        return None
    try:
        return Stage(stage)
    except ValueError:
        return None


def _read_category(category: object) -> tuple[str | None, str]:
    """Return the label and whether rule 1 should stop on it.

    Returns:
        `(label, "ok")` for a usable label, `("unknown", "unknown")` for the fallback
        category, or `(None, "invalid")` when the value is not a category string.
    """
    if category is None:
        return None, "unknown"
    if isinstance(category, FailureCategory):
        if category is FailureCategory.UNKNOWN:
            return None, "unknown"
        return category.value, "ok"
    if isinstance(category, str):
        label = category.strip()
        if label == "" or label == _UNKNOWN:
            return None, "unknown"
        return label, "ok"
    return None, "invalid"


def _score_problem(value: object, field_name: str) -> str | None:
    """Return an explanation when `value` is not a real number in [0, 1]."""
    if value is None:
        return f"{field_name} was missing."
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return f"{field_name} was not a number."
    number = float(value)
    if math.isnan(number) or math.isinf(number) or number < 0.0 or number > 1.0:
        return f"{field_name} was NaN or outside 0 to 1."
    return None


def _count_problem(value: object) -> bool:
    """True when a count is missing, a bool, or negative. Bool is an int in Python."""
    if isinstance(value, bool) or not isinstance(value, int):
        return True
    return value < 0


def _require_unit(name: str, value: object) -> None:
    """Raise when a configured gate is not a real number in [0, 1]."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number from 0 to 1")
    number = float(value)
    if math.isnan(number) or math.isinf(number) or number < 0.0 or number > 1.0:
        raise ValueError(f"{name} must be a number from 0 to 1")


def _require_count(name: str, value: object, *, minimum: int) -> None:
    """Raise when a configured cap is not an integer at or above `minimum`."""
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _snapshot(thresholds: Thresholds, category: str | None) -> Mapping[str, object]:
    """Copy the gates so a later change to the config object cannot rewrite history."""
    effective = None if category is None else thresholds.confidence_gate(category)
    return MappingProxyType(
        {
            "classify_threshold": thresholds.classify,
            "agreement_threshold": thresholds.agreement,
            "flaky_threshold": thresholds.flaky,
            "max_retries": thresholds.max_retries,
            "max_heal_attempts": thresholds.max_heal_attempts,
            "category_thresholds": MappingProxyType(dict(thresholds.by_category)),
            "effective_confidence_threshold": effective,
        }
    )


def _fmt(value: float) -> str:
    """Format a gate or a score for an explanation."""
    return f"{value:.2f}"
