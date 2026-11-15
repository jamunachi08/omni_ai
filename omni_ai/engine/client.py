"""Minimal streaming client for the Claude Messages API.

Uses `requests` (already a Frappe dependency) so the app adds no Python
packages and installs cleanly on Frappe Cloud, self-hosted and on-prem
benches alike.
"""

import json
import time

import requests

API_VERSION = "2023-06-01"
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
MAX_RETRIES = 3


class ProviderError(Exception):
	"""Error returned by the AI provider, with a message that is safe to show users."""

	def __init__(self, message, status=None, kind=None):
		super().__init__(message)
		self.status = status
		self.kind = kind


class Stopped(Exception):
	"""The user pressed Stop."""


def _friendly(status, kind, message) -> str:
	if status == 401:
		return "The AI provider rejected the API key. Ask your administrator to check the assistant settings."
	if status == 403:
		return "The API key is not allowed to use this model or feature."
	if status == 404:
		return f"The configured model was not found. Ask your administrator to check the model name. ({message})"
	if status == 400:
		return f"The AI provider could not process this request. ({message})" if message else "The AI provider could not process this request."
	if status == 413:
		return "The request was too large. Try a smaller file or start a new conversation."
	if status == 429:
		return "The AI provider is rate limiting requests. Please wait a moment and try again."
	if status in (500, 502, 503, 504, 529) or kind == "overloaded_error":
		return "The AI provider is temporarily unavailable. Please try again shortly."
	return message or "The AI provider returned an error."


class ClaudeClient:
	def __init__(self, api_key: str, base_url: str = "https://api.anthropic.com", timeout: int = 600):
		self.api_key = api_key
		self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")
		self.timeout = timeout or 600

	# -- low level ------------------------------------------------------

	def _headers(self):
		return {
			"x-api-key": self.api_key,
			"anthropic-version": API_VERSION,
			"content-type": "application/json",
			"accept": "text/event-stream",
		}

	def _url(self, path: str) -> str:
		base = self.base_url
		if base.endswith("/v1"):
			base = base[:-3]
		return f"{base}{path}"

	def list_models(self) -> list:
		resp = requests.get(self._url("/v1/models"), headers=self._headers(), timeout=30)
		if resp.status_code != 200:
			raise self._error(resp)
		return resp.json().get("data", [])

	def _error(self, resp) -> ProviderError:
		kind, message = None, ""
		try:
			body = resp.json()
			err = body.get("error") or {}
			kind, message = err.get("type"), err.get("message") or ""
		except Exception:
			message = (resp.text or "")[:300]
		return ProviderError(_friendly(resp.status_code, kind, message), status=resp.status_code, kind=kind)

	# -- streaming ------------------------------------------------------

	def stream(self, payload: dict, on_text=None, on_block=None, should_stop=None) -> dict:
		"""Send one request and return the assembled message.

		on_text(delta)        called for every text delta
		on_block(block)       called when a content block starts (tool use, web search, ...)
		should_stop() -> bool polled while streaming; raises Stopped when true
		"""
		if not self.api_key:
			raise ProviderError(
				"No API key is configured. Ask your administrator to add one in the assistant settings."
			)

		payload = dict(payload, stream=True)
		attempt = 0
		while True:
			attempt += 1
			started_output = False
			try:
				resp = requests.post(
					self._url("/v1/messages"),
					headers=self._headers(),
					data=json.dumps(payload),
					stream=True,
					timeout=(20, self.timeout),
				)
			except requests.RequestException as exc:
				if attempt <= MAX_RETRIES:
					time.sleep(min(2**attempt, 10))
					continue
				raise ProviderError(
					f"Could not reach the AI provider: {type(exc).__name__}", kind="connection_error"
				) from exc

			if resp.status_code != 200:
				err = self._error(resp)
				retry_after = resp.headers.get("retry-after")
				resp.close()
				if resp.status_code in RETRY_STATUS and attempt <= MAX_RETRIES:
					delay = min(float(retry_after), 30) if _is_number(retry_after) else min(2**attempt, 15)
					time.sleep(delay)
					continue
				raise err

			try:
				return self._consume(resp, on_text, on_block, should_stop)
			except _RetryableStreamError as exc:
				started_output = exc.started_output
				if not started_output and attempt <= MAX_RETRIES:
					time.sleep(min(2**attempt, 10))
					continue
				raise ProviderError(_friendly(529, exc.kind, str(exc)), kind=exc.kind) from exc
			finally:
				resp.close()

	def _consume(self, resp, on_text, on_block, should_stop) -> dict:
		message = {"role": "assistant", "content": [], "stop_reason": None, "usage": {}, "model": None}
		blocks = {}  # index -> block being built
		partial_json = {}  # index -> accumulated tool input JSON
		started_output = False
		event_data = []

		def handle(data: dict):
			nonlocal started_output
			kind = data.get("type")
			if kind == "message_start":
				msg = data.get("message") or {}
				message["model"] = msg.get("model")
				message["id"] = msg.get("id")
				message["usage"] = dict(msg.get("usage") or {})
			elif kind == "content_block_start":
				idx = data.get("index", len(blocks))
				block = dict(data.get("content_block") or {})
				blocks[idx] = block
				if block.get("type") in ("tool_use", "server_tool_use"):
					partial_json[idx] = ""
				started_output = True
				if on_block:
					on_block(block)
			elif kind == "content_block_delta":
				idx = data.get("index")
				block = blocks.get(idx)
				delta = data.get("delta") or {}
				dtype = delta.get("type")
				if block is None:
					return
				if dtype == "text_delta":
					text = delta.get("text") or ""
					block["text"] = (block.get("text") or "") + text
					started_output = True
					if on_text and text:
						on_text(text)
				elif dtype == "input_json_delta":
					partial_json[idx] = partial_json.get(idx, "") + (delta.get("partial_json") or "")
				elif dtype == "thinking_delta":
					block["thinking"] = (block.get("thinking") or "") + (delta.get("thinking") or "")
				elif dtype == "signature_delta":
					block["signature"] = (block.get("signature") or "") + (delta.get("signature") or "")
				elif dtype == "citations_delta":
					block.setdefault("citations", []).append(delta.get("citation"))
			elif kind == "content_block_stop":
				idx = data.get("index")
				block = blocks.get(idx)
				if block is not None and idx in partial_json:
					raw = partial_json.pop(idx)
					try:
						block["input"] = json.loads(raw) if raw.strip() else (block.get("input") or {})
					except Exception:
						block["input"] = {}
						block["_invalid_json"] = raw[:2000]
			elif kind == "message_delta":
				delta = data.get("delta") or {}
				if delta.get("stop_reason"):
					message["stop_reason"] = delta["stop_reason"]
				usage = data.get("usage") or {}
				for key, value in usage.items():
					if value is not None:
						message["usage"][key] = value
			elif kind == "error":
				err = data.get("error") or {}
				raise _RetryableStreamError(
					err.get("message") or "stream error", err.get("type"), started_output
				)

		try:
			for raw in resp.iter_lines(decode_unicode=False):
				if should_stop and should_stop():
					raise Stopped()
				if raw is None:
					continue
				line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
				if not line:
					if event_data:
						chunk = "\n".join(event_data)
						event_data = []
						try:
							handle(json.loads(chunk))
						except json.JSONDecodeError:
							pass
					continue
				if line.startswith("data:"):
					event_data.append(line[5:].lstrip())
			if event_data:
				try:
					handle(json.loads("\n".join(event_data)))
				except json.JSONDecodeError:
					pass
		except Stopped:
			message["stop_reason"] = "user_stopped"
			for idx in list(partial_json):
				# a tool call cut off mid-way is unusable; drop it
				blocks.pop(idx, None)
		except requests.RequestException as exc:
			raise _RetryableStreamError(str(exc), "connection_error", started_output) from exc

		message["content"] = [blocks[i] for i in sorted(blocks)]
		if message["stop_reason"] is None:
			# Stream ended without a stop reason: treat as an interrupted reply.
			raise _RetryableStreamError("The reply was interrupted.", "incomplete", started_output)
		return message


class _RetryableStreamError(Exception):
	def __init__(self, message, kind=None, started_output=False):
		super().__init__(message)
		self.kind = kind
		self.started_output = started_output


def _is_number(value) -> bool:
	try:
		float(value)
		return True
	except (TypeError, ValueError):
		return False
