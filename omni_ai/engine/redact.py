"""Remove secrets (and optionally personal data) from text that is about to be written to a log.

Plain Python, no Frappe import. The audit log records what a tool was asked to do; it must not become a
second place where passwords, tokens and keys live. Patterns are deliberately conservative: they look for
well-known token shapes and for `name = value` pairs whose name says "secret".
"""

import re

from omni_ai.engine import shield

REDACTED = "[REDACTED]"

_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
# an Authorization header value (any scheme), Frappe's "token key:secret", and long Bearer values.
# Each is shaped narrowly so ordinary sentences such as "Invalid token provided" are left alone.
_AUTH_HEADER = re.compile(r"(?i)(\bauthorization[\"']?\s*[:=]\s*[\"']?)(?:basic|token|bearer)\s+[^\s\"',;]+")
_API_KEY_PAIR = re.compile(r"(?i)\b(token\s+)[A-Za-z0-9]{8,}:[A-Za-z0-9]{8,}")
_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{16,}")
_KNOWN_TOKENS = re.compile(
	r"\b(?:sk-ant-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
	r"|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|erpmcp_[A-Za-z0-9_-]{16,})(?![A-Za-z0-9_-])"
)
# name = value / "name": "value" where the name says it is secret; the name is kept, the value removed
_SECRET_PAIR = re.compile(
	r"(?i)(\b(?:password|passwd|pwd|passcode|secret|api[_-]?key|api[_-]?secret|access[_-]?token|refresh[_-]?token"
	r"|client[_-]?secret|private[_-]?key|token)[\"']?\s*[:=]\s*[\"']?)([^\s\"',;&})]+)"
)
# user:password@host in a URL
_URL_PASSWORD = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]+:)([^\s@/]+)(@)")


def redact(text, mask_personal_data: bool = False) -> str:
	"""`text` with secrets replaced by [REDACTED]; with `mask_personal_data`, also emails, phones, IBANs, cards, IDs."""
	if not isinstance(text, str) or not text:
		return text
	text = _PRIVATE_KEY.sub(REDACTED, text)
	text = _JWT.sub(REDACTED, text)
	text = _AUTH_HEADER.sub(lambda m: m.group(1) + REDACTED, text)
	text = _API_KEY_PAIR.sub(lambda m: m.group(1) + REDACTED, text)
	text = _BEARER.sub(lambda m: m.group(1) + REDACTED, text)
	text = _KNOWN_TOKENS.sub(REDACTED, text)
	text = _URL_PASSWORD.sub(lambda m: m.group(1) + REDACTED + m.group(3), text)
	text = _SECRET_PAIR.sub(lambda m: m.group(1) + REDACTED, text)
	if mask_personal_data:
		text = shield.mask(text, shield.Vault())  # one-way here: the vault is thrown away
	return text
