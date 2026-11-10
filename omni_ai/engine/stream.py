"""Run event bus.

Every run (one assistant reply) writes an ordered list of events to Redis and
pushes the same events over Frappe's realtime channel. The browser listens on
realtime for smooth streaming and falls back to polling the Redis list, so
the assistant works on any hosting setup, including ones where websockets are
blocked or misconfigured.
"""

import json
import time

import frappe

EVENT = "omni_ai_event"
TTL = 60 * 60 * 2  # seconds a finished run stays readable (for reconnects)
FLUSH_INTERVAL = 0.06  # coalesce text deltas so we don't flood Redis / sockets


def _events_key(run_id: str) -> str:
	return f"omni_ai:run:{run_id}:events"


def _state_key(run_id: str) -> str:
	return f"omni_ai:run:{run_id}:state"


def _redis_expire(key: str, seconds: int):
	"""Expire a raw list key. RedisWrapper prefixes list keys itself, so we do the same."""
	try:
		frappe.cache.expire(frappe.cache.make_key(key), seconds)
	except Exception:
		pass


def set_state(run_id: str, **values):
	state = get_state(run_id)
	state.update(values)
	frappe.cache.set_value(_state_key(run_id), state, expires_in_sec=TTL)
	return state


def fresh_cache_value(key: str):
	"""Read a cache value straight from Redis.

	Run state is written by one process (the web request that presses Stop) and read by another
	(the worker producing the reply). Frappe keeps a per-process copy of cache values, and v16 also
	fills it on writes, so the normal getter can return a stale copy. This bypasses it on v15 and v16.
	"""
	import pickle

	try:
		raw = frappe.cache.get(frappe.cache.make_key(key))
	except Exception:
		return None
	if raw is None:
		return None
	try:
		return pickle.loads(raw)
	except Exception:
		return None


def get_state(run_id: str) -> dict:
	return fresh_cache_value(_state_key(run_id)) or {}


def request_stop(run_id: str):
	set_state(run_id, stop=1)


def stop_requested(run_id: str) -> bool:
	return bool(get_state(run_id).get("stop"))


def read_events(run_id: str, after: int = 0) -> list:
	"""Events with seq > after."""
	raw = frappe.cache.lrange(_events_key(run_id), int(after), -1) or []
	out = []
	for item in raw:
		if isinstance(item, bytes):
			item = item.decode("utf-8")
		try:
			out.append(json.loads(item))
		except Exception:
			continue
	return out


class RunStream:
	"""Emits events for one run. Single writer, so plain counters are enough."""

	def __init__(self, run_id: str, user: str, conversation: str):
		self.run_id = run_id
		self.user = user
		self.conversation = conversation
		self.seq = 0
		self._text_buffer = []
		self._last_flush = 0.0
		self._pending = []
		self._last_stop_check = 0.0
		self._stopped = False

	# -- public API -----------------------------------------------------

	def text(self, delta: str):
		if not delta:
			return
		self._text_buffer.append(delta)
		if time.monotonic() - self._last_flush >= FLUSH_INTERVAL:
			self.flush()

	def emit(self, kind: str, **data):
		"""Queue a non-text event; flushes any buffered text first to keep ordering."""
		self._flush_text()
		self._push({"t": kind, **data})
		self._publish()

	def flush(self):
		self._flush_text()
		self._publish()

	def should_stop(self) -> bool:
		if self._stopped:
			return True
		now = time.monotonic()
		if now - self._last_stop_check < 0.5:
			return False
		self._last_stop_check = now
		self._stopped = stop_requested(self.run_id)
		return self._stopped

	# -- internals ------------------------------------------------------

	def _flush_text(self):
		if self._text_buffer:
			self._push({"t": "text", "d": "".join(self._text_buffer)})
			self._text_buffer = []
		self._last_flush = time.monotonic()

	def _push(self, event: dict):
		self.seq += 1
		event["seq"] = self.seq
		try:
			frappe.cache.rpush(_events_key(self.run_id), json.dumps(event, default=str))
			if self.seq == 1:
				_redis_expire(_events_key(self.run_id), TTL)
		except Exception:
			# Redis hiccup: realtime delivery below still works.
			pass
		self._pending.append(event)

	def _publish(self):
		if not self._pending:
			return
		events, self._pending = self._pending, []
		try:
			frappe.publish_realtime(
				EVENT,
				{"run_id": self.run_id, "conversation": self.conversation, "events": events},
				user=self.user,
			)
		except Exception:
			# Polling picks the events up from Redis.
			pass
