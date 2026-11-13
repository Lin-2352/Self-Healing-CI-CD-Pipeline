"""Response models for the stats endpoints."""

from pydantic import BaseModel, Field


class Summary(BaseModel):
    """Resolved decisions in the filter, plus labeled accuracy when ground truth exists."""

    n_total: int
    successes: int
    success_rate: float | None
    n_labeled: int
    accuracy: float | None


class GroupCount(BaseModel):
    """One group in a by-category, by-action, or by-outcome table."""

    key: str
    count: int
    successes: int
    success_rate: float | None


class GroupedStats(BaseModel):
    """Grouped counts. `n_total` is the number of resolved decisions in the filter."""

    items: list[GroupCount]
    n_total: int


class CalibrationBin(BaseModel):
    """One confidence interval. Empty bins keep a null rate so the axis stays stable."""

    lower: float
    upper: float
    count: int
    mean_confidence: float | None
    observed_success_rate: float | None


class CalibrationSeries(BaseModel):
    """Bins plus the expected calibration error of the non-empty bins.

    `ece` is null when `n_total` is 0. `n_total` is the number of scored rows, so a
    dashboard can warn when the sample is small.
    """

    bins: list[CalibrationBin]
    n_total: int
    ece: float | None


class CategoryScore(BaseModel):
    """Precision and recall for one ground-truth category. Null when the denominator is 0."""

    category: str
    precision: float | None
    recall: float | None
    support: int


class ConfusionCell(BaseModel):
    """One cell of the predicted-by-actual matrix, including zeroes."""

    predicted: str
    actual: str
    count: int


class GroundTruthStats(BaseModel):
    """How the latest classification compares with `failures.ground_truth_category`."""

    n_total: int
    accuracy: float | None
    per_category: list[CategoryScore]
    confusion: list[ConfusionCell]
    calibration: CalibrationSeries


class CalibrationReport(BaseModel):
    """Classifier confidence, agreement, and (when labels exist) category calibration."""

    classifier_confidence: CalibrationSeries
    agreement_score: CalibrationSeries
    ground_truth: GroundTruthStats | None


class TimelineDay(BaseModel):
    """Resolved decisions whose `created_at` falls on one UTC day."""

    date: str = Field(min_length=10, max_length=10)
    count: int
    successes: int


class Timeline(BaseModel):
    """Daily counts, oldest day first."""

    days: list[TimelineDay]
    n_total: int
