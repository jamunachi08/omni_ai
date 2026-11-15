"""Client for OpenAI-compatible chat-completion APIs (OpenAI, OpenRouter, Ollama, vLLM, LM Studio...).

The rest of the app speaks one message format (the Claude Messages format). This client translates
a request into the chat-completions format, streams the answer and hands back a message in the
app's own format, so nothing else needs to know which provider is in use.

Not available with this provider: web search, prompt caching and the reasoning-effort setting.
"""

import base64
import io
import json
import time

import requests

from omni_ai.engine.client import MAX_RETRIES, RETRY_STATUS, ProviderError, Stopped, _friendly, _is_number

FINISH = {"stop": "end_turn", "tool_calls": "tool_use", "function_call": "tool_use", "length": "max_tokens", "content_filter": "refusal"}
MAX_PDF_CHARS = 120_000


def _pdf_text(data_b64: str, title: str) -> str:
	"""These APIs do not take PDFs, so pass the text of the file instead."""
	try:
		from pypdf import PdfReader

		reader = PdfReader(io.BytesIO(base64.b64decode(data_b64)))
		text = "\n".join((page.extract_text() or "") for page in reader.pages[:200]).strip()
	except Exception:
		text = ""
	if not text:
		return f"[PDF '{title}': no readable text could be extracted (it may be a scan).]"
	return f"Attached PDF: {title}\n\n{text[:MAX_PDF_CHARS]}"


def to_chat_messages(system, messages: list) -> list:
	"""Claude-format messages -> chat-completions messages."""
	out = []
	if system:
		if isinstance(system, list):
			system = "\n\n".join(b.get("text", "") for b in system if b.get("type") == "text")
		out.append({"role": "system", "content": system})
	for message in messages:
		content = message.get("content")
		if isinstance(content, str):
			content = [{"type": "text", "text": content}]
		if message.get("role") == "assistant":
			text, calls = [], []
			for block in content or []:
				if block.get("type") == "text" and block.get("text"):
					text.append(block["text"])
				elif block.get("type") == "tool_use":
					calls.append(
						{
							"id": block.get("id"),
							"type": "function",
							"function": {"name": block.get("name"), "arguments": json.dumps(block.get("input") or {})},
						}
					)
			item = {"role": "assistant", "content": "\n\n".join(text) or None}
			if calls:
				item["tool_calls"] = calls
			if item["content"] or calls:
				out.append(item)
			continue
		parts, has_media = [], False
		for block in content or []:
			kind = block.get("type")
			if kind == "tool_result":
				result = block.get("content")
				if not isinstance(result, str):
					result = json.dumps(result, default=str)
				if block.get("is_error"):
					result = "ERROR: " + result
				out.append({"role": "tool", "tool_call_id": block.get("tool_use_id"), "content": result})
			elif kind == "text":
				parts.append({"type": "text", "text": block.get("text") or ""})
			elif kind == "image":
				source = block.get("source") or {}
				parts.append(
					{"type": "image_url", "image_url": {"url": f"data:{source.get('media_type')};base64,{source.get('data')}"}}
				)
				has_media = True
			elif kind == "document":
				source = block.get("source") or {}
				parts.append({"type": "text", "text": _pdf_text(source.get("data") or "", block.get("title") or "document")})
		if parts:
			# plain string content is the most widely accepted form; use parts only when there is an image
			out.append({"role": "user", "content": parts if has_media else "\n\n".join(p["text"] for p in parts)})
	return out


def to_chat_tools(tools: list) -> list:
	out = []
	for tool in tools or []:
		if tool.get("type") and tool.get("type") != "custom":
			continue  # provider-side tools such as web search exist only on Claude
		out.append(
			{
				"type": "function",
				"function": {
					"name": tool["name"],
					"description": tool.get("description") or "",
					"parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
				},
			}
		)
	return out


class OpenAICompatClient:
	def __init__(self, api_key: str, base_url: str, timeout: int = 600):
		self.api_key = api_key or ""
		self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
		self.timeout = timeout or 600

	def _headers(self):
		headers = {"content-type": "application/json", "accept": "text/event-stream"}
		if self.api_key:
			headers["authorization"] = f"Bearer {self.api_key}"
		return headers

	def _error(self, resp) -> ProviderError:
		kind, message = None, ""
		try:
			err = resp.json().get("error") or {}
			if isinstance(err, str):
				message = err
			else:
				kind, message = err.get("type") or err.get("code"), err.get("message") or ""
		except Exception:
			message = (resp.text or "")[:300]
		return ProviderError(_friendly(resp.status_code, kind, message), status=resp.status_code, kind=kind)

	def list_models(self) -> list:
		resp = requests.get(f"{self.base_url}/models", headers=self._headers(), timeout=30)
		if resp.status_code != 200:
			raise self._error(resp)
		return resp.json().get("data", [])

	def stream(self, payload: dict, on_text=None, on_block=None, should_stop=None) -> dict:
		body = {
			"model": payload.get("model"),
			"messages": to_chat_messages(payload.get("system"), payload.get("messages") or []),
			"max_tokens": payload.get("max_tokens"),
			"stream": True,
			"stream_options": {"include_usage": True},
		}
		tools = to_chat_tools(payload.get("tools"))
		if tools:
			body["tools"] = tools
		attempt, adjusted = 0, set()
		while True:
			attempt += 1
			try:
				resp = requests.post(
					f"{self.base_url}/chat/completions",
					headers=self._headers(),
					data=json.dumps(body),
					stream=True,
					timeout=(20, self.timeout),
				)
			except requests.RequestException as exc:
				if attempt <= MAX_RETRIES:
					time.sleep(min(2**attempt, 10))
					continue
				raise ProviderError(f"Could not reach the AI provider: {type(exc).__name__}", kind="connection_error") from exc
			if resp.status_code != 200:
				err = self._error(resp)
				text = str(err).lower()
				retry_after = resp.headers.get("retry-after")
				resp.close()
				# servers differ on two optional fields; adapt once instead of failing
				if resp.status_code == 400 and "max_tokens" in text and "max_tokens" not in adjusted:
					adjusted.add("max_tokens")
					body["max_completion_tokens"] = body.pop("max_tokens")
					attempt -= 1
					continue
				if resp.status_code in (400, 422) and "stream_options" in text and "stream_options" not in adjusted:
					adjusted.add("stream_options")
					body.pop("stream_options", None)
					attempt -= 1
					continue
				if resp.status_code in RETRY_STATUS and attempt <= MAX_RETRIES:
					time.sleep(min(float(retry_after), 30) if _is_number(retry_after) else min(2**attempt, 15))
					continue
				raise err
			try:
				return self._consume(resp, payload.get("model"), on_text, on_block, should_stop)
			except requests.RequestException as exc:
				raise ProviderError(_friendly(529, "connection_error", str(exc)), kind="connection_error") from exc
			finally:
				resp.close()

	def _consume(self, resp, model, on_text, on_block, should_stop) -> dict:
		text, calls, order = [], {}, []
		finish, usage, reported_model = None, {}, model
		stopped = False
		for raw in resp.iter_lines(decode_unicode=False):
			if should_stop and should_stop():
				stopped = True
				break
			line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else (raw or "")
			if not line.startswith("data:"):
				continue
			data = line[5:].strip()
			if not data or data == "[DONE]":
				continue
			try:
				chunk = json.loads(data)
			except json.JSONDecodeError:
				continue
			if chunk.get("error"):
				err = chunk["error"]
				raise ProviderError(_friendly(529, None, err.get("message") if isinstance(err, dict) else str(err)))
			reported_model = chunk.get("model") or reported_model
			if chunk.get("usage"):
				usage = chunk["usage"]
			for choice in chunk.get("choices") or []:
				delta = choice.get("delta") or {}
				if delta.get("content"):
					text.append(delta["content"])
					if on_text:
						on_text(delta["content"])
				for call in delta.get("tool_calls") or []:
					index = call.get("index", len(order))
					if index not in calls:
						calls[index] = {"id": call.get("id") or f"call_{int(time.time() * 1000)}_{index}", "name": "", "arguments": "", "announced": False}
						order.append(index)
					entry = calls[index]
					fn = call.get("function") or {}
					if call.get("id"):
						entry["id"] = call["id"]
					if fn.get("name"):
						entry["name"] += fn["name"]
					if fn.get("arguments"):
						entry["arguments"] += fn["arguments"] if isinstance(fn["arguments"], str) else json.dumps(fn["arguments"])
					if entry["name"] and not entry["announced"] and on_block:
						entry["announced"] = True
						on_block({"type": "tool_use", "id": entry["id"], "name": entry["name"], "input": {}})
				if choice.get("finish_reason"):
					finish = choice["finish_reason"]

		content = []
		if "".join(text).strip():
			content.append({"type": "text", "text": "".join(text)})
		if not stopped:
			for index in order:
				entry = calls[index]
				block = {"type": "tool_use", "id": entry["id"], "name": entry["name"]}
				try:
					block["input"] = json.loads(entry["arguments"]) if entry["arguments"].strip() else {}
					if not isinstance(block["input"], dict):
						raise ValueError
				except Exception:
					block["input"] = {}
					block["_invalid_json"] = entry["arguments"][:2000]
				content.append(block)
		has_calls = any(b["type"] == "tool_use" for b in content)
		if stopped:
			stop_reason = "user_stopped"
		elif has_calls:
			stop_reason = "tool_use"  # some servers report "stop" even when they return tool calls
		elif finish is None and not content:
			raise ProviderError("The AI provider returned an empty reply.", kind="incomplete")
		else:
			stop_reason = FINISH.get(finish or "stop", "end_turn")
		cached = ((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0
		return {
			"role": "assistant",
			"content": content,
			"stop_reason": stop_reason,
			"model": reported_model,
			"usage": {
				"input_tokens": max((usage.get("prompt_tokens") or 0) - cached, 0),
				"cache_read_input_tokens": cached,
				"output_tokens": usage.get("completion_tokens") or 0,
			},
		}


def embed(base_url: str, api_key: str, model: str, texts: list, timeout: int = 8) -> list:
	"""Vectors for `texts` from an OpenAI-compatible embeddings endpoint. Raises on failure."""
	headers = {"content-type": "application/json"}
	if api_key:
		headers["authorization"] = f"Bearer {api_key}"
	resp = requests.post(
		f"{(base_url or '').rstrip('/')}/embeddings", headers=headers, data=json.dumps({"model": model, "input": texts}), timeout=timeout
	)
	if resp.status_code != 200:
		raise ProviderError(f"The embeddings service answered HTTP {resp.status_code}.", status=resp.status_code)
	data = sorted(resp.json().get("data") or [], key=lambda d: d.get("index", 0))
	vectors = [d.get("embedding") for d in data]
	if len(vectors) != len(texts) or not all(isinstance(v, list) and v for v in vectors):
		raise ProviderError("The embeddings service returned an unexpected reply.")
	return vectors
