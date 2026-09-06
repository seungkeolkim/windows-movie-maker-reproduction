"""Deterministic time primitives for the project timeline."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from fractions import Fraction
from math import gcd
from typing import Self

NANOSECONDS_PER_SECOND = 1_000_000_000
NANOSECONDS_PER_MILLISECOND = 1_000_000


class TimeRounding(str, Enum):
    """Supported policies when an exact rational time does not fit one tick."""

    FLOOR = "floor"
    CEILING = "ceiling"
    NEAREST = "nearest"


def _round_fraction(value: Fraction, rounding: TimeRounding) -> int:
    if not isinstance(rounding, TimeRounding):
        raise TypeError("rounding must be a TimeRounding value.")
    if rounding is TimeRounding.FLOOR:
        return value.numerator // value.denominator
    if rounding is TimeRounding.CEILING:
        return -(-value.numerator // value.denominator)

    sign = -1 if value < 0 else 1
    numerator = abs(value.numerator)
    quotient, remainder = divmod(numerator, value.denominator)
    if remainder * 2 >= value.denominator:
        quotient += 1
    return sign * quotient


def _seconds_fraction(value: int | Decimal | Fraction) -> Fraction:
    if isinstance(value, bool):
        raise TypeError("Boolean values are not valid project times.")
    if isinstance(value, Fraction):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Project time must be finite.")
        return Fraction(value)
    if type(value) is int:
        return Fraction(value, 1)
    raise TypeError("Project time requires int, Decimal, or Fraction seconds.")


@dataclass(frozen=True, order=True, slots=True)
class ProjectTime:
    """A signed integer count of nanoseconds on the project timeline."""

    nanoseconds: int

    def __post_init__(self) -> None:
        if type(self.nanoseconds) is not int:
            raise TypeError("ProjectTime.nanoseconds must be an integer.")

    @classmethod
    def zero(cls) -> Self:
        """Return the project origin."""

        return cls(0)

    @classmethod
    def from_milliseconds(cls, milliseconds: int) -> Self:
        """Create an exact project time from an integer millisecond value."""

        if type(milliseconds) is not int:
            raise TypeError("Milliseconds must be an integer.")
        return cls(milliseconds * NANOSECONDS_PER_MILLISECOND)

    @classmethod
    def from_seconds(
        cls,
        seconds: int | Decimal | Fraction,
        *,
        rounding: TimeRounding = TimeRounding.NEAREST,
    ) -> Self:
        """Create project time from exact seconds using an explicit rounding policy."""

        ticks = _seconds_fraction(seconds) * NANOSECONDS_PER_SECOND
        return cls(_round_fraction(ticks, rounding))

    def to_fractional_seconds(self) -> Fraction:
        """Return the exact project time as a fraction of one second."""

        return Fraction(self.nanoseconds, NANOSECONDS_PER_SECOND)

    def to_milliseconds(self, *, rounding: TimeRounding = TimeRounding.NEAREST) -> int:
        """Convert to integer milliseconds for a UI or service adapter."""

        value = Fraction(self.nanoseconds, NANOSECONDS_PER_MILLISECOND)
        return _round_fraction(value, rounding)

    def __add__(self, other: ProjectTime) -> ProjectTime:
        if not isinstance(other, ProjectTime):
            return NotImplemented
        return ProjectTime(self.nanoseconds + other.nanoseconds)

    def __sub__(self, other: ProjectTime) -> ProjectTime:
        if not isinstance(other, ProjectTime):
            return NotImplemented
        return ProjectTime(self.nanoseconds - other.nanoseconds)

    def __neg__(self) -> ProjectTime:
        return ProjectTime(-self.nanoseconds)


@dataclass(frozen=True, slots=True)
class FrameRate:
    """A positive rational number of frames per second."""

    numerator: int
    denominator: int = 1

    def __post_init__(self) -> None:
        if type(self.numerator) is not int or type(self.denominator) is not int:
            raise TypeError("Frame-rate numerator and denominator must be integers.")
        if self.numerator <= 0 or self.denominator <= 0:
            raise ValueError("Frame rate must be positive.")

        divisor = gcd(self.numerator, self.denominator)
        object.__setattr__(self, "numerator", self.numerator // divisor)
        object.__setattr__(self, "denominator", self.denominator // divisor)

    @property
    def frames_per_second(self) -> Fraction:
        """Return the exact rate."""

        return Fraction(self.numerator, self.denominator)

    def time_at_frame(self, frame_index: int) -> ProjectTime:
        """Map an absolute non-negative frame index to project time."""

        if type(frame_index) is not int:
            raise TypeError("Frame index must be an integer.")
        if frame_index < 0:
            raise ValueError("Frame index cannot be negative.")
        seconds = Fraction(frame_index * self.denominator, self.numerator)
        return ProjectTime.from_seconds(seconds)

    def frame_at_or_before(self, time: ProjectTime) -> int:
        """Return the greatest quantized frame timestamp that is not after time."""

        if time.nanoseconds < 0:
            raise ValueError("Frame lookup requires a non-negative project time.")
        # For non-negative values, round-half-away maps an exact tick value x to at most t
        # precisely when x < t + 1/2. The strict integer form avoids a correction loop even
        # for rates where several frames quantize to the same nanosecond.
        upper_bound = (2 * time.nanoseconds + 1) * self.numerator
        tick_denominator = 2 * self.denominator * NANOSECONDS_PER_SECOND
        return (upper_bound - 1) // tick_denominator
