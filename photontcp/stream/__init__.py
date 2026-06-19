"""PhotonTCP stream multiplexing package (M4).

Re-exports the stream multiplexer and its public surface so callers can import
from :mod:`photontcp.stream` directly.
"""

from __future__ import annotations

from .mux import (
    CONTROL_STREAM_ID,
    DEFAULT_STREAM_ID,
    MuxOutput,
    StreamMux,
)

__all__ = [
    "CONTROL_STREAM_ID",
    "DEFAULT_STREAM_ID",
    "MuxOutput",
    "StreamMux",
]
