"""32-bit serial number arithmetic (RFC 1982-style sequence comparison).

Reliable transport sequence numbers live in a fixed 32-bit space and wrap
around modulo ``2**32``. Plain integer ``<`` / ``>`` comparisons break across
the wrap boundary, so this module provides wraparound-safe helpers.

Comparisons follow RFC 1982 serial-number arithmetic specialised to 32 bits.
For two sequence numbers ``a`` and ``b``, let ``d = (b - a) % SEQ_MOD``. Then
``a`` is considered *less than* ``b`` when ``0 < d < 2**31`` — i.e. ``b`` is
within the "forward half" of the sequence space from ``a``. This makes, for
example, ``2**32 - 1`` compare as less than ``0`` (the next value after a wrap).

Note on the half-space boundary (``d == 2**31``): when ``a`` and ``b`` are
exactly half the sequence space apart the ordering is ambiguous; with the strict
``< 2**31`` bound used here such a pair compares as *not* less-than in either
direction (and *not* greater-than either), matching the cautious RFC 1982
treatment of the maximally-distant case.

All functions are pure and depend only on the standard library.
"""

__all__ = [
    "SEQ_BITS",
    "SEQ_MOD",
    "seq_add",
    "seq_lt",
    "seq_leq",
    "seq_gt",
    "seq_geq",
    "seq_diff",
]

SEQ_BITS = 32
SEQ_MOD = 2 ** 32

# Half of the sequence space; the threshold separating "forward" from
# "backward" distances in RFC 1982-style comparisons.
_SEQ_HALF = 2 ** 31


def seq_add(a: int, n: int) -> int:
    """Return ``a + n`` reduced into the 32-bit sequence space.

    The result wraps around modulo :data:`SEQ_MOD`, so e.g.
    ``seq_add(2**32 - 1, 1) == 0``. ``n`` may be negative.
    """
    return (a + n) % SEQ_MOD


def seq_lt(a: int, b: int) -> bool:
    """Return ``True`` if ``a`` is strictly *before* ``b`` in sequence order.

    Uses the wraparound-safe rule ``0 < (b - a) % SEQ_MOD < 2**31``. Across the
    wrap boundary this yields ``seq_lt(2**32 - 1, 0) is True``. Equal inputs
    (``a == b``) return ``False``. The maximally-distant case (distance exactly
    ``2**31``) returns ``False``.
    """
    d = (b - a) % SEQ_MOD
    return 0 < d < _SEQ_HALF


def seq_gt(a: int, b: int) -> bool:
    """Return ``True`` if ``a`` is strictly *after* ``b`` in sequence order.

    Defined as ``seq_lt(b, a)``. Equal inputs return ``False`` and the
    maximally-distant case (distance exactly ``2**31``) returns ``False``.
    """
    return seq_lt(b, a)


def seq_leq(a: int, b: int) -> bool:
    """Return ``True`` if ``a`` is before-or-equal to ``b`` in sequence order.

    Defined as ``a == b or seq_lt(a, b)``; equal inputs return ``True``.
    """
    return a == b or seq_lt(a, b)


def seq_geq(a: int, b: int) -> bool:
    """Return ``True`` if ``a`` is after-or-equal to ``b`` in sequence order.

    Defined as ``a == b or seq_gt(a, b)``; equal inputs return ``True``.
    """
    return a == b or seq_gt(a, b)


def seq_diff(a: int, b: int) -> int:
    """Return the signed circular distance from ``b`` to ``a`` (i.e. ``a - b``).

    Computed as ``d = (a - b) % SEQ_MOD`` then mapped into the signed range
    ``[-2**31, 2**31)`` by subtracting :data:`SEQ_MOD` when ``d >= 2**31``.
    Positive results mean ``a`` is ahead of ``b`` (useful for window-size
    calculations); negative results mean ``a`` is behind ``b``.
    """
    d = (a - b) % SEQ_MOD
    return d - SEQ_MOD if d >= _SEQ_HALF else d
