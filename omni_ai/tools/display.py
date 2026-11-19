"""Things the assistant shows rather than says: tables, charts and downloadable files."""

import csv
import io
import re

import frappe

from omni_ai.tools.base import UI_TABLE_ROWS, Tool, ToolError, clean_rows
from omni_ai.utils import clip

CHART_TYPES = ("bar", "line", "pie", "donut", "percentage")
MAX_CHART_POINTS = 60
MAX_SERIES = 8
MAX_EXPORT_ROWS = 50000
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value):
	"""Text that a spreadsheet would run as a formula gets a leading apostrophe (record contents are data)."""
	if isinstance(value, str) and value.startswith(FORMULA_START):
		return "'" + value
	return value


def _dataset(args, ctx) -> tuple:
	"""(columns, rows, title) from a stored result_id or from inline columns + rows."""
	if args.get("result_id"):
		data = ctx.get_result(args["result_id"])
		return data["columns"], data["rows"], data.get("title") or ""
	columns, rows = args.get("columns"), args.get("rows")
	if not columns or rows is None:
		raise ToolError("Pass either result_id, or columns and rows.")
	cols = [{"fieldname": frappe.scrub(str(c)), "label": str(c)} for c in columns]
	for row in rows:
		if not isinstance(row, (list, tuple)) or len(row) != len(cols):
			raise ToolError("Every row must be a list with one value per column.")
	return cols, clean_rows(rows), ""


def _column_index(columns, wanted) -> int:
	wanted_l = str(wanted).strip().lower()
	for i, col in enumerate(columns):
		if wanted_l in (str(col.get("label") or "").lower(), str(col.get("fieldname") or "").lower()):
			return i
	names = ", ".join(str(c.get("label") or c.get("fieldname")) for c in columns)
	raise ToolError(f"Column '{wanted}' is not in the result. Available columns: {names}.")


def show_table(args, ctx):
	columns, rows, stored_title = _dataset(args, ctx)
	if args.get("columns_to_show") and args.get("result_id"):
		keep = [_column_index(columns, c) for c in args["columns_to_show"]]
		columns = [columns[i] for i in keep]
		rows = [[row[i] for i in keep] for row in rows]
	total = len(rows)
	ctx.add_part(
		{
			"type": "table",
			"title": clip(args.get("title") or stored_title, 140),
			"columns": columns,
			"rows": rows[:UI_TABLE_ROWS],
			"total_rows": total,
			"result_id": args.get("result_id"),
		}
	)
	note = f"The table with {min(total, UI_TABLE_ROWS)} rows is now displayed to the user. Do not repeat its rows in text; summarise the key points."
	if total > UI_TABLE_ROWS:
		note += f" Only the first {UI_TABLE_ROWS} of {total} rows are shown; offer export_data for the full set."
	return {"status": "displayed", "note": note}


def show_chart(args, ctx):
	chart_type = str(args.get("chart_type") or "bar").lower()
	if chart_type not in CHART_TYPES:
		raise ToolError(f"chart_type must be one of: {', '.join(CHART_TYPES)}.")
	columns, rows, stored_title = _dataset(args, ctx)
	if not rows:
		raise ToolError("There is no data to chart.")
	label_idx = _column_index(columns, args.get("label_column") or (columns[0].get("label") or columns[0].get("fieldname")))
	value_columns = args.get("value_columns")
	if not value_columns:
		value_columns = [
			c.get("label") or c.get("fieldname")
			for i, c in enumerate(columns)
			if i != label_idx and any(isinstance(r[i], (int, float)) and not isinstance(r[i], bool) for r in rows)
		]
	if not value_columns:
		raise ToolError("No numeric column was found to chart. Name them in value_columns.")
	value_idx = [_column_index(columns, c) for c in value_columns][:MAX_SERIES]
	if chart_type in ("pie", "donut", "percentage"):
		value_idx = value_idx[:1]

	note = ""
	if len(rows) > MAX_CHART_POINTS:
		if chart_type in ("pie", "donut", "percentage"):
			raise ToolError(f"Too many slices ({len(rows)}). Aggregate to at most 12 groups, combining the rest as 'Other'.")
		rows = rows[:MAX_CHART_POINTS]
		note = f" Only the first {MAX_CHART_POINTS} points were charted; aggregate for a complete picture."

	labels = [clip("" if r[label_idx] is None else r[label_idx], 40) for r in rows]
	datasets = []
	for idx in value_idx:
		values = []
		for r in rows:
			v = r[idx]
			try:
				values.append(round(float(v), 4) if v not in (None, "") else 0)
			except (TypeError, ValueError):
				raise ToolError(f"Column '{columns[idx].get('label')}' contains non-numeric values and cannot be charted.") from None
		datasets.append({"name": str(columns[idx].get("label") or columns[idx].get("fieldname")), "values": values})

	ctx.add_part(
		{
			"type": "chart",
			"title": clip(args.get("title") or stored_title, 140),
			"chart_type": chart_type,
			"labels": labels,
			"datasets": datasets,
			"stacked": bool(args.get("stacked")),
			"y_label": clip(args.get("y_label") or "", 60),
		}
	)
	return {"status": "displayed", "note": "The chart is now displayed to the user. Describe the main takeaway in a sentence or two." + note}


def export_data(args, ctx):
	if not ctx.settings.enable_exports:
		raise ToolError("Exports are turned off in the assistant settings.", denied=True)
	columns, rows, stored_title = _dataset(args, ctx)
	fmt = str(args.get("format") or "xlsx").lower()
	if fmt not in ("xlsx", "csv"):
		raise ToolError("format must be 'xlsx' or 'csv'.")
	rows = rows[:MAX_EXPORT_ROWS]
	base = re.sub(r"[^\w\- ]+", "", str(args.get("filename") or stored_title or "export")).strip()[:80] or "export"
	header = [safe_cell(str(c.get("label") or c.get("fieldname"))) for c in columns]
	rows = [[safe_cell(v) for v in row] for row in rows]

	if fmt == "xlsx":
		from frappe.utils.xlsxutils import make_xlsx

		content = make_xlsx([header, *rows], base[:31] or "Data").getvalue()
	else:
		buffer = io.StringIO()
		writer = csv.writer(buffer)
		writer.writerow(header)
		writer.writerows(rows)
		content = ("﻿" + buffer.getvalue()).encode("utf-8")  # BOM so Excel opens Arabic and other scripts correctly

	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{base}.{fmt}",
			"is_private": 1,
			"content": content,
			"attached_to_doctype": "AI Assistant Conversation",
			"attached_to_name": ctx.conversation,
		}
	)
	file_doc.flags.ignore_permissions = True
	file_doc.insert(ignore_permissions=True)
	ctx.add_part(
		{
			"type": "file",
			"file_name": file_doc.file_name,
			"file_url": file_doc.file_url,
			"rows": len(rows),
			"format": fmt,
		}
	)
	return {"status": "created", "file_name": file_doc.file_name, "rows": len(rows), "note": "A download card is now shown to the user."}


DATA_PROPS = {
	"result_id": {"type": "string", "description": "result_id from query_data, run_sql, run_report or find_documents (preferred)."},
	"columns": {"type": "array", "items": {"type": "string"}, "description": "Only without result_id: column labels."},
	"rows": {"type": "array", "items": {"type": "array"}, "description": "Only without result_id: rows as lists (max a few hundred)."},
}

def show_kpis(args, ctx):
	items = args.get("items")
	if not isinstance(items, list) or not 1 <= len(items) <= 6:
		raise ToolError("items must be a list of 1 to 6 tiles.")
	tiles = []
	for item in items:
		if not isinstance(item, dict) or not str(item.get("label") or "").strip() or item.get("value") in (None, ""):
			raise ToolError("Every tile needs a label and a value.")
		trend = str(item.get("trend") or "").lower()
		tiles.append(
			{
				"label": clip(str(item["label"]), 60),
				"value": clip(str(item["value"]), 40),
				"note": clip(str(item.get("note") or ""), 80),
				"trend": trend if trend in ("up", "down", "flat") else "",
				"good": bool(item.get("good")) if item.get("good") is not None else None,
			}
		)
	if ctx.settings.verify_figures:
		# a headline number must come from a query result of this reply, like any figure in the answer
		from omni_ai.engine.grounding import collect_sources, untraced

		missing = untraced(" ; ".join(t["value"] for t in tiles), collect_sources([], ctx.results))
		if missing:
			raise ToolError(
				f"These tile values do not appear in a query result of this reply: {', '.join(missing)}. "
				"Compute each one with a query (an aggregate in query_data or run_sql) and use that value."
			)
	ctx.add_part({"type": "kpis", "title": clip(args.get("title") or "", 140), "items": tiles})
	return {"status": "displayed", "note": "The tiles are now shown to the user. Do not repeat the numbers in a list; add the insight in a sentence or two."}


TOOLS = [
	Tool(
		"show_kpis",
		"""Show up to six headline numbers as tiles (for example Revenue, Orders, Overdue amount) at the top
		of an answer. Use it for summaries and overviews where a few figures matter most. Every value must be
		a figure you obtained from a query in this reply, formatted as the user should read it (with currency
		or unit). Optional: note (a short comparison such as "vs 1.2M last month"), trend (up, down, flat) and
		good (true when the trend is good news, false when it is bad).""",
		{
			"type": "object",
			"properties": {
				"title": {"type": "string"},
				"items": {
					"type": "array",
					"items": {
						"type": "object",
						"properties": {
							"label": {"type": "string"},
							"value": {"type": "string"},
							"note": {"type": "string"},
							"trend": {"type": "string", "enum": ["up", "down", "flat"]},
							"good": {"type": "boolean"},
						},
						"required": ["label", "value"],
					},
				},
			},
			"required": ["items"],
		},
		show_kpis,
		label="Showing key figures",
	),
	Tool(
		"show_table",
		"""Display a result to the user as a sortable, searchable table with a download button. Use it whenever
		the answer is a list of more than a handful of rows instead of writing a long markdown table. Pass the
		result_id of an earlier tool result, so the rows do not have to be repeated.""",
		{
			"type": "object",
			"properties": {
				**DATA_PROPS,
				"title": {"type": "string"},
				"columns_to_show": {"type": "array", "items": {"type": "string"}, "description": "Optional subset/order of columns (with result_id)."},
			},
		},
		show_table,
		label="Preparing table",
	),
	Tool(
		"show_chart",
		"""Display a chart to the user. Choose bar for comparisons, line for trends over time, pie or donut for
		shares of a whole (12 slices or fewer). Aggregate the data first so there are at most about 60 points.
		Pass result_id plus the label column and the numeric value columns.""",
		{
			"type": "object",
			"properties": {
				**DATA_PROPS,
				"chart_type": {"type": "string", "enum": list(CHART_TYPES)},
				"title": {"type": "string"},
				"label_column": {"type": "string", "description": "Column for the x axis or slice names (default: first column)."},
				"value_columns": {"type": "array", "items": {"type": "string"}, "description": "Numeric columns to plot (default: all numeric)."},
				"stacked": {"type": "boolean"},
				"y_label": {"type": "string"},
			},
			"required": ["chart_type"],
		},
		show_chart,
		label="Drawing chart",
	),
	Tool(
		"export_data",
		"""Create an Excel (.xlsx) or CSV file from a result and give the user a download link. Use it when
		the user asks to export, download or get a spreadsheet, or when a result is too long to show.""",
		{
			"type": "object",
			"properties": {
				**DATA_PROPS,
				"format": {"type": "string", "enum": ["xlsx", "csv"]},
				"filename": {"type": "string", "description": "File name without extension."},
			},
		},
		export_data,
		label="Creating file",
		enabled=lambda ctx: bool(ctx.settings.enable_exports),
	),
]
