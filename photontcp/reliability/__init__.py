"""PhotonTCP reliability layer (M3).

Public re-export surface for the reliability components:

* 32-bit wraparound-safe serial-number arithmetic (:mod:`.serial`).
* The adaptive RTO/RTT estimator (:mod:`.rto`).
* The Selective Repeat ARQ engine (:mod:`.arq`).
"""

from .arq import ArqEndpoint, ArqOutput
from .rto import RtoEstimator
from .serial import (
    SEQ_MOD,
    seq_add,
    seq_diff,
    seq_geq,
    seq_gt,
    seq_leq,
    seq_lt,
)

__all__ = [
    # serial
    "SEQ_MOD",
    "seq_add",
    "seq_lt",
    "seq_leq",
    "seq_gt",
    "seq_geq",
    "seq_diff",
    # rto
    "RtoEstimator",
    # arq
    "ArqEndpoint",
    "ArqOutput",
]
