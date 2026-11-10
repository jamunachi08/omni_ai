"""Keeps a public workspace that lists the reports saved through the assistant.

The workspace is site data, not part of this app's files. On Frappe v15 it is created without a
module so developer-mode benches never export it into the app folder; on v16 a module is mandatory
and only workspaces flagged "standard" are exported, so it is filed under this app's module.
Administrators can also point the setting at any custom public workspace of their own.
"""

import json

import frappe

from omni_ai.utils import frappe_major, get_settings

MODULE = "AI Assistant"


def add_report_shortcut(report) -> str | None:
	settings = get_settings()
	if not settings.add_reports_to_workspace:
		return None
	title = (settings.reports_workspace or "AI Reports").strip()[:60] or "AI Reports"

	original_user = frappe.session.user
	try:
		# Workspaces are maintained by Workspace Managers; this one belongs to the app.
		frappe.set_user("Administrator")
		if frappe.db.exists("Workspace", title):
			ws = frappe.get_doc("Workspace", title)
			if (ws.module and ws.module != MODULE) or ws.get("standard") or not ws.public:
				# a workspace shipped by another app, or someone's private one: leave it alone
				return None
		else:
			ws = frappe.new_doc("Workspace")
			ws.update(
				{
					"label": title,
					"title": title,
					"name": title,
					"public": 1,
					"icon": "chart",
					**({"module": MODULE, "type": "Workspace"} if frappe_major() >= 16 else {}),
					"content": json.dumps(
						[
							{
								"id": frappe.generate_hash(length=10),
								"type": "header",
								"data": {"text": f'<span class="h4"><b>{frappe.utils.escape_html(title)}</b></span>', "col": 12},
							}
						]
					),
				}
			)

		if any(s.link_to == report.name and s.type == "Report" for s in ws.shortcuts):
			return ws.name

		label = report.name[:140]
		row = {"type": "Report", "link_to": report.name, "label": label}
		if report.report_type == "Report Builder":
			row["doc_view"] = "Report Builder"
		row["report_ref_doctype"] = report.ref_doctype
		ws.append("shortcuts", row)

		content = []
		try:
			content = json.loads(ws.content or "[]")
		except Exception:
			content = []
		content.append(
			{"id": frappe.generate_hash(length=10), "type": "shortcut", "data": {"shortcut_name": label, "col": 4}}
		)
		ws.content = json.dumps(content)
		is_new = ws.is_new()
		ws.flags.ignore_permissions = True
		ws.flags.ignore_links = True
		ws.save(ignore_permissions=True)
		if is_new and frappe_major() >= 16:
			# v16 lists a page in its module's sidebar through this call (inserts do not do it themselves)
			try:
				from frappe.desk.doctype.workspace.workspace import add_to_sidebar

				add_to_sidebar(ws)
			except Exception:
				pass
		return ws.name
	finally:
		frappe.set_user(original_user)
