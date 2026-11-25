"""Deterministic warning signals for comms, not a safety verdict or an auth gate."""

import re
import unicodedata


_SECRET = r"(?:api[ _-]?keys?|access[ _-]?tokens?|credentials?|passwords?|\.env\b|secrets\.json|operator\.token|api anahtar\w*|şifre\w*)"
_DISCLOSE = r"(?:send|upload|forward|reveal|print|copy|show|read|gönder\w*|yükle\w*|aktar\w*|göster\w*|yazdır\w*|oku|paylaş\w*)"
_RULES = [
    ("instruction_override", "Instruction override phrase",
     r"\b(?:ignore|disregard|override|forget)\b.{0,70}\b(?:previous|prior|above|system|developer|operator|instructions?|rules?|polic(?:y|ies))\b"
     r"|(?:önceki|yukarıdaki|sistem|operatör).{0,70}(?:talimat|kural|politika).{0,40}(?:yok\s*say|unut|geçersiz\s*kıl|görmezden\s*gel)"),
    ("role_spoofing", "Content spoofing system or developer role",
     r"</?(?:system|developer)>|\[(?:system|developer)\]|\b(?:system|developer)(?:\s+(?:message|override))?\s*:"),
    ("sensitive_data_request", "Pattern requesting or sharing sensitive data",
     rf"\b{_DISCLOSE}\b[^;!?]{{0,110}}{_SECRET}|{_SECRET}[^;!?]{{0,110}}\b{_DISCLOSE}\b"),
    ("concealment", "Phrase attempting to conceal actions from the operator",
     r"\b(?:do not|don't)\s+(?:tell|inform|notify)\s+(?:the\s+)?(?:operator|user)\b"
     r"|(?:operatör|kullanıcı).{0,25}(?:söyleme|gizle|haber\s*verme)"),
    ("process_termination", "Process termination command",
     r"\b(?:stop-process|taskkill(?:\.exe)?|pkill|killall)\b|\bos\.kill\s*\("),
    ("workflow_redirection", "Pattern redirecting message or review workflow",
     r"\b(?:review|routing)\s+instructions?\b|\b(?:repost|forward)\s+(?:this|the)\s+(?:message|content)\b"
     r"|\b(?:reply|respond)\s+(?:to\s+this\s+message\s+)?on\s+(?:the\s+)?[\w-]+\s+channel\b"
     r"|özetlemeden\s+önce.{0,100}(?:gönder|ilet)"),
]
_RULES = [(code, label, re.compile(pattern)) for code, label, pattern in _RULES]


def _normalize(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.translate(str.maketrans("", "", "\u200b\u200c\u200d\ufeff\u0307"))
    return " ".join(text.split())


def review_comms_message(message):
    """Scan all message text, independently of any supplied review or sender name."""
    sender = _normalize(message.get("sender"))
    text = " ".join(_normalize(message.get(field)) for field in ("sender", "channel", "content"))
    reasons = [
        {"code": code, "label": label}
        for code, label, pattern in _RULES
        if pattern.search(text) or (code == "role_spoofing" and sender in {"system", "developer"})
    ]
    return {"version": 1, "status": "flagged" if reasons else "clear", "reasons": reasons}
