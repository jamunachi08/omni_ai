"""Tool plumbing: the context handed to every tool, the result store and helpers."""

import datetime
import decimal
import time

import frappe

from omni_ai.utils import dumps, get_settings, to_int

RESULT_TTL = 60 * 60 * 6
MODEL_PREVIEW_ROWS = 30
MAX_RESULT_CHARS = 30000
UI_TABLE_ROWS = 300


class ToolError(Exception):
	"""A problem the model should see and can usually fix by itself (bad field, no permission...)."""

	def __init__(self, message, denied=False):
		super().__init__(message)
		self.denied = denied


class Tool:
	def __init__(self, name, description, schema, handler, label=None, enabled=None):
		self.name = name
		self.description = " ".join(description.split())
		self.schema = schema
		self.handler = handler
		self.label = label or name.replace("_", " ").capitalize()
		self.enabled = enabled  # callable(ctx) -> bool, or None for always

	def definition(self) -> dict:
		return {"name": self.name, "description": self.description, "input_schema": self.schema}


class ToolContext:
	"""Everything a tool needs. One instance per assistant reply."""

	def __init__(self, user, conversation, stream=None, parts=None):
		self.user = user
		self.conversation = conversation
		self.stream = stream
		self.parts = parts if parts is not None else []
		self.settings = get_settings()
		self.max_rows = max(1, to_int(self.settings.max_rows, 5000) or 5000)
		self.created_actions = []

	# -- display parts --------------------------------------------------

	def add_part(self, part: dict) -> dict:
		"""Append something the user sees (chart, table, action card, file) and stream it."""
		part.setdefault("id", frappe.generate_hash(length=8))
		self.parts.append(part)
		if self.stream:
			self.stream.emit("part", p=part)
		return part

	# -- result store ---------------------------------------------------

	def _result_key(self, result_id: str) -> str:
		return f"omni_ai:res:{self.conversation}:{result_id}"

	def store_result(self, columns: list, rows: list, title: str = "") -> str:
		"""Keep a full result set server-side so later tools (table, chart, export) can reuse it
		without the model having to repeat the rows."""
		result_id = "r" + frappe.generate_hash(length=5)
		frappe.cache.set_value(
			self._result_key(result_id),
			{"columns": columns, "rows": rows, "title": title},
			expires_in_sec=RESULT_TTL,
		)
		return result_id

	def get_result(self, result_id: str) -> dict:
		from omni_ai.engine.stream import fresh_cache_value

		data = fresh_cache_value(self._result_key(str(result_id or "").strip()))
		if not data:
			raise ToolError(
				f"Result '{result_id}' is not available any more (results expire after a few hours). "
				"Run the query again to get a fresh result_id."
			)
		return data


# -- value helpers ----------------------------------------------------------


def clean_value(value):
	"""Make a DB value JSON friendly and compact."""
	if value is None:
		return None
	if isinstance(value, bool):
		return int(value)
	if isinstance(value, (int, str)):
		return value
	if isinstance(value, float):
		return round(value, 6)
	if isinstance(value, decimal.Decimal):
		return float(round(value, 6))
	if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
		return str(value)
	if isinstance(value, datetime.timedelta):
		return str(value)
	if isinstance(value, bytes):
		return f"<{len(value)} bytes>"
	return str(value)


def clean_rows(rows) -> list:
	return [[clean_value(v) for v in row] for row in rows]


def columns_from_names(names) -> list:
	return [{"fieldname": str(n), "label": frappe.unscrub(str(n)) if str(n).islower() else str(n)} for n in names]


def tabular_reply(ctx: ToolContext, columns: list, rows: list, title: str = "", preview: int = None, extra=None) -> dict:
	"""Standard shape for any tool that returns rows: store everything, show the model a sample."""
	rows = clean_rows(rows)
	truncated = len(rows) > ctx.max_rows
	if truncated:
		rows = rows[: ctx.max_rows]
	result_id = ctx.store_result(columns, rows, title)
	preview = max(1, min(to_int(preview, MODEL_PREVIEW_ROWS) or MODEL_PREVIEW_ROWS, 200))
	sample = rows[:preview]
	# keep the sample inside a sane size even with very wide rows
	while sample and len(dumps(sample)) > MAX_RESULT_CHARS:
		sample = sample[: max(1, len(sample) // 2)]
	reply = {
		"result_id": result_id,
		"total_rows": len(rows),
		"columns": [c.get("label") or c.get("fieldname") for c in columns],
		"rows_shown": len(sample),
		"rows": sample,
	}
	if len(sample) < len(rows):
		reply["note"] = (
			f"Only the first {len(sample)} of {len(rows)} rows are shown here. The full result is stored as "
			f"result_id '{result_id}': pass it to show_table, show_chart or export_data instead of copying rows."
		)
	if truncated:
		reply["truncated"] = f"The result was cut at the {ctx.max_rows}-row limit. Add filters or aggregate."
	if extra:
		reply.update(extra)
	return reply


class Timer:
	def __enter__(self):
		self.start = time.monotonic()
		return self

	def __exit__(self, *exc):
		self.ms = int((time.monotonic() - self.start) * 1000)
		return False
