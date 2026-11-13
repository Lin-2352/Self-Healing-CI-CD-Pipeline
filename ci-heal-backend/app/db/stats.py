"""SQL aggregates for the stats endpoints.

Counts, rates, and ECE are computed in the database. Python only places those
results into the response, including the empty calibration bins the query's
bin list left out.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import (
    Integer,
    and_,
    case,
    cast,
    exists,
    func,
    literal,
    select,
    true,
    union_all,
)
from sqlalchemy.orm import Session
from sqlalchemy.sql import Subquery
from sqlalchemy.sql.elements import ColumnElement

from app.core.enums import DecisionAction, DecisionOutcome, FailureCategory
from app.db.models import Classification, Decision, Failure
from app.schemas.stats import (
    CalibrationBin,
    CalibrationReport,
    CalibrationSeries,
    CategoryScore,
    ConfusionCell,
    GroundTruthStats,
    GroupCount,
    GroupedStats,
    Summary,
    Timeline,
    TimelineDay,
)

# Pending and error rows have not been verified. Escalated is a resolved non-success.
_RESOLVED = (
    DecisionOutcome.VERIFIED_SUCCESS,
    DecisionOutcome.VERIFIED_FAILURE,
    DecisionOutcome.RETRY_SUCCESS,
    DecisionOutcome.RETRY_FAILURE,
    DecisionOutcome.ESCALATED,
)


@dataclass(frozen=True)
class StatsFilter:
    """Shared query bounds. `until` is exclusive."""

    repo: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    include_dry_run: bool = False


def _success() -> ColumnElement[float]:
    """1 when a FIX verified or a RETRY succeeded, otherwise 0."""
    return case(
        (
            and_(
                Decision.action == DecisionAction.FIX,
                Decision.outcome == DecisionOutcome.VERIFIED_SUCCESS,
            ),
            1.0,
        ),
        (
            and_(
                Decision.action == DecisionAction.RETRY,
                Decision.outcome == DecisionOutcome.RETRY_SUCCESS,
            ),
            1.0,
        ),
        else_=0.0,
    )


def _rate(successes: float, total: int) -> float | None:
    """Return successes/total, or None when there is nothing to score."""
    if total == 0:
        return None
    return float(successes) / total


class StatsRepository:
    """Read-only aggregates. The caller owns the session."""

    def __init__(self, session: Session) -> None:
        """Bind the repository to one session."""
        self._session = session

    def summary(self, bounds: StatsFilter) -> Summary:
        """Count resolved decisions and, separately, labeled classification accuracy."""
        success = _success()
        row = self._session.execute(
            select(func.count(), func.coalesce(func.sum(success), 0.0)).where(
                *self._decision_filters(bounds)
            )
        ).one()
        total = int(row[0])
        successes = float(row[1])
        labeled, correct = self._label_totals(bounds)
        return Summary(
            n_total=total,
            successes=int(successes),
            success_rate=_rate(successes, total),
            n_labeled=labeled,
            accuracy=_rate(correct, labeled),
        )

    def grouped(self, bounds: StatsFilter, column: ColumnElement[object]) -> GroupedStats:
        """Group resolved decisions by `column` and count successes in each group."""
        success = _success()
        rows = self._session.execute(
            select(column, func.count(), func.coalesce(func.sum(success), 0.0))
            .where(*self._decision_filters(bounds))
            .group_by(column)
            .order_by(column)
        ).all()
        items = [
            GroupCount(
                key=_text(key),
                count=int(count),
                successes=int(successes),
                success_rate=_rate(float(successes), int(count)),
            )
            for key, count, successes in rows
        ]
        return GroupedStats(items=items, n_total=sum(item.count for item in items))

    def timeline(self, bounds: StatsFilter) -> Timeline:
        """Count resolved decisions per UTC day."""
        success = _success()
        day = _utc_day(Decision.created_at, self._dialect()).label("day")
        rows = self._session.execute(
            select(day, func.count(), func.coalesce(func.sum(success), 0.0))
            .where(*self._decision_filters(bounds))
            .group_by(day)
            .order_by(day)
        ).all()
        days = [
            TimelineDay(date=str(value), count=int(count), successes=int(successes))
            for value, count, successes in rows
        ]
        return Timeline(days=days, n_total=sum(item.count for item in days))

    def calibration(self, bounds: StatsFilter, bins: int) -> CalibrationReport:
        """Bin classifier confidence and agreement, and add ground truth when it exists."""
        return CalibrationReport(
            classifier_confidence=self._decision_series(
                bounds, Decision.classifier_confidence, bins
            ),
            agreement_score=self._decision_series(bounds, Decision.agreement_score, bins),
            ground_truth=self._ground_truth(bounds, bins),
        )

    def _decision_series(
        self, bounds: StatsFilter, score: ColumnElement[float | None], bins: int
    ) -> CalibrationSeries:
        """Bin one decision score. Success is a verified fix or a successful retry."""
        scored = (
            select(
                _bin(score, bins).label("bin"),
                score.label("score"),
                _success().label("hit"),
            )
            .where(*self._decision_filters(bounds), score.is_not(None))
            .subquery()
        )
        return self._series(scored.c.bin, scored.c.score, scored.c.hit, bins)

    def _series(
        self,
        bin_index: ColumnElement[int],
        score: ColumnElement[float],
        hit: ColumnElement[float],
        bins: int,
    ) -> CalibrationSeries:
        """GROUP the scored rows, compute ECE in SQL, and attach the empty bins."""
        grouped = (
            select(
                bin_index.label("bin"),
                func.count().label("n"),
                func.avg(score).label("mean_conf"),
                func.avg(hit).label("observed"),
            )
            .group_by(bin_index)
            .subquery()
        )
        ece = self._session.scalar(
            select(
                func.sum(grouped.c.n * func.abs(grouped.c.observed - grouped.c.mean_conf))
                / func.sum(grouped.c.n)
            )
        )
        n_total = self._session.scalar(select(func.coalesce(func.sum(grouped.c.n), 0)))
        numbers = _bin_numbers(bins)
        fetched = self._session.execute(
            select(
                numbers.c.i,
                func.coalesce(grouped.c.n, 0),
                grouped.c.mean_conf,
                grouped.c.observed,
            )
            .select_from(numbers.outerjoin(grouped, grouped.c.bin == numbers.c.i))
            .order_by(numbers.c.i)
        ).all()
        width = 1.0 / bins
        filled = [
            CalibrationBin(
                lower=index * width,
                upper=(index + 1) * width,
                count=int(count),
                mean_confidence=None if count == 0 else float(mean),
                observed_success_rate=None if count == 0 else float(observed),
            )
            for index, count, mean, observed in fetched
        ]
        return CalibrationSeries(
            bins=filled,
            n_total=int(n_total or 0),
            ece=None if not n_total else float(ece),
        )

    def _ground_truth(self, bounds: StatsFilter, bins: int) -> GroundTruthStats | None:
        """Accuracy, precision, recall, confusion, and category-confidence calibration."""
        labeled = self._labeled(bounds)
        total, correct = self._count_labeled(labeled)
        if total == 0:
            return None
        counts = self._confusion_counts(labeled)
        scored = (
            select(
                _bin(labeled.c.confidence, bins).label("bin"),
                labeled.c.confidence.label("score"),
                case((labeled.c.predicted == labeled.c.actual, 1.0), else_=0.0).label("hit"),
            )
            .select_from(labeled)
            .subquery()
        )
        return GroundTruthStats(
            n_total=total,
            accuracy=_rate(correct, total),
            per_category=self._category_scores(labeled),
            confusion=_matrix(counts),
            calibration=self._series(scored.c.bin, scored.c.score, scored.c.hit, bins),
        )

    def _label_totals(self, bounds: StatsFilter) -> tuple[int, float]:
        """Return how many labeled failures there are and how many the classifier got right."""
        return self._count_labeled(self._labeled(bounds))

    def _count_labeled(self, labeled: Subquery) -> tuple[int, float]:
        """Count rows in a labeled subquery and how many predictions match."""
        total = self._session.scalar(select(func.count()).select_from(labeled))
        correct = self._session.scalar(
            select(func.count()).select_from(labeled).where(labeled.c.predicted == labeled.c.actual)
        )
        return int(total or 0), float(correct or 0)

    def _confusion_counts(self, labeled: Subquery) -> dict[tuple[str, str], int]:
        """Aggregate predicted/actual pairs. Zero cells are filled in afterwards."""
        rows = self._session.execute(
            select(labeled.c.predicted, labeled.c.actual, func.count()).group_by(
                labeled.c.predicted, labeled.c.actual
            )
        ).all()
        return {(_text(predicted), _text(actual)): int(count) for predicted, actual, count in rows}

    def _category_scores(self, labeled: Subquery) -> list[CategoryScore]:
        """Precision and recall per category, both reduced in SQL."""
        counts = (
            select(
                labeled.c.predicted.label("predicted"),
                labeled.c.actual.label("actual"),
                func.count().label("n"),
            )
            .group_by(labeled.c.predicted, labeled.c.actual)
            .subquery()
        )
        categories = union_all(
            *[
                select(
                    literal(category.name).label("cat"),
                    literal(category.value).label("label"),
                )
                for category in FailureCategory
            ]
        ).subquery()
        matched = func.coalesce(
            func.sum(
                case(
                    (
                        and_(
                            counts.c.predicted == categories.c.cat,
                            counts.c.actual == categories.c.cat,
                        ),
                        counts.c.n,
                    ),
                    else_=0,
                )
            ),
            0,
        )
        predicted_n = func.coalesce(
            func.sum(case((counts.c.predicted == categories.c.cat, counts.c.n), else_=0)),
            0,
        )
        support = func.coalesce(
            func.sum(case((counts.c.actual == categories.c.cat, counts.c.n), else_=0)),
            0,
        )
        rows = self._session.execute(
            select(categories.c.label, matched, predicted_n, support)
            .select_from(categories)
            .join(counts, true())
            .group_by(categories.c.cat, categories.c.label)
        ).all()
        return [
            CategoryScore(
                category=_text(name),
                precision=_rate(float(tp), int(predicted)),
                recall=_rate(float(tp), int(actual_n)),
                support=int(actual_n),
            )
            for name, tp, predicted, actual_n in rows
        ]

    def _labeled(self, bounds: StatsFilter) -> Subquery:
        """Latest classification of each failure that has a ground-truth label."""
        latest = (
            select(
                Classification.failure_id.label("failure_id"),
                func.max(Classification.id).label("id"),
            )
            .group_by(Classification.failure_id)
            .subquery()
        )
        statement = (
            select(
                Classification.category.label("predicted"),
                Failure.ground_truth_category.label("actual"),
                Classification.confidence.label("confidence"),
            )
            .join(latest, Classification.id == latest.c.id)
            .join(Failure, Failure.id == Classification.failure_id)
            .where(Failure.ground_truth_category.is_not(None))
        )
        if bounds.repo is not None:
            statement = statement.where(Failure.repo == bounds.repo)
        if bounds.since is not None:
            statement = statement.where(Failure.created_at >= bounds.since)
        if bounds.until is not None:
            statement = statement.where(Failure.created_at < bounds.until)
        if not bounds.include_dry_run:
            statement = statement.where(
                exists(
                    select(Decision.id).where(
                        Decision.failure_id == Failure.id,
                        Decision.dry_run.is_(False),
                    )
                )
            )
        return statement.subquery()

    def _decision_filters(self, bounds: StatsFilter) -> list[ColumnElement[bool]]:
        """Resolved decisions, excluding dry runs unless the caller opted in."""
        filters: list[ColumnElement[bool]] = [Decision.outcome.in_(_RESOLVED)]
        if not bounds.include_dry_run:
            filters.append(Decision.dry_run.is_(False))
        if bounds.repo is not None:
            filters.append(Decision.repo == bounds.repo)
        if bounds.since is not None:
            filters.append(Decision.created_at >= bounds.since)
        if bounds.until is not None:
            filters.append(Decision.created_at < bounds.until)
        return filters

    def _dialect(self) -> str:
        """Return the SQL dialect name for day bucketing."""
        return self._session.get_bind().dialect.name


def _bin(score: ColumnElement[float | None], bins: int) -> ColumnElement[int]:
    """Map a score in [0, 1] to a bin. 1.0 lands in the last bin, not past it."""
    return case(
        (score >= 1, bins - 1),
        (score < 0, 0),
        else_=cast(score * bins, Integer),
    )


def _bin_numbers(bins: int):
    """A SQL list of bin indexes from 0 through `bins - 1`."""
    numbers = select(literal(0).label("i")).cte("bin_numbers", recursive=True)
    return numbers.union_all(select(numbers.c.i + 1).where(numbers.c.i + 1 < bins))


def _utc_day(column: ColumnElement[datetime], dialect: str) -> ColumnElement[str]:
    """Bucket a timestamp by its UTC calendar day."""
    if dialect == "postgresql":
        return func.to_char(func.timezone("UTC", column), "YYYY-MM-DD")
    return func.strftime("%Y-%m-%d", column)


def _matrix(counts: dict[tuple[str, str], int]) -> list[ConfusionCell]:
    """Expand aggregated pairs into a full category-by-category matrix."""
    names = [category.value for category in FailureCategory]
    return [
        ConfusionCell(predicted=predicted, actual=actual, count=counts.get((predicted, actual), 0))
        for predicted in names
        for actual in names
    ]


def _text(value: object) -> str:
    """Return the API value for an enum, including a name read back from a subquery."""
    raw = getattr(value, "value", value)
    text = str(raw)
    for enum_type in (FailureCategory, DecisionAction, DecisionOutcome):
        try:
            return enum_type[text].value
        except KeyError:
            continue
    return text
