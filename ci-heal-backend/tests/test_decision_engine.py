"""Table-driven coverage of the decision rules, plus the FIX invariant."""

import ast
import inspect
import math
from pathlib import Path

import pytest

from app.core.decision_engine import (
    Action,
    Decision,
    ReasonCode,
    Stage,
    Thresholds,
    decide,
)
from app.core.enums import FailureCategory

_FIXABLE = {
    FailureCategory.DEPENDENCY_ERROR.value,
    FailureCategory.CONFIGURATION_ERROR.value,
    FailureCategory.TEST_FAILURE.value,
}
_BELOW_CLASSIFY = math.nextafter(0.75, 0.0)
_BELOW_AGREEMENT = math.nextafter(0.6, 0.0)
_BELOW_OVERRIDE = math.nextafter(0.9, 0.0)

# name, reason, action, overrides of the default call (dependency, confidence 0.9, attempt 0).
_CASES: list[tuple[str, ReasonCode, Action, dict[str, object]]] = [
    ("confidence missing", ReasonCode.INVALID_INPUT, Action.ESCALATE, {"confidence": None}),
    ("confidence nan", ReasonCode.INVALID_INPUT, Action.ESCALATE, {"confidence": float("nan")}),
    ("confidence above 1", ReasonCode.INVALID_INPUT, Action.ESCALATE, {"confidence": 1.01}),
    ("confidence below 0", ReasonCode.INVALID_INPUT, Action.ESCALATE, {"confidence": -0.01}),
    ("attempt missing", ReasonCode.INVALID_INPUT, Action.ESCALATE, {"attempt": None}),
    ("retries missing", ReasonCode.INVALID_INPUT, Action.ESCALATE, {"retries_done": None}),
    ("attempt bool", ReasonCode.INVALID_INPUT, Action.ESCALATE, {"attempt": True}),
    (
        "agreement nan at stage 1",
        ReasonCode.INVALID_INPUT,
        Action.ESCALATE,
        {"agreement_score": float("nan")},
    ),
    (
        "agreement nan would otherwise fix",
        ReasonCode.INVALID_INPUT,
        Action.ESCALATE,
        {"stage": Stage.AGREEMENT, "confidence": 1.0, "agreement_score": float("nan")},
    ),
    (
        "unknown category",
        ReasonCode.UNKNOWN_CATEGORY,
        Action.ESCALATE,
        {"category": FailureCategory.UNKNOWN, "confidence": 1.0},
    ),
    ("category missing", ReasonCode.UNKNOWN_CATEGORY, Action.ESCALATE, {"category": None}),
    ("category blank", ReasonCode.UNKNOWN_CATEGORY, Action.ESCALATE, {"category": "  "}),
    (
        "unknown override cannot promote it",
        ReasonCode.UNKNOWN_CATEGORY,
        Action.ESCALATE,
        {
            "category": FailureCategory.UNKNOWN,
            "confidence": 0.0,
            "thresholds": Thresholds(by_category={"unknown": 0.0}),
        },
    ),
    (
        "attempt cap beats a perfect fix",
        ReasonCode.HEAL_ATTEMPTS_EXHAUSTED,
        Action.ESCALATE,
        {
            "stage": Stage.AGREEMENT,
            "attempt": 3,
            "confidence": 1.0,
            "agreement_score": 1.0,
        },
    ),
    (
        "attempt cap beats a flaky retry",
        ReasonCode.HEAL_ATTEMPTS_EXHAUSTED,
        Action.ESCALATE,
        {
            "category": FailureCategory.FLAKY_OR_NETWORK,
            "attempt": 3,
            "confidence": 1.0,
            "retries_done": 0,
        },
    ),
    (
        "flaky at the gate",
        ReasonCode.FLAKY_RETRY,
        Action.RETRY,
        {
            "category": FailureCategory.FLAKY_OR_NETWORK,
            "confidence": 0.75,
            "retries_done": 0,
        },
    ),
    (
        "flaky just below the gate",
        ReasonCode.FLAKY_BELOW_THRESHOLD,
        Action.ESCALATE,
        {"category": FailureCategory.FLAKY_OR_NETWORK, "confidence": _BELOW_CLASSIFY},
    ),
    (
        "flaky retries exhausted",
        ReasonCode.FLAKY_RETRIES_EXHAUSTED,
        Action.ESCALATE,
        {
            "category": FailureCategory.FLAKY_OR_NETWORK,
            "confidence": 1.0,
            "retries_done": 1,
        },
    ),
    (
        "exhausted retry is reported before low confidence",
        ReasonCode.FLAKY_RETRIES_EXHAUSTED,
        Action.ESCALATE,
        {
            "category": FailureCategory.FLAKY_OR_NETWORK,
            "confidence": 0.1,
            "retries_done": 1,
        },
    ),
    (
        "stage 2 does not turn a flake into a fix",
        ReasonCode.FLAKY_RETRY,
        Action.RETRY,
        {
            "stage": Stage.AGREEMENT,
            "category": FailureCategory.FLAKY_OR_NETWORK,
            "confidence": 0.9,
            "agreement_score": 1.0,
        },
    ),
    (
        "flaky override lowers only that gate",
        ReasonCode.FLAKY_RETRY,
        Action.RETRY,
        {
            "category": FailureCategory.FLAKY_OR_NETWORK,
            "confidence": 0.5,
            "thresholds": Thresholds(by_category={"flaky_or_network": 0.5}),
        },
    ),
    (
        "classify just below even with perfect agreement",
        ReasonCode.BELOW_CLASSIFY_THRESHOLD,
        Action.ESCALATE,
        {
            "stage": Stage.AGREEMENT,
            "confidence": _BELOW_CLASSIFY,
            "agreement_score": 1.0,
        },
    ),
    (
        "classify gate is inclusive at stage 1",
        ReasonCode.CANDIDATE_FIX,
        Action.CANDIDATE_FIX,
        {"confidence": 0.75, "agreement_score": 0.0},
    ),
    (
        "stage 1 ignores a low agreement score",
        ReasonCode.CANDIDATE_FIX,
        Action.CANDIDATE_FIX,
        {"confidence": 0.9, "agreement_score": 0.0},
    ),
    (
        "agreement missing at stage 2",
        ReasonCode.AGREEMENT_MISSING,
        Action.ESCALATE,
        {"stage": Stage.AGREEMENT, "confidence": 0.9, "agreement_score": None},
    ),
    (
        "agreement just below the gate",
        ReasonCode.BELOW_AGREEMENT_THRESHOLD,
        Action.ESCALATE,
        {
            "stage": Stage.AGREEMENT,
            "confidence": 0.9,
            "agreement_score": _BELOW_AGREEMENT,
        },
    ),
    (
        "both gates inclusive and attempt just under the cap",
        ReasonCode.FIX,
        Action.FIX,
        {
            "stage": Stage.AGREEMENT,
            "confidence": 0.75,
            "agreement_score": 0.6,
            "attempt": 2,
        },
    ),
    (
        "configuration error can fix",
        ReasonCode.FIX,
        Action.FIX,
        {
            "category": FailureCategory.CONFIGURATION_ERROR,
            "stage": Stage.AGREEMENT,
            "confidence": 0.75,
            "agreement_score": 0.6,
        },
    ),
    (
        "test failure can fix",
        ReasonCode.FIX,
        Action.FIX,
        {
            "category": FailureCategory.TEST_FAILURE,
            "stage": Stage.AGREEMENT,
            "confidence": 0.75,
            "agreement_score": 0.6,
        },
    ),
    (
        "category override is inclusive",
        ReasonCode.CANDIDATE_FIX,
        Action.CANDIDATE_FIX,
        {
            "category": FailureCategory.TEST_FAILURE,
            "confidence": 0.9,
            "thresholds": Thresholds(by_category={"test_failure": 0.9}),
        },
    ),
    (
        "category override just below escalates",
        ReasonCode.BELOW_CLASSIFY_THRESHOLD,
        Action.ESCALATE,
        {
            "category": FailureCategory.TEST_FAILURE,
            "confidence": _BELOW_OVERRIDE,
            "thresholds": Thresholds(by_category={"test_failure": 0.9}),
        },
    ),
    (
        "a test-failure override does not raise the dependency gate",
        ReasonCode.CANDIDATE_FIX,
        Action.CANDIDATE_FIX,
        {
            "category": FailureCategory.DEPENDENCY_ERROR,
            "confidence": 0.8,
            "thresholds": Thresholds(by_category={"test_failure": 0.9}),
        },
    ),
    (
        "unrecognised category",
        ReasonCode.NO_RULE_MATCHED,
        Action.ESCALATE,
        {
            "category": "other",
            "stage": Stage.AGREEMENT,
            "confidence": 1.0,
            "agreement_score": 1.0,
        },
    ),
]


def _decide(**overrides: object) -> Decision:
    base: dict[str, object] = {
        "stage": Stage.CLASSIFICATION,
        "category": FailureCategory.DEPENDENCY_ERROR,
        "confidence": 0.9,
        "attempt": 0,
        "retries_done": 0,
        "agreement_score": None,
        "thresholds": Thresholds(),
    }
    base.update(overrides)
    return decide(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("name", "reason", "action", "overrides"), _CASES, ids=[c[0] for c in _CASES]
)
def test_rules(name: str, reason: ReasonCode, action: Action, overrides: dict[str, object]) -> None:
    decision = _decide(**overrides)
    thresholds = overrides.get("thresholds", Thresholds())
    assert isinstance(thresholds, Thresholds)

    assert decision.reason_code is reason, name
    assert decision.action is action
    assert decision.stage == overrides.get("stage", Stage.CLASSIFICATION)
    assert decision.explanation
    assert decision.thresholds_snapshot["classify_threshold"] == thresholds.classify


def test_every_reason_code_has_a_row() -> None:
    assert {reason for _, reason, _, _ in _CASES} == set(ReasonCode)


def test_failure_kind_is_not_an_input_and_the_engine_imports_neither_settings_nor_a_clock() -> None:
    assert "failure_kind" not in inspect.signature(decide).parameters
    source = Path(inspect.getfile(decide)).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "app.config" not in imported
    assert "datetime" not in imported
    assert "time" not in imported


def test_thresholds_reject_a_gate_outside_the_unit_interval() -> None:
    with pytest.raises(ValueError, match="classify"):
        Thresholds(classify=1.5)
    with pytest.raises(ValueError, match="unknown category"):
        Thresholds(by_category={"not_a_category": 0.5})


def test_fix_requires_a_fixable_category_both_gates_and_room_under_the_cap() -> None:
    """FIX is impossible unless the category is fixable, both gates pass, and attempt < cap."""
    categories: list[FailureCategory | str] = [*list(FailureCategory), "other"]
    confidences = [None, float("nan"), -0.01, 0.0, _BELOW_CLASSIFY, 0.75, 1.0, 1.01]
    agreements = [None, float("nan"), 0.0, _BELOW_AGREEMENT, 0.6, 1.0]
    attempts = [None, -1, 0, 2, 3]
    configs = (
        Thresholds(),
        Thresholds(by_category={"test_failure": 0.9, "flaky_or_network": 0.4}),
    )

    for thresholds in configs:
        for category in categories:
            for confidence in confidences:
                for agreement in agreements:
                    for attempt in attempts:
                        for retries_done in (0, 1):
                            for stage in (Stage.CLASSIFICATION, Stage.AGREEMENT):
                                decision = decide(
                                    stage=stage,
                                    category=category,
                                    confidence=confidence,
                                    attempt=attempt,
                                    retries_done=retries_done,
                                    agreement_score=agreement,
                                    thresholds=thresholds,
                                )
                                _assert_fix_invariant(
                                    decision,
                                    stage=stage,
                                    category=category,
                                    confidence=confidence,
                                    agreement=agreement,
                                    attempt=attempt,
                                    retries_done=retries_done,
                                    thresholds=thresholds,
                                )


def _assert_fix_invariant(
    decision: Decision,
    *,
    stage: Stage,
    category: FailureCategory | str,
    confidence: float | None,
    agreement: float | None,
    attempt: int | None,
    retries_done: int,
    thresholds: Thresholds,
) -> None:
    """Restate the spec: a FIX row has to clear every gate the engine claims to check."""
    label = category.value if isinstance(category, FailureCategory) else category
    if decision.action is Action.FIX:
        assert stage is Stage.AGREEMENT
        assert label in _FIXABLE
        assert _usable_score(confidence)
        assert confidence is not None
        assert confidence >= _spec_gate(thresholds, label)
        assert _usable_score(agreement)
        assert agreement is not None
        assert agreement >= thresholds.agreement
        assert isinstance(attempt, int) and not isinstance(attempt, bool)
        assert attempt < thresholds.max_heal_attempts
    if decision.action is Action.CANDIDATE_FIX:
        assert stage is Stage.CLASSIFICATION
        assert label in _FIXABLE
    if decision.action is Action.RETRY:
        assert label == FailureCategory.FLAKY_OR_NETWORK.value
        assert isinstance(attempt, int) and not isinstance(attempt, bool)
        assert attempt < thresholds.max_heal_attempts
        assert retries_done < thresholds.max_retries
        assert _usable_score(confidence)
        assert confidence is not None
        assert confidence >= _spec_gate(thresholds, label)


def _spec_gate(thresholds: Thresholds, category: str) -> float:
    """The confidence gate the tests expect, written out rather than copied from the engine."""
    if category in thresholds.by_category:
        return float(thresholds.by_category[category])
    if category == FailureCategory.FLAKY_OR_NETWORK.value:
        return thresholds.flaky
    return thresholds.classify


def _usable_score(value: object) -> bool:
    """True for a real number in [0, 1]. NaN and bool fail."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    number = float(value)
    return not math.isnan(number) and 0.0 <= number <= 1.0
