"""transport_receipts -- tie a ledger response to the transport call that produced it.

rt_send.py reports every target response to /api/redteam/chat. The server
notes a receipt here, keyed by the SHA-256 of the normalised response text:
which target was asked, which model the provider says answered
(``model_routed``), the session and request ids, and when.

gauntlet.record_attempt() looks the posted response up. A hit stamps
``model_routed`` (when the caller did not plumb it) and a ``transport_receipt``
on the attempt. A miss is recorded too: the response text that reached the
ledger never left rt_send.py in this server process, so it was edited, retyped,
or produced elsewhere. trophy_audit reads both.

In-memory and bounded on purpose: a receipt proves "this server saw that exact
text come back from that target", which a file an agent can write would not.
A server restart forgets receipts, so attempts posted across a restart read as
unmatched -- a false negative, never a false positive.
"""
from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from datetime import datetime

MAX_RECEIPTS = 2048

_lock = threading.Lock()
_receipts: "OrderedDict[str, dict]" = OrderedDict()


def response_digest(text) -> str:
    """SHA-256 of the response as the agent will see it after a round trip
    through stdout and a file: CRLF folded to LF, outer whitespace stripped."""
    norm = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(norm.encode("utf-8", errors="replace")).hexdigest()


def note(text, *, target="", model_routed="", session_id="", request_id="",
         claimed_digest="") -> dict:
    """Record a receipt for a response rt_send.py reported. Returns it.

    ``claimed_digest`` is the hash rt_send computed; it is stored for
    comparison only. The key is always the digest of the text the server
    actually received.
    """
    digest = response_digest(text)
    receipt = {
        "sha256": digest,
        "target": str(target or ""),
        "model_routed": "".join(str(model_routed or "").split())[:200],
        "session_id": str(session_id or ""),
        "request_id": str(request_id or ""),
        "time": datetime.now().isoformat(),
        "digest_agrees": (not claimed_digest) or claimed_digest == digest,
    }
    if not str(text or "").strip():
        return receipt
    with _lock:
        _receipts.pop(digest, None)
        _receipts[digest] = receipt
        while len(_receipts) > MAX_RECEIPTS:
            _receipts.popitem(last=False)
    return receipt


def lookup(text) -> dict:
    """Return the receipt for this response text, or {} if none was seen."""
    digest = response_digest(text)
    with _lock:
        hit = _receipts.get(digest)
        return dict(hit) if hit else {}


def stamp(text) -> dict:
    """The ``transport_receipt`` block record_attempt stores on an attempt."""
    hit = lookup(text)
    if not hit:
        return {"matched": False, "sha256": response_digest(text)}
    out = {"matched": True}
    out.update({k: hit[k] for k in ("sha256", "target", "model_routed",
                                     "request_id", "time")})
    return out


def clear() -> None:
    """Test helper."""
    with _lock:
        _receipts.clear()
