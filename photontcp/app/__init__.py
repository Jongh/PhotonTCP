"""PhotonTCP application layer (chat).

Public surface of the chat application: the wire codec
(:class:`ChatMessage`, :func:`encode_message`, :class:`StreamReassembler`) and
the message-oriented endpoint (:class:`ChatSession`) that binds a transport
:class:`~photontcp.session.session.Session` to the codec.
"""

from __future__ import annotations

from .chat import ChatSession
from .codec import ChatMessage, StreamReassembler, encode_message

__all__ = [
    "ChatMessage",
    "encode_message",
    "StreamReassembler",
    "ChatSession",
]
