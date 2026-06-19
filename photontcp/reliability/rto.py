"""Adaptive RTO/RTT estimator (Jacobson/Karels algorithm).

This module provides :class:`RtoEstimator`, a pure and deterministic
implementation of the smoothed round-trip time (SRTT) and RTT variation
(RTTVAR) estimation described by Jacobson/Karels and standardised in
RFC 6298. The retransmission timeout (RTO) is derived from these estimates.

The estimator performs no real-time or random operations: all timing
information is supplied by the caller through measured RTT samples, which
makes the behaviour fully reproducible and easy to test.
"""

from __future__ import annotations

__all__ = ["RtoEstimator"]

# Jacobson/Karels smoothing constants (RFC 6298).
_ALPHA = 1.0 / 8.0  # SRTT gain.
_BETA = 1.0 / 4.0   # RTTVAR gain.
_K = 4.0            # RTTVAR multiplier for the RTO computation.


def _clamp(value: float, low: float, high: float) -> float:
    """Return *value* constrained to the inclusive range [*low*, *high*]."""
    if value < low:
        return low
    if value > high:
        return high
    return value


class RtoEstimator:
    """Adaptive retransmission-timeout estimator (Jacobson/Karels).

    The estimator maintains the smoothed RTT (``srtt``) and RTT variation
    (``rttvar``) from a stream of RTT samples and derives the current
    retransmission timeout (RTO) as ``srtt + 4 * rttvar``, clamped to the
    configured ``[min_rto, max_rto]`` bounds.

    All units are seconds. The class is purely deterministic: identical
    sequences of method calls always produce identical state.
    """

    def __init__(
        self,
        *,
        initial_rto: float = 1.0,
        min_rto: float = 0.2,
        max_rto: float = 60.0,
    ) -> None:
        """Initialise the estimator.

        :param initial_rto: RTO used before any RTT sample is observed.
        :param min_rto: Lower clamp bound for the computed RTO.
        :param max_rto: Upper clamp bound for the computed RTO.
        """
        self._min_rto = float(min_rto)
        self._max_rto = float(max_rto)
        self._srtt: float | None = None
        self._rttvar: float | None = None
        self._rto: float = float(initial_rto)

    @property
    def srtt(self) -> float | None:
        """Current smoothed RTT estimate, or ``None`` before any sample."""
        return self._srtt

    @property
    def rttvar(self) -> float | None:
        """Current RTT variation estimate, or ``None`` before any sample."""
        return self._rttvar

    def on_sample(self, rtt: float) -> None:
        """Update the estimator with a new RTT measurement.

        On the first sample, ``srtt`` is set to *rtt* and ``rttvar`` to
        ``rtt / 2``. Subsequent samples update ``rttvar`` and ``srtt`` using
        the Jacobson/Karels recurrences before recomputing the RTO.

        :param rtt: Measured round-trip time in seconds (must be >= 0).
        :raises ValueError: If *rtt* is negative.
        """
        rtt = float(rtt)
        if rtt < 0:
            raise ValueError(f"rtt must be non-negative, got {rtt!r}")

        if self._srtt is None or self._rttvar is None:
            self._srtt = rtt
            self._rttvar = rtt / 2.0
        else:
            self._rttvar = (1.0 - _BETA) * self._rttvar + _BETA * abs(
                self._srtt - rtt
            )
            self._srtt = (1.0 - _ALPHA) * self._srtt + _ALPHA * rtt

        self._rto = _clamp(
            self._srtt + _K * self._rttvar, self._min_rto, self._max_rto
        )

    def on_timeout(self) -> None:
        """Apply exponential backoff to the RTO after a timeout.

        The RTO is doubled, then clamped to ``max_rto``.
        """
        self._rto = min(self._rto * 2.0, self._max_rto)

    def rto(self) -> float:
        """Return the current retransmission timeout in seconds."""
        return self._rto
