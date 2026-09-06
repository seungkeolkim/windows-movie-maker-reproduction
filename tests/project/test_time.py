from decimal import Decimal
from fractions import Fraction

import pytest

from movie_maker.project import FrameRate, ProjectTime, TimeRounding


def test_project_time_converts_exact_adapter_units() -> None:
    time = ProjectTime.from_seconds(Decimal("1.23456789"))

    assert time.nanoseconds == 1_234_567_890
    assert time.to_fractional_seconds() == Fraction(123_456_789, 100_000_000)
    assert time.to_milliseconds() == 1_235
    assert time.to_milliseconds(rounding=TimeRounding.FLOOR) == 1_234


def test_project_time_uses_documented_half_away_from_zero_rounding() -> None:
    positive = ProjectTime.from_seconds(Fraction(1, 2_000_000_000))
    negative = ProjectTime.from_seconds(Fraction(-1, 2_000_000_000))

    assert positive.nanoseconds == 1
    assert negative.nanoseconds == -1


def test_project_time_rejects_floating_point_and_boolean_input() -> None:
    with pytest.raises(TypeError):
        ProjectTime.from_seconds(0.1)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ProjectTime.from_seconds(True)
    with pytest.raises(TypeError):
        ProjectTime.from_milliseconds(True)


def test_frame_rate_normalises_and_does_not_accumulate_fractional_frames() -> None:
    rate = FrameRate(48_000, 2_002)

    assert rate == FrameRate(24_000, 1_001)
    assert rate.time_at_frame(1).nanoseconds == 41_708_333
    assert rate.frame_at_or_before(rate.time_at_frame(1)) == 1
    assert rate.time_at_frame(24_000) == ProjectTime.from_seconds(1_001)
    assert rate.frame_at_or_before(ProjectTime.from_seconds(1_001)) == 24_000


@pytest.mark.parametrize(
    ("numerator", "denominator"),
    [(0, 1), (1, 0), (-24, 1), (24, -1)],
)
def test_frame_rate_rejects_non_positive_values(numerator: int, denominator: int) -> None:
    with pytest.raises(ValueError):
        FrameRate(numerator, denominator)


def test_time_conversion_rejects_unknown_rounding_policy() -> None:
    with pytest.raises(TypeError):
        ProjectTime.from_seconds(1, rounding="nearest")  # type: ignore[arg-type]
