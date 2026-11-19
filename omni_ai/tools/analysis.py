"""Calculations on a query result, done by code rather than estimated by the model:
unusual values, a simple trend forecast, a summary, and who makes up most of a total."""

import statistics

from omni_ai.tools.base import Tool, ToolError

MAX_FORECAST = 12


def _column(data: dict, wanted: str, label: str) -> int:
	wanted = str(wanted or "").strip().lower()
	if not wanted:
		raise ToolError(f"{label} is required.")
	for index, column in enumerate(data["columns"]):
		if wanted in (str(column.get("fieldname") or "").lower(), str(column.get("label") or "").lower()):
			return index
	names = ", ".join(str(c.get("fieldname") or c.get("label")) for c in data["columns"])
	raise ToolError(f"There is no column '{wanted}' in this result. Columns: {names}.")


def _number(value):
	if isinstance(value, bool) or value is None:
		return None
	if isinstance(value, (int, float)):
		return float(value)
	try:
		return float(str(value).replace(",", ""))
	except ValueError:
		return None


def _round(value: float) -> float:
	return round(value, 2) if abs(value) >= 1 else round(value, 4)


def summary(values: list) -> dict:
	return {
		"count": len(values),
		"total": _round(sum(values)),
		"average": _round(statistics.fmean(values)),
		"median": _round(statistics.median(values)),
		"smallest": _round(min(values)),
		"largest": _round(max(values)),
		"spread": _round(statistics.pstdev(values)) if len(values) > 1 else 0,
	}


def outliers(points: list, threshold: float = 3.5) -> list:
	"""points: [(value, row_index)]. Indexes of values far from the typical one, most extreme first.
	Uses the median and the median absolute deviation, which one huge value cannot distort."""
	values = [v for v, _i in points]
	if len(values) < 8:
		return []
	middle = statistics.median(values)
	deviation = statistics.median([abs(v - middle) for v in values])
	scored = []
	if deviation > 0:
		for value, index in points:
			score = 0.6745 * (value - middle) / deviation
			if abs(score) >= threshold:
				scored.append((abs(score), index, value, middle))
	else:
		spread = statistics.pstdev(values)
		if spread > 0:
			mean = statistics.fmean(values)
			for value, index in points:
				score = (value - mean) / spread
				if abs(score) >= 3 and value != middle:
					scored.append((abs(score), index, value, middle))
	scored.sort(reverse=True)
	return scored


def trend(values: list) -> dict:
	"""Least-squares straight line through values in their given order."""
	n = len(values)
	xs = list(range(n))
	mean_x, mean_y = (n - 1) / 2, statistics.fmean(values)
	sxx = sum((x - mean_x) ** 2 for x in xs)
	slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values, strict=False)) / sxx if sxx else 0.0
	intercept = mean_y - slope * mean_x
	residuals = [y - (intercept + slope * x) for x, y in zip(xs, values, strict=False)]
	total = sum((y - mean_y) ** 2 for y in values)
	fit = 1 - sum(r * r for r in residuals) / total if total else 1.0
	error = (sum(r * r for r in residuals) / (n - 2)) ** 0.5 if n > 2 else 0.0
	return {"slope": slope, "intercept": intercept, "fit": fit, "error": error}


def analyze_data(args, ctx):
	data = ctx.get_result(args.get("result_id"))
	kind = str(args.get("analysis") or "").lower()
	value_index = _column(data, args.get("value_column"), "value_column")
	rows = data["rows"]
	points = [(v, i) for i, v in ((i, _number(row[value_index])) for i, row in enumerate(rows)) if v is not None]
	if not points:
		raise ToolError("That column has no numbers.")
	labels = [i for i in range(len(data["columns"])) if i != value_index][:4]

	def describe(index: int) -> dict:
		return {str(data["columns"][i].get("label") or data["columns"][i].get("fieldname")): rows[index][i] for i in labels}

	if kind == "summary":
		return {"summary": summary([v for v, _i in points])}

	if kind == "outliers":
		groups = {}
		if args.get("group_column"):
			group_index = _column(data, args.get("group_column"), "group_column")
			for value, index in points:
				groups.setdefault(rows[index][group_index], []).append((value, index))
		else:
			groups[None] = points
		found, skipped = [], 0
		for members in groups.values():
			if len(members) < 8:
				skipped += 1
				continue
			found.extend(outliers(members))
		found.sort(reverse=True)
		out = [
			{**describe(index), "value": _round(value), "typical": _round(typical), "times_typical": round(value / typical, 1) if typical else None}
			for _score, index, value, typical in found[:20]
		]
		note = f"{len(found)} unusual value(s) among {len(points)}." if found else f"No unusual values among {len(points)}."
		if skipped:
			note += f" {skipped} group(s) had fewer than 8 values and were not judged."
		return {"unusual": out, "note": note + " 'Unusual' means far from the typical value; it is a reason to look, not proof of an error."}

	if kind == "top_share":
		group_index = _column(data, args.get("group_column"), "group_column")
		totals = {}
		for value, index in points:
			totals[rows[index][group_index]] = totals.get(rows[index][group_index], 0.0) + value
		grand = sum(totals.values())
		if grand <= 0:
			raise ToolError("The total is zero, so shares cannot be calculated.")
		ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
		running, out, to_80 = 0.0, [], None
		for position, (name, total) in enumerate(ranked, 1):
			running += total
			if to_80 is None and running / grand >= 0.8:
				to_80 = position
			if position <= 10:
				out.append({"name": name, "total": _round(total), "share_percent": round(total / grand * 100, 1), "cumulative_percent": round(running / grand * 100, 1)})
		return {"top": out, "groups": len(ranked), "grand_total": _round(grand), "groups_making_80_percent": to_80}

	if kind == "forecast":
		values = [v for v, _i in points]
		if len(values) < 6:
			raise ToolError("A forecast needs at least 6 periods of history. Query more periods, oldest first.")
		periods = min(max(int(args.get("periods") or 3), 1), MAX_FORECAST)
		line = trend(values)
		floor = 0.0 if min(values) >= 0 else None
		out = []
		for step in range(1, periods + 1):
			centre = line["intercept"] + line["slope"] * (len(values) - 1 + step)
			low, high = centre - 1.96 * line["error"], centre + 1.96 * line["error"]
			if floor is not None:
				centre, low, high = max(centre, floor), max(low, floor), max(high, floor)
			out.append({"period_ahead": step, "expected": _round(centre), "low": _round(low), "high": _round(high)})
		quality = "follows a clear trend" if line["fit"] >= 0.6 else "moves around a lot, so the forecast is weak" if line["fit"] < 0.3 else "follows a loose trend"
		return {
			"forecast": out,
			"history_periods": len(values),
			"last_value": _round(values[-1]),
			"change_per_period": _round(line["slope"]),
			"fit": round(line["fit"], 2),
			"note": f"A straight-line trend over {len(values)} periods; the history {quality}. It ignores seasons, promotions and "
			"anything not in the numbers. Present it as an estimate with its range, never as a fact.",
		}

	raise ToolError("analysis must be one of: summary, outliers, top_share, forecast.")


TOOLS = [
	Tool(
		"analyze_data",
		"""Calculate on a query result (by result_id) instead of estimating:
		- outliers: rows whose value is far from the typical one (optionally within each group_column, e.g. per
		  item or per supplier). Use for "anything unusual / suspicious / wrong?" questions.
		- forecast: a straight-line trend forecast for the next periods. The result must have one row per period,
		  oldest first (for example monthly totals for the last 12 months).
		- top_share: which groups make up most of a total (group_column), with shares.
		- summary: count, total, average, median, smallest, largest.
		Query the data first, with enough rows (at least 8 per group for outliers, 6 periods for a forecast).""",
		{
			"type": "object",
			"properties": {
				"result_id": {"type": "string"},
				"analysis": {"type": "string", "enum": ["outliers", "forecast", "top_share", "summary"]},
				"value_column": {"type": "string", "description": "The numeric column to analyse (fieldname or label)."},
				"group_column": {"type": "string", "description": "outliers: judge within each group. top_share: what to group by."},
				"periods": {"type": "integer", "description": "forecast: how many periods ahead (1 to 12, default 3)."},
			},
			"required": ["result_id", "analysis", "value_column"],
		},
		analyze_data,
		label="Calculating",
	)
]
