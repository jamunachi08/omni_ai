"""Personal-data masking for text that leaves the site.

Before a request goes to the AI provider, email addresses, phone numbers, IBANs, payment-card
numbers and Saudi national ID / Iqama numbers are replaced with placeholders such as [[EMAIL_1]].
The table of placeholders (the vault) never leaves the server. The provider's answer and the tool
calls it asks for are unmasked on the way back, so the user reads real values and a lookup by
email still finds the record.

What is not masked: names, addresses, free-text descriptions, images and PDF pages.

No Frappe import: this module is plain Python so it can be tested on its own.
"""

import re

PLACEHOLDER_RE = re.compile(r"\[\[(EMAIL|PHONE|IBAN|CARD|ID)_(\d+)\]\]")
# the longest text that can still grow into a placeholder; used to hold back streamed text
_PARTIAL_RE = re.compile(r"\[(\[([A-Z]{0,5}(_\d{0,6}(\]\]?)?)?)?)?$")

EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}(?![\w-])")
IBAN_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?(?![A-Za-z0-9])")
CARD_RE = re.compile(r"(?<![\d.,-])\d(?:[ -]?\d){12,18}(?![\d]|[.,]\d)")
# international numbers written with +, and Saudi mobiles written 05XXXXXXXX or 9665XXXXXXXX
PHONE_RE = re.compile(r"(?<![\w.+-])(?:\+\d(?:[ -]?\d){7,14}|(?:00)?9665\d{8}|05\d{8})(?![\d]|[.,]\d)")
ID_RE = re.compile(r"(?<![\w.,-])[12]\d{9}(?![\d]|[.,]\d)")


def _luhn(digits: str) -> bool:
	total = 0
	for index, char in enumerate(reversed(digits)):
		value = ord(char) - 48
		if index % 2:
			value *= 2
			if value > 9:
				value -= 9
		total += value
	return total % 10 == 0


def _iban_ok(text: str) -> bool:
	compact = text.replace(" ", "")
	if not 15 <= len(compact) <= 34:
		return False
	moved = compact[4:] + compact[:4]
	try:
		return int("".join(str(int(ch, 36)) for ch in moved)) % 97 == 1
	except ValueError:
		return False


def _card_ok(text: str) -> bool:
	digits = re.sub(r"\D", "", text)
	return 13 <= len(digits) <= 19 and len(set(digits)) > 1 and _luhn(digits)


def _id_ok(text: str) -> bool:
	return _luhn(text)  # Saudi ID and Iqama numbers carry a Luhn check digit


# order matters: an IBAN contains digit runs that would otherwise look like cards or IDs
_RULES = (
	("EMAIL", EMAIL_RE, None),
	("IBAN", IBAN_RE, _iban_ok),
	("CARD", CARD_RE, _card_ok),
	("PHONE", PHONE_RE, None),
	("ID", ID_RE, _id_ok),
)


class Vault:
	"""Placeholder <-> original for one reply. The same value always gets the same placeholder."""

	def __init__(self):
		self.by_value = {}
		self.by_token = {}
		self.counts = {}

	def token_for(self, kind: str, value: str) -> str:
		key = (kind, value)
		token = self.by_value.get(key)
		if not token:
			self.counts[kind] = self.counts.get(kind, 0) + 1
			token = f"[[{kind}_{self.counts[kind]}]]"
			self.by_value[key] = token
			self.by_token[token] = value
		return token

	def __len__(self):
		return len(self.by_token)


def mask(text, vault: Vault):
	"""Replace personal data in `text` with placeholders recorded in `vault`."""
	if not isinstance(text, str) or len(text) < 6:
		return text
	for kind, pattern, check in _RULES:

		def swap(match, kind=kind, check=check):
			value = match.group(0)
			if check and not check(value):
				return value
			return vault.token_for(kind, value)

		text = pattern.sub(swap, text)
	return text


def unmask(text, vault: Vault):
	if not isinstance(text, str) or "[[" not in text or not len(vault):
		return text
	return PLACEHOLDER_RE.sub(lambda m: vault.by_token.get(m.group(0), m.group(0)), text)


def mask_obj(value, vault: Vault):
	"""Mask every string inside a JSON-like structure (keys are left alone)."""
	if isinstance(value, str):
		return mask(value, vault)
	if isinstance(value, list):
		return [mask_obj(v, vault) for v in value]
	if isinstance(value, dict):
		return {k: mask_obj(v, vault) for k, v in value.items()}
	return value


def unmask_obj(value, vault: Vault):
	if isinstance(value, str):
		return unmask(value, vault)
	if isinstance(value, list):
		return [unmask_obj(v, vault) for v in value]
	if isinstance(value, dict):
		return {k: unmask_obj(v, vault) for k, v in value.items()}
	return value


def _mask_block(block: dict, vault: Vault) -> dict:
	kind = block.get("type")
	if kind == "text":
		return dict(block, text=mask(block.get("text"), vault))
	if kind == "tool_use":
		return dict(block, input=mask_obj(block.get("input"), vault))
	if kind == "tool_result":
		return dict(block, content=mask_obj(block.get("content"), vault))
	return block  # thinking (signed), images, documents and provider-side blocks go through untouched


def mask_payload(payload: dict, vault: Vault) -> dict:
	"""A copy of a request payload with personal data masked in the system prompt, messages and tool results."""
	out = dict(payload)
	system = payload.get("system")
	if isinstance(system, str):
		out["system"] = mask(system, vault)
	elif isinstance(system, list):
		out["system"] = [_mask_block(b, vault) for b in system]
	messages = []
	for message in payload.get("messages") or []:
		content = message.get("content")
		if isinstance(content, str):
			content = mask(content, vault)
		else:
			content = [_mask_block(b, vault) for b in content or []]
		messages.append(dict(message, content=content))
	out["messages"] = messages
	return out


def unmask_message(message: dict, vault: Vault) -> dict:
	"""The provider's reply with placeholders turned back into real values (text and tool arguments)."""
	content = []
	for block in message.get("content") or []:
		kind = block.get("type")
		if kind == "text":
			block = dict(block, text=unmask(block.get("text"), vault))
		elif kind == "tool_use":
			block = dict(block, input=unmask_obj(block.get("input"), vault))
		content.append(block)
	return dict(message, content=content)


class StreamUnmasker:
	"""Unmasks streamed text. A placeholder can arrive split across pieces, so the end of the text is
	held back while it could still be the start of one."""

	def __init__(self, vault: Vault, emit):
		self.vault = vault
		self.emit = emit
		self.held = ""

	def feed(self, delta: str):
		text = self.held + (delta or "")
		match = _PARTIAL_RE.search(text)
		if match and match.group(0) and len(match.group(0)) < 20:
			self.held = match.group(0)
			text = text[: match.start()]
		else:
			self.held = ""
		if text:
			self.emit(unmask(text, self.vault))

	def flush(self):
		if self.held:
			text, self.held = self.held, ""
			self.emit(unmask(text, self.vault))


class ShieldedClient:
	"""Wraps a provider client: masks what goes out, unmasks what comes back. Same interface."""

	def __init__(self, inner, vault: Vault | None = None):
		self.inner = inner
		self.vault = vault or Vault()

	def stream(self, payload, on_text=None, on_block=None, should_stop=None):
		unmasker = StreamUnmasker(self.vault, on_text) if on_text else None

		def block_out(block):
			if on_block:
				on_block(unmask_message({"content": [block]}, self.vault)["content"][0])

		try:
			message = self.inner.stream(
				mask_payload(payload, self.vault),
				on_text=unmasker.feed if unmasker else None,
				on_block=block_out if on_block else None,
				should_stop=should_stop,
			)
		finally:
			if unmasker:
				unmasker.flush()
		return unmask_message(message, self.vault)

	def __getattr__(self, name):
		return getattr(self.inner, name)
