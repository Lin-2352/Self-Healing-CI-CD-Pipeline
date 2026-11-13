"""Pairwise agreement, including invalid candidates and the medoid tie-break."""

import pytest

from app.core.agreement import Agreement, PatchCandidate, score_agreement
from app.core.patch_apply import apply_patch

_FILE = "def value():\n    return 1\n"


def _applied(
    index: int, replacement: str, path: str = "app.py", source: str = _FILE
) -> PatchCandidate:
    result = apply_patch(
        file_path=path,
        edits=f"<<<<<<< SEARCH\n    return 1\n=======\n    {replacement}\n>>>>>>> REPLACE\n",
        files={path: source},
    )
    assert result.applied_ok is True
    return PatchCandidate(index, path, result.diff, True)


def _invalid(index: int, path: str = "app.py") -> PatchCandidate:
    return PatchCandidate(index, path, None, False)


def test_identical_candidates_score_one() -> None:
    candidates = [_applied(0, "return 2"), _applied(1, "return 2"), _applied(2, "return 2")]

    agreement = score_agreement(candidates)

    assert agreement.status == "ok"
    assert agreement.score == 1
    assert agreement.chosen_index == 0


def test_divergent_candidates_score_below_one() -> None:
    candidates = [_applied(0, "return 2"), _applied(1, "return 3"), _applied(2, "raise SystemExit")]

    agreement = score_agreement(candidates)

    assert agreement.status == "ok"
    assert agreement.score is not None
    assert 0 <= agreement.score < 1


def test_one_invalid_candidate_stays_in_the_denominator() -> None:
    candidates = [_applied(0, "return 2"), _applied(1, "return 2"), _invalid(2)]

    agreement = score_agreement(candidates)

    assert agreement.status == "ok"
    assert agreement.score == pytest.approx(1 / 3)
    assert agreement.chosen_index == 0


def test_all_unapplicable_candidates_are_insufficient() -> None:
    agreement = score_agreement([_invalid(0), _invalid(1), _invalid(2)])

    assert agreement == Agreement(
        status="insufficient_candidates",
        score=None,
        chosen_index=None,
        explanation="insufficient candidates",
    )


def test_different_files_score_zero() -> None:
    candidates = [
        _applied(0, "return 2", path="a.py"),
        _applied(1, "return 2", path="b.py"),
    ]

    agreement = score_agreement(candidates)

    assert agreement.status == "ok"
    assert agreement.score == 0
    assert agreement.chosen_index == 0


def test_medoid_tie_breaks_to_the_lowest_index() -> None:
    candidates = [_applied(0, "return 2"), _applied(1, "raise SystemExit"), _applied(2, "return 2")]

    first = score_agreement(candidates)
    second = score_agreement(candidates)

    assert first.status == "ok"
    assert first.chosen_index == 0
    assert second.chosen_index == first.chosen_index
    assert first.score is not None
    assert 0 <= first.score <= 1
