"""Unit tests for 32-bit serial arithmetic and the adaptive RTO estimator.

Covers :mod:`photontcp.reliability.serial` (RFC 1982-style wraparound-safe
sequence comparison) and :mod:`photontcp.reliability.rto` (Jacobson/Karels
RTO/RTT estimation). Expected values are derived from the actual module
implementations, not assumed.
"""

import pytest

from photontcp.reliability.rto import RtoEstimator
from photontcp.reliability.serial import (
    SEQ_MOD,
    seq_add,
    seq_diff,
    seq_geq,
    seq_gt,
    seq_leq,
    seq_lt,
)

MAX = 2 ** 32 - 1  # largest representable 32-bit sequence number


# ---------------------------------------------------------------------------
# serial: wrap boundary
# ---------------------------------------------------------------------------


def test_seq_lt_wrap_boundary():
    # MAX is the value immediately before 0 after a wrap -> MAX < 0.
    assert seq_lt(MAX, 0) is True
    assert seq_lt(0, MAX) is False


def test_seq_gt_wrap_boundary():
    assert seq_gt(0, MAX) is True
    assert seq_gt(MAX, 0) is False


# ---------------------------------------------------------------------------
# serial: equality semantics
# ---------------------------------------------------------------------------


def test_seq_equal_inputs():
    assert seq_lt(5, 5) is False
    assert seq_gt(5, 5) is False
    assert seq_leq(5, 5) is True
    assert seq_geq(5, 5) is True


# ---------------------------------------------------------------------------
# serial: seq_add wraparound
# ---------------------------------------------------------------------------


def test_seq_add_wraps_at_modulus():
    assert seq_add(MAX, 1) == 0
    assert seq_add(MAX, 2) == 1
    assert seq_add(0, SEQ_MOD) == 0


def test_seq_add_basic_and_negative():
    assert seq_add(10, 5) == 15
    # Negative n wraps backwards across the boundary.
    assert seq_add(0, -1) == MAX
    assert seq_add(3, -5) == MAX - 1


# ---------------------------------------------------------------------------
# serial: ordering for small numbers and near the wrap
# ---------------------------------------------------------------------------


def test_seq_ordering_small_numbers():
    assert seq_lt(1, 2) is True
    assert seq_gt(2, 1) is True
    assert seq_leq(1, 2) is True
    assert seq_geq(2, 1) is True
    assert seq_lt(2, 1) is False
    assert seq_gt(1, 2) is False


def test_seq_ordering_near_wrap():
    # Forward across the boundary: MAX-1 -> MAX -> 0 -> 1
    assert seq_lt(MAX - 1, MAX) is True
    assert seq_lt(MAX, 0) is True
    assert seq_lt(0, 1) is True
    assert seq_lt(MAX - 1, 1) is True  # spans the wrap, still forward
    assert seq_gt(1, MAX - 1) is True


def test_seq_half_space_boundary_is_neither_lt_nor_gt():
    # Distance exactly 2**31 is the maximally-distant, ambiguous case:
    # neither strictly less-than nor strictly greater-than.
    half = 2 ** 31
    assert seq_lt(0, half) is False
    assert seq_gt(0, half) is False
    # leq/geq still hold only via the strict comparisons (a != b here).
    assert seq_leq(0, half) is False
    assert seq_geq(0, half) is False


# ---------------------------------------------------------------------------
# serial: seq_diff signed circular distance (a - b)
# ---------------------------------------------------------------------------


def test_seq_diff_sign_and_wrap():
    # Positive when a is ahead of b.
    assert seq_diff(10, 4) == 6
    # Negative when a is behind b.
    assert seq_diff(4, 10) == -6
    # Equal -> zero.
    assert seq_diff(7, 7) == 0
    # Across the wrap: 0 is one ahead of MAX.
    assert seq_diff(0, MAX) == 1
    # ...and MAX is one behind 0.
    assert seq_diff(MAX, 0) == -1


def test_seq_diff_half_space_mapping():
    half = 2 ** 31
    # d == 2**31 maps into the negative side of [-2**31, 2**31).
    assert seq_diff(half, 0) == -half
    # Just below half stays positive.
    assert seq_diff(half - 1, 0) == half - 1


# ---------------------------------------------------------------------------
# RTO: first sample
# ---------------------------------------------------------------------------


def test_rto_first_sample():
    est = RtoEstimator()
    est.on_sample(0.5)
    # First sample: srtt = rtt, rttvar = rtt / 2.
    assert est.srtt == pytest.approx(0.5)
    assert est.rttvar == pytest.approx(0.25)
    # rto = srtt + 4 * rttvar = 0.5 + 1.0 = 1.5.
    assert est.rto() == pytest.approx(1.5)


def test_rto_before_any_sample_uses_initial():
    est = RtoEstimator(initial_rto=1.0)
    assert est.srtt is None
    assert est.rttvar is None
    assert est.rto() == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# RTO: convergence on a steady RTT
# ---------------------------------------------------------------------------


def test_rto_converges_on_constant_rtt():
    est = RtoEstimator()
    est.on_sample(0.5)
    for _ in range(200):
        est.on_sample(0.5)
    # With a constant RTT, srtt -> rtt and rttvar -> 0, so rto -> rtt.
    assert est.srtt == pytest.approx(0.5, abs=1e-6)
    assert est.rttvar == pytest.approx(0.0, abs=1e-6)
    assert est.rto() == pytest.approx(0.5, abs=1e-6)


# ---------------------------------------------------------------------------
# RTO: min/max clamping
# ---------------------------------------------------------------------------


def test_rto_clamped_to_minimum():
    est = RtoEstimator(min_rto=0.2)
    # Tiny RTT would yield rto well below the floor; expect the clamp.
    est.on_sample(0.001)
    # raw = 0.001 + 4 * 0.0005 = 0.003 < 0.2
    assert est.rto() == pytest.approx(0.2)


def test_rto_clamped_to_maximum():
    est = RtoEstimator(max_rto=5.0)
    # Large RTT would yield rto above the ceiling; expect the clamp.
    est.on_sample(100.0)
    # raw = 100 + 4 * 50 = 300 > 5.0
    assert est.rto() == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# RTO: exponential backoff on timeout
# ---------------------------------------------------------------------------


def test_rto_timeout_doubles():
    est = RtoEstimator(initial_rto=1.0, max_rto=60.0)
    assert est.rto() == pytest.approx(1.0)
    est.on_timeout()
    assert est.rto() == pytest.approx(2.0)
    est.on_timeout()
    assert est.rto() == pytest.approx(4.0)


def test_rto_timeout_clamped_to_maximum():
    est = RtoEstimator(initial_rto=40.0, max_rto=60.0)
    est.on_timeout()  # 80 -> clamped to 60
    assert est.rto() == pytest.approx(60.0)
    est.on_timeout()  # stays at 60
    assert est.rto() == pytest.approx(60.0)


# ---------------------------------------------------------------------------
# RTO: input validation
# ---------------------------------------------------------------------------


def test_rto_negative_sample_raises():
    est = RtoEstimator()
    with pytest.raises(ValueError):
        est.on_sample(-0.1)


def test_rto_zero_sample_allowed():
    est = RtoEstimator()
    # Zero is the boundary of the non-negative requirement; must not raise.
    est.on_sample(0.0)
    assert est.srtt == pytest.approx(0.0)
    assert est.rttvar == pytest.approx(0.0)
