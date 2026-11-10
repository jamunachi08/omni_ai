"""Assistant usage per user for a period: messages, tokens and web searches."""

import frappe
from frappe import _
from frappe.utils import add_days, getdate, nowdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	from_date = getdate(filters.get("from_date") or add_days(nowdate(), -30))
	to_date = getdate(filters.get("to_date") or nowdate())
	values = {"start": f"{from_date} 00:00:00", "end": f"{to_date} 23:59:59"}
	condition = ""
	if filters.get("user"):
		condition = "and owner = %(user)s"
		values["user"] = filters.user

	data = frappe.db.sql(
		f"""
		select
			owner as user,
			count(distinct conversation) as conversations,
			sum(case when role = 'user' then 1 else 0 end) as messages,
			sum(coalesce(input_tokens, 0)) as input_tokens,
			sum(coalesce(output_tokens, 0)) as output_tokens,
			sum(coalesce(cache_read_tokens, 0)) as cached_tokens,
			sum(coalesce(web_searches, 0)) as web_searches,
			max(creation) as last_used
		from `tabAI Assistant Message`
		where creation between %(start)s and %(end)s {condition}
		group by owner
		order by messages desc
		""",
		values,
		as_dict=True,
	)
	columns = [
		{"fieldname": "user", "label": _("User"), "fieldtype": "Link", "options": "User", "width": 240},
		{"fieldname": "conversations", "label": _("Conversations"), "fieldtype": "Int", "width": 130},
		{"fieldname": "messages", "label": _("Messages"), "fieldtype": "Int", "width": 110},
		{"fieldname": "input_tokens", "label": _("Input Tokens"), "fieldtype": "Int", "width": 140},
		{"fieldname": "output_tokens", "label": _("Output Tokens"), "fieldtype": "Int", "width": 140},
		{"fieldname": "cached_tokens", "label": _("Cached Input Tokens"), "fieldtype": "Int", "width": 170},
		{"fieldname": "web_searches", "label": _("Web Searches"), "fieldtype": "Int", "width": 130},
		{"fieldname": "last_used", "label": _("Last Used"), "fieldtype": "Datetime", "width": 180},
	]
	chart = None
	if data:
		top = data[:15]
		chart = {
			"data": {
				"labels": [row.user for row in top],
				"datasets": [{"name": _("Messages"), "values": [int(row.messages or 0) for row in top]}],
			},
			"type": "bar",
		}
	return columns, data, None, chart
