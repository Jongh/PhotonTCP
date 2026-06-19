"""PhotonTCP application layer (chat + file transfer).

Public surface of the application layer:

* **Chat**: the wire codec (:class:`ChatMessage`, :func:`encode_message`,
  :class:`StreamReassembler`) and the message-oriented endpoint
  (:class:`ChatSession`).
* **File transfer**: the single-file, one-directional endpoints
  (:class:`FileSender`, :class:`FileReceiver`) with their completion handshake,
  the frame type tag (:class:`FileFrameType`), and the transfer lifecycle state
  (:class:`FileTransferState`).

Both bind a transport :class:`~photontcp.session.session.Session` to a pure
codec via a synchronous pump driver.
"""

from __future__ import annotations

from .chat import ChatSession
from .codec import ChatMessage, StreamReassembler, encode_message
from .file import FileReceiver, FileSender, FileTransferState
from .file_codec import FileFrameType

__all__ = [
    "ChatMessage",
    "encode_message",
    "StreamReassembler",
    "ChatSession",
    "FileSender",
    "FileReceiver",
    "FileFrameType",
    "FileTransferState",
]
