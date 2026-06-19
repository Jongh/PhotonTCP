"""Clock abstraction for the PhotonTCP session layer.

The session state machine and its synchronous pump must be testable without
real sleeps. To make time deterministic and injectable, all time access goes
through a :class:`Clock` that returns a monotonically non-decreasing time in
fractional seconds.

Two implementations are provided:

* :class:`ManualClock` — a virtual clock for tests, advanced explicitly.
* :class:`MonotonicClock` — a production clock delegating to
  :func:`time.monotonic`.

Wall-clock sources (``time.time()``, calendar dates, global mutable time) are
intentionally avoided: only injected clocks are used, so timeouts, heartbeats,
and handshakes can be exercised under fully controlled virtual time.
"""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    """Interface for a monotonic time source measured in seconds.

    Implementations must return a non-decreasing ``float`` from :meth:`now`.
    The absolute origin is unspecified and only differences are meaningful, so
    callers must not assume any particular epoch.
    """

    def now(self) -> float:
        """Return the current time in fractional seconds (monotonic)."""
        ...


class ManualClock:
    """A virtual clock for deterministic tests.

    The internal time starts at ``0.0`` and only moves when explicitly driven
    via :meth:`advance` or :meth:`set`, allowing tests to step through
    heartbeats and timeouts without any real sleeping.
    """

    def __init__(self, start: float = 0.0) -> None:
        """Initialize the clock at ``start`` seconds (default ``0.0``)."""
        self._t: float = float(start)

    def now(self) -> float:
        """Return the current virtual time in seconds."""
        return self._t

    def advance(self, dt: float) -> None:
        """Advance virtual time by ``dt`` seconds.

        :param dt: A non-negative number of seconds to move forward.
        :raises ValueError: If ``dt`` is negative (time must not go backward).
        """
        if dt < 0:
            raise ValueError(f"advance(dt) must be non-negative, got {dt!r}")
        self._t += float(dt)

    def set(self, t: float) -> None:
        """Set virtual time to the absolute value ``t`` seconds.

        :param t: A non-negative absolute time in seconds.
        :raises ValueError: If ``t`` is negative.
        """
        if t < 0:
            raise ValueError(f"set(t) must be non-negative, got {t!r}")
        self._t = float(t)


class MonotonicClock:
    """A production clock delegating to :func:`time.monotonic`.

    Reports a monotonically non-decreasing time from the standard library's
    monotonic source, which is unaffected by wall-clock adjustments.
    """

    def now(self) -> float:
        """Return :func:`time.monotonic` in fractional seconds."""
        return time.monotonic()
