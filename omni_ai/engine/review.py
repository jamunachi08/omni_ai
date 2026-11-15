"""A second, independent look at a report draft before the user sees it.

The reviewer is a separate model request with one job: try to find a real defect in the report's
logic. It does not block anything. Its notes are shown on the draft card and handed back to the
assistant, which can redraft. If the review cannot run, the draft is shown without it.
"""

import json
import re

from omni_ai.utils import clip, dumps, get_api_key, get_settings, to_int

SYSTEM = """You review report definitions for an ERPNext / Frappe business system (MariaDB) before a user saves them.
Your job is to find real defects in the logic, not to restyle it. Try to refute the draft: assume it is wrong and look for
the evidence. Check in particular:
- double counting: a parent table joined to a child table (child.parent = parent.name) while summing or counting parent
  columns such as grand_total;
- cancelled or draft documents included when the description implies real business figures (missing docstatus = 1);
- a filter the description promises (company, period, customer) that the SQL does not apply, or applies to the wrong date;
- GROUP BY that does not match the selected columns, or a column that does not mean what its label says;
- mixed currencies summed together (grand_total across currencies instead of base_ amounts);
- a preview that contradicts the description (for example duplicate rows, impossible values).
Do not report style, naming, performance or missing features. If you are not reasonably sure something is a defect, leave it out.
Reply with JSON only, no prose: {"verdict": "ok" | "issues", "issues": ["one short sentence per defect, at most 4"]}"""


def parse_verdict(text: str) -> dict | None:
	"""The reviewer's reply as {"verdict", "issues"} if it matches the expected shape, else None."""
	match = re.search(r"\{.*\}", text or "", re.S)
	if not match:
		return None
	try:
		data = json.loads(match.group(0))
	except Exception:
		return None
	if not isinstance(data, dict) or data.get("verdict") not in ("ok", "issues"):
		return None
	issues = data.get("issues", [])
	if not isinstance(issues, list) or not all(isinstance(i, str) for i in issues):
		return None
	issues = [clip(" ".join(i.split()), 300) for i in issues if i.strip()][:4]
	if data["verdict"] == "issues" and not issues:
		return None
	return {"verdict": data["verdict"], "issues": issues if data["verdict"] == "issues" else []}


def review_report(payload: dict, columns: list, rows: list) -> dict:
	"""Returns {"status": "ok" | "issues" | "unavailable" | "skipped", "notes": [...]}."""
	settings = get_settings()
	if not settings.review_reports or payload.get("report_type") != "Query Report":
		return {"status": "skipped", "notes": []}
	try:
		from omni_ai.engine import agent  # looked up at call time so tests can replace the client

		client = agent.make_client(min(to_int(settings.request_timeout, 600) or 600, 120))
		request = {
			"report_name": payload.get("report_name"),
			"description": payload.get("description"),
			"main_doctype": payload.get("ref_doctype"),
			"sql": payload.get("query"),
			"filters": [{k: f.get(k) for k in ("fieldname", "label", "fieldtype", "default")} for f in payload.get("filters") or []],
			"columns": [c.get("label") for c in columns],
			"preview_rows": rows[:12],
		}
		messages = [{"role": "user", "content": clip(dumps(request), 20000)}]
		for _attempt in range(2):
			reply = client.stream(
				{"model": settings.model or "claude-opus-5-5", "max_tokens": 1500, "system": SYSTEM, "messages": messages}
			)
			text = " ".join(b.get("text", "") for b in reply.get("content") or [] if b.get("type") == "text")
			verdict = parse_verdict(text)
			if verdict:
				return {"status": verdict["verdict"], "notes": verdict["issues"]}
			# not in the agreed shape: say so once and ask again
			messages += [
				{"role": "assistant", "content": text or "(empty)"},
				{
					"role": "user",
					"content": 'That reply was not valid. Reply again with only this JSON object and nothing else: '
					'{"verdict": "ok" or "issues", "issues": [strings]}',
				},
			]
		return {"status": "unavailable", "notes": []}
	except Exception:
		return {"status": "unavailable", "notes": []}
