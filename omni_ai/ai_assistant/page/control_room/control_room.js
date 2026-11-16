// Control Room: what needs attention in each area, what waits for approval, what the entry checks caught.
frappe.pages["control-room"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Control Room"), single_column: true });
	const host = document.createElement("div");
	host.className = "omni-room";
	$(page.main || page.body).empty().append(host);
	const state = { show_clear: 0 };
	const esc = (v) => frappe.utils.escape_html(String(v == null ? "" : v));
	const money = (m) => (m.amount == null ? "" : format_currency(m.amount, m.currency));
	const SEVERITY = { high: __("Needs action"), medium: __("Keep an eye on"), low: __("When there is time") };

	const open_list = (m) => {
		if (m.route) return frappe.set_route(m.route.split("/"));
		const options = {};
		(m.filters || []).forEach(([field, op, value]) => {
			options[field] = op === "=" ? value : [op, value];
		});
		frappe.route_options = options;
		frappe.set_route("List", m.doctype, "List");
	};
	const ask = (text) => window.omni_ai && omni_ai.enabled() && omni_ai.open(text);

	const render = (data) => {
		const attention = data.monitors.filter((m) => m.count > 0);
		const high = attention.filter((m) => m.severity === "high").length;
		const areas = {};
		data.monitors.forEach((m) => (areas[m.area] = areas[m.area] || []).push(m));
		const checks = data.checks;
		const head = `
			<div class="omni-room-summary">
				<div class="omni-room-stat ${high ? "is-high" : ""}"><b>${attention.length}</b><span>${esc(__("things need attention"))}${high ? ` · ${high} ${esc(__("urgent"))}` : ""}</span></div>
				<div class="omni-room-stat ${data.pending.length ? "is-wait" : ""}"><b>${data.pending.length}</b><span>${esc(__("waiting for your approval"))}</span></div>
				${checks ? `<div class="omni-room-stat"><b>${checks.blocked + checks.warned}</b><span>${esc(__("entries questioned this week"))}${checks.blocked ? ` · ${checks.blocked} ${esc(__("stopped"))}` : ""}</span></div>` : ""}
			</div>`;

		const pending = data.pending.length
			? `<section class="omni-room-section"><h3>${esc(__("Waiting for your approval"))}</h3>
				<p class="omni-room-hint">${esc(__("Prepared by the assistant. Nothing is created or sent until you approve it."))}</p>
				${data.pending
					.map(
						(a) => `<div class="omni-room-card" data-action="${esc(a.name)}">
							<div class="omni-room-card-main">
								<div class="omni-room-card-kind">${esc(__(a.action_type))}${a.scheduled ? ` · ${esc(__("from a scheduled task"))}` : ""}</div>
								<div class="omni-room-card-title" dir="auto">${esc(a.title)}</div>
								<div class="omni-room-card-lines" dir="auto">${a.lines.map((l) => `<div>${esc(l)}</div>`).join("")}</div>
								<div class="omni-room-card-result" hidden></div>
							</div>
							<div class="omni-room-card-actions">
								<button class="btn btn-primary btn-sm" data-do="approve">${esc(__("Approve"))}</button>
								<button class="btn btn-default btn-sm" data-do="dismiss">${esc(__("Dismiss"))}</button>
								<button class="btn btn-link btn-sm" data-do="chat">${esc(__("Open the conversation"))}</button>
							</div>
						</div>`
					)
					.join("")}</section>`
			: "";

		const monitors = Object.keys(areas).length
			? Object.keys(areas)
					.map(
						(area) => `<section class="omni-room-section"><h3>${esc(__(area))}</h3>
						${areas[area]
							.map(
								(m) => `<div class="omni-room-row sev-${m.severity} ${m.count ? "" : "is-clear"}" data-key="${esc(m.key)}">
									<button class="omni-room-row-main" data-do="open" title="${esc(__("Open the list"))}">
										<span class="omni-room-dot" title="${esc(SEVERITY[m.severity])}"></span>
										<span class="omni-room-row-text"><span class="omni-room-row-title">${esc(__(m.title))}</span><span class="omni-room-row-why">${esc(__(m.why))}</span></span>
										<span class="omni-room-row-amount">${esc(money(m))}</span>
										<span class="omni-room-row-count">${m.count}</span>
									</button>
									${m.count ? `<button class="btn btn-default btn-xs omni-room-ask" data-do="ask">${esc(__("Ask"))}</button>` : ""}
								</div>`
							)
							.join("")}</section>`
					)
					.join("")
			: `<div class="omni-room-empty">${esc(__("Nothing needs attention in the areas you can see."))}</div>`;

		const checks_html = checks
			? `<section class="omni-room-section"><h3>${esc(__("Entry checks, last 7 days"))}</h3>
				<p class="omni-room-hint">${
					checks.enabled
						? esc(__("{0} rules are active. They run when a document is saved or submitted.", [checks.active_rules]))
						: esc(__("Entry checks are switched off in the assistant settings."))
				}</p>
				${
					checks.rules.length
						? checks.rules.map((r) => `<div class="omni-room-check"><span class="omni-room-pill ${r.outcome === "Blocked" ? "is-blocked" : ""}">${esc(__(r.outcome))}</span><span>${esc(r.rule)}</span><b>${r.count}</b></div>`).join("")
						: `<div class="omni-room-hint">${esc(__("No entry was questioned this week."))}</div>`
				}
				<div class="omni-room-links"><a data-go="rules">${esc(__("Rules"))}</a><a data-go="log">${esc(__("Full log"))}</a></div>
			</section>`
			: "";

		host.innerHTML = head + pending + monitors + checks_html;
		host.querySelectorAll("[data-go]").forEach((a) =>
			a.addEventListener("click", () => frappe.set_route("List", a.dataset.go === "rules" ? "AI Assistant Check Rule" : "AI Assistant Check Log"))
		);
		host.querySelectorAll(".omni-room-row").forEach((row) => {
			const m = data.monitors.find((x) => x.key === row.dataset.key);
			row.querySelector("[data-do='open']").addEventListener("click", () => open_list(m));
			const ask_btn = row.querySelector("[data-do='ask']");
			if (ask_btn)
				ask_btn.addEventListener("click", () =>
					ask(__("In the Control Room I see: {0} ({1}). Show me the most important ones and suggest what to do.", [__(m.title), m.count]))
				);
		});
		host.querySelectorAll(".omni-room-card").forEach((card) => {
			const name = card.dataset.action;
			const item = data.pending.find((x) => x.name === name);
			const result = card.querySelector(".omni-room-card-result");
			const finish = (text, ok) => {
				card.classList.add(ok ? "is-done" : "is-failed");
				card.querySelectorAll("[data-do='approve'],[data-do='dismiss']").forEach((b) => b.remove());
				result.hidden = false;
				result.textContent = text;
			};
			const run = (method, button) => {
				card.querySelectorAll("button").forEach((b) => (b.disabled = true));
				frappe
					.xcall(`omni_ai.api.chat.${method}`, { action: name })
					.then((r) => {
						card.querySelectorAll("button").forEach((b) => (b.disabled = false));
						if (method === "cancel_action") finish(__("Dismissed."), true);
						else finish(r.message || r.status, r.status !== "Failed");
					})
					.catch(() => card.querySelectorAll("button").forEach((b) => (b.disabled = false)));
			};
			card.querySelector("[data-do='approve']").addEventListener("click", (e) => run("confirm_action", e.target));
			card.querySelector("[data-do='dismiss']").addEventListener("click", (e) => run("cancel_action", e.target));
			card.querySelector("[data-do='chat']").addEventListener("click", () => frappe.set_route("ai-assistant", item.conversation));
		});
	};

	const load = () => {
		host.classList.add("is-loading");
		frappe
			.xcall("omni_ai.api.control.overview", { show_clear: state.show_clear })
			.then((data) => {
				host.classList.remove("is-loading");
				render(data);
			})
			.catch(() => {
				host.classList.remove("is-loading");
				host.innerHTML = `<div class="omni-room-empty">${esc(__("The Control Room could not be loaded."))}</div>`;
			});
	};
	wrapper.omni_room_load = load;
	page.set_primary_action(__("Refresh"), load, "refresh");
	page.add_menu_item(__("Show everything, including what is clear"), () => {
		state.show_clear = state.show_clear ? 0 : 1;
		load();
	});
	page.add_menu_item(__("Scheduled Tasks"), () => frappe.set_route("List", "AI Assistant Schedule"));
	if (frappe.user.has_role("System Manager")) {
		page.add_menu_item(__("Entry Check Rules"), () => frappe.set_route("List", "AI Assistant Check Rule"));
		page.add_menu_item(__("Assistant Settings"), () => frappe.set_route("Form", "AI Assistant Settings"));
	}
	load();
};

frappe.pages["control-room"].on_page_show = function (wrapper) {
	if (wrapper.omni_room_load && wrapper.omni_room_seen) wrapper.omni_room_load();
	wrapper.omni_room_seen = true;
};
