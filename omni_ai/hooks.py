from omni_ai import __version__

app_name = "omni_ai"
app_title = "AI Assistant"
app_publisher = "AI Assistant"
app_description = "A white-label AI assistant inside ERPNext and Frappe"
app_email = "support@example.com"
app_license = "mit"

try:
	import frappe

	_major = int(str(frappe.__version__).split(".")[0])
except Exception:
	_major = 15

# Frappe v16 serves the desk from /desk; v15 from /app.
DESK_ROUTE = "/desk/ai-assistant" if _major >= 16 else "/app/ai-assistant"

# Plain assets (no build step needed). The version suffix busts browser caches on upgrade.
app_include_js = [f"/assets/omni_ai/js/omni_ai.js?v={__version__}"]
app_include_css = [f"/assets/omni_ai/css/omni_ai.css?v={__version__}"]

extend_bootinfo = "omni_ai.boot.extend_bootinfo"

after_install = "omni_ai.install.after_install"
after_migrate = "omni_ai.install.after_migrate"

add_to_apps_screen = [
	{
		"name": "omni_ai",
		"logo": "/assets/omni_ai/images/assistant.svg",
		"title": "AI Assistant",
		"route": DESK_ROUTE,
		"has_permission": "omni_ai.utils.has_app_permission",
	}
]

scheduler_events = {
	"daily": ["omni_ai.tasks.daily_cleanup"],
}
