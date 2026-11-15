// Full-page view of the assistant. The chat component itself lives in public/js/omni_ai.js.
frappe.pages["ai-assistant"].on_page_load = function (wrapper) {
	const brand = (frappe.boot.omni_ai && frappe.boot.omni_ai.brand_name) || __("AI Assistant");
	const page = frappe.ui.make_app_page({ parent: wrapper, title: brand, single_column: true });
	const host = document.createElement("div");
	host.className = "omni-page-host";
	$(page.main || page.body).empty().append(host);
	wrapper.omni_host = host;

	if (!window.omni_ai || !omni_ai.enabled()) {
		host.innerHTML = `<div class="text-muted" style="padding:40px;text-align:center">${__(
			"The assistant is not available for your account. Ask your administrator for the AI Assistant User role."
		)}</div>`;
		return;
	}
	const features = (frappe.boot.omni_ai && frappe.boot.omni_ai.features) || {};
	if (features.memory) page.add_menu_item(__("Memory"), () => frappe.set_route("List", "AI Assistant Memory"));
	if (features.schedules) page.add_menu_item(__("Scheduled Tasks"), () => frappe.set_route("List", "AI Assistant Schedule"));
	if (frappe.user.has_role("System Manager")) {
		page.add_menu_item(__("Automations"), () => frappe.set_route("List", "AI Assistant Automation"));
		page.add_menu_item(__("Assistant Settings"), () => frappe.set_route("Form", "AI Assistant Settings"));
		page.add_menu_item(__("Data Access Log"), () => frappe.set_route("List", "AI Assistant Tool Log"));
	}
	const fit = () => {
		if (!host.isConnected) return;
		const top = host.getBoundingClientRect().top + window.scrollY;
		host.style.height = Math.max(420, window.innerHeight - top - 16) + "px";
		if (wrapper.ai_assistant) wrapper.ai_assistant.redraw_charts();
	};
	wrapper.omni_fit = fit;
	$(window).on("resize.omni_page", fit);
	wrapper.ai_assistant = omni_ai.mount_page(host);
};

frappe.pages["ai-assistant"].on_page_show = function (wrapper) {
	if (!wrapper.ai_assistant) return;
	// the page head settles a moment after the route change, so size the chat a few times
	[30, 300, 1000].forEach((ms) => setTimeout(() => wrapper.omni_fit && wrapper.omni_fit(), ms));
	const route = frappe.get_route() || [];
	const name = route[1];
	if (name && name !== wrapper.ai_assistant.conversation) wrapper.ai_assistant.open_conversation(name);
	else wrapper.ai_assistant.load_history();
	setTimeout(() => wrapper.ai_assistant.input.focus({ preventScroll: true }), 150);
};
