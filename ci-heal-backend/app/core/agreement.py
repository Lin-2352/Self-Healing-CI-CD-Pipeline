"""Score how much a set of patch candidates agree with each other.

The score is the mean pairwise similarity. Each pair is the mean of token-Jaccard and
`difflib.SequenceMatcher.ratio` on a normalised unified diff. An invalid candidate has
similarity 0 with every other candidate and still occupies those pairs, so it pulls the
mean down. Two applicable candidates that edit different files also score 0.

Fewer than two applicable candidates returns "insufficient candidates" and no score.
The chosen patch is the medoid among the applicable candidates: the one with the highest
mean similarity to the others. A tie keeps the lowest index. The score stays in [0, 1].
"""

import difflib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class PatchCandidate:
    """One candidate as the scorer sees it. `diff` is ignored unless `applied_ok` is true."""

    index: int
    file_path: str
    diff: str | None
    applied_ok: bool


@dataclass(frozen=True)
class Agreement:
    """The score for a whole set, plus the medoid when there is one."""

    status: Literal["ok", "insufficient_candidates"]
    score: float | None
    chosen_index: int | None
    explanation: str


def score_agreement(candidates: Sequence[PatchCandidate]) -> Agreement:
    """Score `candidates` and pick the medoid.

    Args:
        candidates: Every generated candidate, including the ones that did not apply.
            Indexes should be unique. Order is not required.

    Returns:
        `insufficient_candidates` when fewer than two candidates applied. Otherwise a
        score in [0, 1] and the chosen index.
    """
    applicable = [candidate for candidate in candidates if _applicable(candidate)]
    if len(applicable) < 2:
        return Agreement(
            status="insufficient_candidates",
            score=None,
            chosen_index=None,
            explanation="insufficient candidates",
        )

    pairs = _pairs(candidates)
    score = _clamp(sum(pairs.values()) / len(pairs))
    chosen = _medoid(applicable, pairs, len(candidates))
    return Agreement(
        status="ok",
        score=score,
        chosen_index=chosen,
        explanation=f"Agreement {score:.2f}. Chosen candidate {chosen}.",
    )


def normalise_diff(diff: str) -> str:
    """Drop diff headers and surrounding whitespace so two encodings of one change match.

    Args:
        diff: A unified diff.

    Returns:
        The remaining change lines, one per line, with each line stripped.
    """
    kept: list[str] = []
    for line in diff.splitlines():
        stripped = line.strip()
        if stripped == "" or stripped.startswith(("diff ", "index ", "---", "+++", "@@")):
            continue
        kept.append(stripped)
    return "\n".join(kept)


def _applicable(candidate: PatchCandidate) -> bool:
    """True when this candidate produced a diff that can be compared."""
    return candidate.applied_ok and bool(candidate.diff)


def _pairs(candidates: Sequence[PatchCandidate]) -> dict[tuple[int, int], float]:
    """Similarity for every unordered pair, keyed by the two candidate indexes."""
    pairs: dict[tuple[int, int], float] = {}
    for left in range(len(candidates)):
        for right in range(left + 1, len(candidates)):
            first = candidates[left]
            second = candidates[right]
            key = (first.index, second.index)
            pairs[key] = _similarity(first, second)
    return pairs


def _similarity(left: PatchCandidate, right: PatchCandidate) -> float:
    """Return 0 when either side is invalid or the two edits name different files."""
    if not _applicable(left) or not _applicable(right):
        return 0.0
    if _path(left.file_path) != _path(right.file_path):
        return 0.0
    return _text_similarity(left.diff or "", right.diff or "")


def _text_similarity(left: str, right: str) -> float:
    """Mean of token-Jaccard and the difflib ratio, both in [0, 1]."""
    normalised_left = normalise_diff(left)
    normalised_right = normalise_diff(right)
    jaccard = _jaccard(normalised_left, normalised_right)
    ratio = difflib.SequenceMatcher(a=normalised_left, b=normalised_right).ratio()
    return (jaccard + ratio) / 2


def _jaccard(left: str, right: str) -> float:
    """Jaccard of the whitespace-separated tokens. Two empty texts count as the same."""
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens and not right_tokens:
        return 1.0
    union = left_tokens | right_tokens
    return len(left_tokens & right_tokens) / len(union)


def _medoid(
    applicable: Sequence[PatchCandidate],
    pairs: Mapping[tuple[int, int], float],
    count: int,
) -> int:
    """Pick the applicable candidate with the highest mean similarity. Lowest index wins a tie."""
    chosen = applicable[0].index
    chosen_average = -1.0
    for candidate in sorted(applicable, key=lambda item: item.index):
        average = _average(candidate.index, pairs, count)
        if average > chosen_average:
            chosen = candidate.index
            chosen_average = average
    return chosen


def _average(index: int, pairs: Mapping[tuple[int, int], float], count: int) -> float:
    """Mean similarity between `index` and every other candidate, including the zeros."""
    if count < 2:
        return 0.0
    total = 0.0
    for (left, right), similarity in pairs.items():
        if index in {left, right}:
            total += similarity
    return total / (count - 1)


def _path(file_path: str) -> str:
    """Compare paths with forward slashes and no leading `./`."""
    parts = [part for part in file_path.replace("\\", "/").split("/") if part not in {"", "."}]
    return "/".join(parts)


def _clamp(value: float) -> float:
    """Keep a score inside [0, 1]."""
    return min(1.0, max(0.0, value))
