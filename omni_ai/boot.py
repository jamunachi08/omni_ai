import frappe

from omni_ai import __version__
from omni_ai.utils import can_save_query_reports, can_use_sql, get_settings, has_access


def extend_bootinfo(bootinfo):
	"""Send only what the browser needs. The API key never leaves the server."""
	try:
		if not has_access():
			return
		settings = get_settings()
		prompts = [p.strip() for p in (settings.suggested_prompts or "").splitlines() if p.strip()]
		bootinfo.omni_ai = {
			"version": __version__,
			"brand_name": settings.brand_name or "Assistant",
			"brand_logo": settings.brand_logo or "",
			"accent_color": settings.accent_color or "#4F46E5",
			"launcher_position": settings.launcher_position or "Bottom Right",
			"welcome_title": settings.welcome_title or "",
			"welcome_message": settings.welcome_message or "",
			"suggested_prompts": prompts[:8],
			"features": {
				"uploads": bool(settings.enable_file_uploads),
				"sql": can_use_sql(),
				"query_reports": can_save_query_reports(),
				"web_search": bool(settings.enable_web_search),
				"memory": bool(settings.enable_memory),
				"schedules": bool(settings.enable_schedules),
				"writing": bool(settings.get("enable_writing_help")),
				"control_room": bool(settings.get("enable_control_room")),
			},
		}
	except Exception:
		# Never block the desk from loading because of the assistant.
		frappe.log_error(title="AI Assistant boot failed")
