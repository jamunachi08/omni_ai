/*
 * Omni AI — chat interface for the Frappe desk.
 *
 * One plain script, no build step. It uses only what the desk already ships:
 * jQuery, frappe.call, frappe.realtime, showdown (markdown) and frappe.Chart.
 * Works on Frappe v15 (/app) and v16 (/desk).
 */
(function () {
	"use strict";

	if (window.omni_ai && window.omni_ai.__loaded) return;

	const API = "omni_ai.api.chat.";
	const EVENT = "omni_ai_event";
	const STORE = "omni_ai:";
	const ICONS = {
		spark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15z"/></svg>',
		send: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>',
		stop: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="7" y="7" width="10" height="10" rx="2"/></svg>',
		clip: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5l-8.6 8.6a5.5 5.5 0 01-7.8-7.8l9-9a3.7 3.7 0 015.2 5.2l-9 9a1.8 1.8 0 01-2.6-2.6l8.3-8.3"/></svg>',
		close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>',
		plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
		history: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 109-9 9 9 0 00-6.4 2.6L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/></svg>',
		expand: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>',
		check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
		warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8v5M12 16.5v.5"/><circle cx="12" cy="12" r="9"/></svg>',
		copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 012-2h9"/></svg>',
		retry: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 11-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg>',
		download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11M7 11l5 5 5-5M5 20h14"/></svg>',
		file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8l-5-5z"/><path d="M14 3v5h5"/></svg>',
		chevron: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>',
		pin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 4h6l-1 6 3 3H7l3-3-1-6zM12 13v7"/></svg>',
		edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h4L19 9l-4-4L4 16v4z"/></svg>',
		trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13"/></svg>',
		doc: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 8h6M9 12h6M9 16h3"/></svg>',
		link: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 5h5v5M19 5l-8 8M11 7H6a1 1 0 00-1 1v10a1 1 0 001 1h10a1 1 0 001-1v-5"/></svg>',
	};
	const PALETTE = ["#4F46E5", "#0EA5E9", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6", "#14B8A6", "#EC4899"];
	const ALLOWED_TAGS = new Set(
		"p br hr strong b em i u s del code pre blockquote ul ol li h1 h2 h3 h4 h5 h6 a table thead tbody tr th td span div sup sub input".split(
			" "
		)
	);

	// ------------------------------------------------------------------ utils

	const __ = (text, args) => (window.__ ? window.__(text, args) : text);
	const esc = (value) =>
		String(value === null || value === undefined ? "" : value)
			.replace(/&/g, "&amp;")
			.replace(/</g, "&lt;")
			.replace(/>/g, "&gt;")
			.replace(/"/g, "&quot;")
			.replace(/'/g, "&#39;");
	const el = (html) => {
		const t = document.createElement("template");
		t.innerHTML = html.trim();
		return t.content.firstElementChild;
	};
	const store = {
		get(key, fallback) {
			try {
				const raw = localStorage.getItem(STORE + key);
				return raw === null ? fallback : JSON.parse(raw);
			} catch (e) {
				return fallback;
			}
		},
		set(key, value) {
			try {
				localStorage.setItem(STORE + key, JSON.stringify(value));
			} catch (e) {
				/* private mode: preferences simply do not persist */
			}
		},
	};
	const hex = (n) => Array.from(crypto.getRandomValues(new Uint8Array(n)), (b) => b.toString(16).padStart(2, "0")).join("");
	const call = (method, args, opts) =>
		new Promise((resolve, reject) => {
			frappe.call({
				method: API + method,
				args: args || {},
				type: (opts && opts.type) || "POST",
				callback: (r) => resolve(r.message),
				error: (r) => reject(r),
			});
		});

	function readable_on(color) {
		const m = /^#?([0-9a-f]{6})$/i.exec(color || "");
		if (!m) return "#ffffff";
		const n = parseInt(m[1], 16);
		const lin = (c) => {
			c /= 255;
			return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
		};
		const lum = 0.2126 * lin((n >> 16) & 255) + 0.7152 * lin((n >> 8) & 255) + 0.0722 * lin(n & 255);
		return lum > 0.45 ? "#111827" : "#ffffff";
	}

	function desk_base() {
		return window.location.pathname.startsWith("/desk") ? "/desk" : "/app";
	}

	function time_ago(value) {
		if (!value) return "";
		try {
			return frappe.datetime.prettyDate(value, true) || "";
		} catch (e) {
			return "";
		}
	}

	// --------------------------------------------------------------- markdown

	let converter = null;
	function markdown(text) {
		text = String(text || "");
		// Neutralise raw HTML outside code so model or data content can never inject markup.
		const pieces = text.split(/(```[\s\S]*?(?:```|$)|`[^`\n]*`)/g);
		text = pieces.map((p, i) => (i % 2 ? p : p.replace(/</g, "&lt;"))).join("");
		let html;
		if (window.showdown) {
			if (!converter) {
				converter = new showdown.Converter({
					tables: true,
					strikethrough: true,
					simplifiedAutoLink: true,
					literalMidWordUnderscores: true,
					disableForced4SpacesIndentedSublists: true,
					tasklists: true,
					simpleLineBreaks: false,
					openLinksInNewWindow: false,
				});
			}
			html = converter.makeHtml(text);
		} else if (frappe.markdown) {
			html = frappe.markdown(text);
		} else {
			html = "<p>" + esc(text).replace(/\n/g, "<br>") + "</p>";
		}
		return sanitize(html);
	}

	function sanitize(html) {
		const doc = new DOMParser().parseFromString("<div>" + html + "</div>", "text/html");
		const root = doc.body.firstElementChild;
		const walk = (node) => {
			Array.from(node.children).forEach((child) => {
				const tag = child.tagName.toLowerCase();
				if (!ALLOWED_TAGS.has(tag)) {
					child.replaceWith(doc.createTextNode(child.textContent));
					return;
				}
				Array.from(child.attributes).forEach((attr) => {
					const name = attr.name.toLowerCase();
					const keep =
						(tag === "a" && name === "href") ||
						(tag === "input" && ["type", "checked", "disabled"].includes(name)) ||
						((tag === "td" || tag === "th") && name === "style" && /^text-align:\s*(left|right|center);?$/.test(attr.value)) ||
						(tag === "code" && name === "class" && /^[\w\s-]+$/.test(attr.value));
					if (!keep) child.removeAttribute(attr.name);
				});
				if (tag === "input") {
					if (child.getAttribute("type") !== "checkbox") {
						child.remove();
						return;
					}
					child.setAttribute("disabled", "");
				}
				if (tag === "a") {
					const href = (child.getAttribute("href") || "").trim();
					if (/^(https?:|mailto:)/i.test(href)) {
						child.setAttribute("target", "_blank");
						child.setAttribute("rel", "noopener noreferrer");
					} else if (/^\/(app|desk)\//.test(href)) {
						child.setAttribute("data-omni-route", href);
					} else {
						child.replaceWith(doc.createTextNode(child.textContent));
						return;
					}
				}
				if (tag === "table") {
					const wrap = doc.createElement("div");
					wrap.setAttribute("class", "omni-md-table");
					child.replaceWith(wrap);
					wrap.appendChild(child);
				}
				walk(child);
			});
		};
		walk(root);
		return root.innerHTML;
	}

	function copy_text(text, label) {
		const done = () => frappe.show_alert({ message: label || __("Copied"), indicator: "green" }, 2);
		if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done);
		else if (frappe.utils.copy_to_clipboard) frappe.utils.copy_to_clipboard(text);
	}

	function go(route) {
		// keep desk navigation in-app (no full reload) so the panel stays open
		const path = route.replace(/^\/(app|desk)\/?/, "");
		frappe.set_route(decodeURIComponent(path).split("/").filter(Boolean).join("/"));
	}

	// ------------------------------------------------------------ page context

	function page_context() {
		try {
			const route = frappe.get_route() || [];
			const view = route[0];
			if (view === "Form" && route[1] && route[2]) {
				const ctx = { view: "form", doctype: route[1], docname: route.slice(2).join("/") };
				if (window.cur_frm && cur_frm.doctype === route[1] && cur_frm.doc) {
					if (cur_frm.is_new && cur_frm.is_new()) return null;
					const tf = cur_frm.meta && cur_frm.meta.title_field;
					if (tf && cur_frm.doc[tf]) ctx.title = String(cur_frm.doc[tf]);
				}
				return ctx;
			}
			if (view === "List" && route[1]) {
				const ctx = { view: "list", doctype: route[1] };
				try {
					if (window.cur_list && cur_list.doctype === route[1] && cur_list.get_filters_for_args) {
						const f = cur_list.get_filters_for_args();
						if (f && f.length) ctx.filters = f;
					}
				} catch (e) {
					/* filters are optional */
				}
				return ctx;
			}
			if (view === "query-report" && route[1]) {
				const ctx = { view: "report", report_name: route[1] };
				try {
					if (frappe.query_report && frappe.query_report.get_filter_values) {
						ctx.filters = frappe.query_report.get_filter_values() || undefined;
					}
				} catch (e) {
					/* filters are optional */
				}
				return ctx;
			}
			if (view === "Workspaces" && route[1]) return { view: "workspace", title: route.slice(1).join("/") };
		} catch (e) {
			/* context is a convenience; never block chatting */
		}
		return null;
	}

	function context_label(ctx) {
		if (!ctx) return "";
		if (ctx.view === "form") return `${__(ctx.doctype)}: ${ctx.title || ctx.docname}`;
		if (ctx.view === "list") return __("{0} list", [__(ctx.doctype)]);
		if (ctx.view === "report") return __("Report: {0}", [__(ctx.report_name)]);
		if (ctx.view === "workspace") return __("Workspace: {0}", [__(ctx.title)]);
		return "";
	}

	// ------------------------------------------------------------- part views

	function fmt_cell(value, col) {
		if (value === null || value === undefined || value === "") return { text: "", num: false };
		const ft = (col && col.fieldtype) || "";
		if (typeof value === "number") {
			if (ft === "Check") return { text: value ? "✓" : "", num: false };
			const decimals = ft === "Int" || Number.isInteger(value) ? 0 : 2;
			let text;
			try {
				text = format_number(value, null, ft === "Currency" ? 2 : decimals);
			} catch (e) {
				text = value.toLocaleString(undefined, { maximumFractionDigits: 2 });
			}
			return { text: ft === "Percent" ? text + "%" : text, num: true };
		}
		let text = String(value);
		if (ft === "Date" && /^\d{4}-\d{2}-\d{2}$/.test(text)) {
			try {
				text = frappe.datetime.str_to_user(text);
			} catch (e) {
				/* keep ISO */
			}
		}
		return { text, num: false };
	}

	function to_csv(columns, rows) {
		const q = (v) => {
			v = v === null || v === undefined ? "" : String(v);
			return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
		};
		return [columns.map((c) => q(c.label || c.fieldname)).join(",")].concat(rows.map((r) => r.map(q).join(","))).join("\n");
	}

	function download_blob(name, text, type) {
		const blob = new Blob(["﻿" + text], { type: type || "text/csv;charset=utf-8" });
		const a = document.createElement("a");
		a.href = URL.createObjectURL(blob);
		a.download = name;
		document.body.appendChild(a);
		a.click();
		setTimeout(() => {
			URL.revokeObjectURL(a.href);
			a.remove();
		}, 500);
	}

	function table_view(part, opts) {
		opts = opts || {};
		const columns = part.columns || [];
		const all = part.rows || [];
		const node = el(`<div class="omni-card omni-table-card"></div>`);
		const compact = !!opts.compact;
		if (!compact) {
			node.appendChild(
				el(`<div class="omni-card-head">
					<div class="omni-card-title" dir="auto">${esc(part.title || __("Result"))}</div>
					<div class="omni-card-tools">
						<input class="omni-table-search" type="search" placeholder="${esc(__("Filter…"))}" aria-label="${esc(__("Filter rows"))}">
						<button class="omni-icon-btn" data-act="csv" title="${esc(__("Download CSV"))}">${ICONS.download}</button>
					</div>
				</div>`)
			);
		}
		const scroller = el(`<div class="omni-table-scroll${compact ? " is-compact" : ""}"><table><thead><tr></tr></thead><tbody></tbody></table></div>`);
		node.appendChild(scroller);
		const head = scroller.querySelector("thead tr");
		const body = scroller.querySelector("tbody");
		const numeric = columns.map((c, i) => all.some((r) => typeof r[i] === "number") && !["Check"].includes(c.fieldtype));
		let sort = { index: -1, dir: 1 };
		let query = "";

		columns.forEach((c, i) => {
			const th = el(`<th class="${numeric[i] ? "is-num" : ""}" title="${esc(__("Sort"))}">${esc(c.label || c.fieldname)}</th>`);
			th.addEventListener("click", () => {
				sort = { index: i, dir: sort.index === i ? -sort.dir : 1 };
				head.querySelectorAll("th").forEach((x) => x.removeAttribute("data-sort"));
				th.setAttribute("data-sort", sort.dir > 0 ? "asc" : "desc");
				draw();
			});
			head.appendChild(th);
		});

		const footer = el(`<div class="omni-card-foot"></div>`);
		node.appendChild(footer);

		function draw() {
			let rows = all;
			if (query) {
				const q = query.toLowerCase();
				rows = rows.filter((r) => r.some((v) => v !== null && String(v).toLowerCase().includes(q)));
			}
			if (sort.index >= 0) {
				const i = sort.index;
				rows = rows.slice().sort((a, b) => {
					const x = a[i], y = b[i];
					if (x === y) return 0;
					if (x === null || x === undefined || x === "") return 1;
					if (y === null || y === undefined || y === "") return -1;
					return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))) * sort.dir;
				});
			}
			const limit = compact ? 8 : 300;
			const frag = document.createDocumentFragment();
			rows.slice(0, limit).forEach((r) => {
				const tr = document.createElement("tr");
				columns.forEach((c, i) => {
					const cell = fmt_cell(r[i], c);
					const td = document.createElement("td");
					if (cell.num) td.className = "is-num";
					td.setAttribute("dir", "auto");
					if (c.fieldtype === "Link" && c.options && cell.text) {
						const a = document.createElement("a");
						a.textContent = cell.text;
						a.setAttribute("data-omni-route", `${desk_base()}/${frappe.router.slug(c.options)}/${encodeURIComponent(cell.text)}`);
						a.href = a.getAttribute("data-omni-route");
						td.appendChild(a);
					} else {
						td.textContent = cell.text;
					}
					tr.appendChild(td);
				});
				frag.appendChild(tr);
			});
			body.replaceChildren(frag);
			const total = part.total_rows || all.length;
			let note = rows.length === all.length ? __("{0} rows", [all.length]) : __("{0} of {1} rows", [rows.length, all.length]);
			if (compact && rows.length > limit) note = __("First {0} of {1} rows", [limit, total]);
			else if (total > all.length) note += " · " + __("{0} in total, export for all", [total]);
			footer.textContent = all.length ? note : __("No rows");
		}
		draw();

		node.addEventListener("click", (e) => {
			const btn = e.target.closest("[data-act='csv']");
			if (btn) download_blob((part.title || "result").replace(/[^\w\- ]+/g, "").trim() + ".csv", to_csv(columns, all));
		});
		const search = node.querySelector(".omni-table-search");
		if (search) {
			search.addEventListener("input", () => {
				query = search.value.trim();
				draw();
			});
		}
		return node;
	}

	const plain = (value) => String(value === null || value === undefined ? "" : value).replace(/[<>&"']/g, " ");

	function chart_view(part) {
		const node = el(`<div class="omni-card omni-chart-card">
			<div class="omni-card-head"><div class="omni-card-title" dir="auto">${esc(part.title || __("Chart"))}</div></div>
			<div class="omni-chart"></div>
		</div>`);
		const target = node.querySelector(".omni-chart");
		const accent = (frappe.boot.omni_ai && frappe.boot.omni_ai.accent_color) || PALETTE[0];
		const colors = [accent].concat(PALETTE.filter((c) => c.toLowerCase() !== String(accent).toLowerCase()));
		const draw = () => {
			if (node.__drawn || !target.isConnected || !target.offsetWidth) return false;
			if (!frappe.Chart) {
				target.textContent = __("Charts are not available on this page.");
				return (node.__drawn = true);
			}
			try {
				new frappe.Chart(target, {
					type: part.chart_type || "bar",
					height: 250,
					data: {
						// chart tooltips are built as HTML by the chart library, so labels must not carry markup
						labels: (part.labels || []).map(plain),
						datasets: (part.datasets || []).map((d) => ({ name: plain(d.name), values: (d.values || []).map((v) => Number(v) || 0) })),
					},
					colors,
					truncateLegends: true,
					barOptions: { stacked: !!part.stacked, spaceRatio: 0.4 },
					lineOptions: { regionFill: 1, hideDots: (part.labels || []).length > 30 ? 1 : 0 },
					axisOptions: { xAxisMode: "tick", xIsSeries: part.chart_type === "line" || (part.labels || []).length > 6, shortenYAxisNumbers: 1 },
					tooltipOptions: { formatTooltipY: (d) => (typeof d === "number" ? d.toLocaleString(undefined, { maximumFractionDigits: 2 }) : d) },
				});
			} catch (e) {
				target.textContent = __("This chart could not be drawn.");
			}
			return (node.__drawn = true);
		};
		node.__draw = draw;
		return node;
	}

	function file_view(part) {
		return el(`<a class="omni-file" href="${esc(part.file_url)}" target="_blank" rel="noopener" download>
			<span class="omni-file-icon">${ICONS.file}</span>
			<span class="omni-file-meta"><strong dir="auto">${esc(part.file_name)}</strong>
			<small>${part.rows !== undefined ? esc(__("{0} rows", [part.rows])) + " · " : ""}${esc(String(part.format || "").toUpperCase())}</small></span>
			<span class="omni-file-get">${ICONS.download}</span>
		</a>`);
	}

	function sources_view(part) {
		const node = el(`<div class="omni-sources"><div class="omni-sources-title">${esc(__("Sources"))}</div><ol></ol></div>`);
		const list = node.querySelector("ol");
		(part.items || []).forEach((item) => {
			if (!/^https?:/i.test(item.url || "")) return;
			const li = document.createElement("li");
			const a = document.createElement("a");
			a.href = item.url;
			a.target = "_blank";
			a.rel = "noopener noreferrer";
			a.textContent = item.title || item.url;
			li.appendChild(a);
			list.appendChild(li);
		});
		return node;
	}

	function action_view(part, chat) {
		const is_report = part.action_type === "Save Report";
		const node = el(`<div class="omni-card omni-action" data-status="${esc(part.status || "Pending")}">
			<div class="omni-action-head">
				<span class="omni-action-icon">${is_report ? ICONS.doc : ICONS.edit}</span>
				<div class="omni-action-titles">
					<div class="omni-action-kicker">${esc(is_report ? __("Report draft") : __("Needs your confirmation"))}</div>
					<div class="omni-card-title" dir="auto">${esc(part.title || "")}</div>
					${part.subtitle ? `<div class="omni-action-sub" dir="auto">${esc(part.subtitle)}</div>` : ""}
				</div>
			</div>
			${part.description ? `<p class="omni-action-desc" dir="auto">${esc(part.description)}</p>` : ""}
			<div class="omni-action-body"></div>
			<div class="omni-action-foot"></div>
		</div>`);
		const body = node.querySelector(".omni-action-body");
		if (part.columns && part.rows) {
			body.appendChild(el(`<div class="omni-action-label">${esc(__("Preview"))}</div>`));
			body.appendChild(table_view({ columns: part.columns, rows: part.rows, total_rows: part.total_rows }, { compact: true }));
		}
		const plain = (part.details || []).filter((d) => !d.code);
		const code = (part.details || []).filter((d) => d.code);
		if (plain.length) {
			const dl = el(`<dl class="omni-details"></dl>`);
			plain.forEach((d) => {
				dl.appendChild(el(`<dt>${esc(d.label)}</dt>`));
				dl.appendChild(el(`<dd dir="auto">${esc(d.value)}</dd>`));
			});
			body.appendChild(dl);
		}
		code.forEach((d) => {
			const box = el(`<details class="omni-code-details"><summary>${esc(__("Show {0}", [d.label]))}</summary><pre><code></code></pre></details>`);
			box.querySelector("code").textContent = d.value;
			body.appendChild(box);
		});

		const foot = node.querySelector(".omni-action-foot");
		const render_foot = () => {
			const status = part.status || "Pending";
			node.setAttribute("data-status", status);
			foot.replaceChildren();
			if (status === "Pending") {
				const confirm_label = is_report ? __("Save report") : __("Confirm");
				const ok = el(`<button class="omni-btn ${part.danger ? "is-danger" : "is-primary"}">${esc(confirm_label)}</button>`);
				const no = el(`<button class="omni-btn is-ghost">${esc(__("Dismiss"))}</button>`);
				ok.addEventListener("click", () => {
					const run = () => {
						ok.disabled = no.disabled = true;
						ok.textContent = __("Working…");
						call("confirm_action", { action: part.action })
							.then((res) => {
								part.status = res.status;
								part.result = res.message;
								part.route = res.route;
								render_foot();
								if (res.status === "Completed") frappe.show_alert({ message: res.message, indicator: "green" }, 5);
							})
							.catch(() => {
								ok.disabled = no.disabled = false;
								ok.textContent = confirm_label;
							});
					};
					if (part.danger) frappe.confirm(__("This cannot be undone. Continue?"), run);
					else run();
				});
				no.addEventListener("click", () => {
					call("cancel_action", { action: part.action }).then(() => {
						part.status = "Cancelled";
						render_foot();
					});
				});
				foot.appendChild(ok);
				foot.appendChild(no);
				if (is_report) foot.appendChild(el(`<span class="omni-action-hint">${esc(__("Not saved yet. Ask for changes if it is not right."))}</span>`));
			} else if (status === "Completed") {
				foot.appendChild(el(`<span class="omni-state is-ok">${ICONS.check}<span dir="auto">${esc(part.result || __("Done"))}</span></span>`));
				if (part.route) {
					const open = el(`<a class="omni-btn is-primary" href="${esc(part.route)}" data-omni-route="${esc(part.route)}">${esc(is_report ? __("Open report") : __("Open"))}</a>`);
					foot.appendChild(open);
				}
			} else if (status === "Failed") {
				foot.appendChild(el(`<span class="omni-state is-bad">${ICONS.warn}<span dir="auto">${esc(part.result || __("This did not work."))}</span></span>`));
			} else {
				foot.appendChild(el(`<span class="omni-state is-muted">${esc(__("Dismissed"))}</span>`));
			}
		};
		render_foot();
		return node;
	}

	// ------------------------------------------------------------ message view

	class MessageView {
		constructor(chat, message) {
			this.chat = chat;
			this.message = message;
			this.parts = [];
			this.nodes = new Map(); // part id -> node
			this.node = el(`<div class="omni-msg is-${message.role}"></div>`);
			if (message.role === "user") this.render_user();
			else {
				this.body = el(`<div class="omni-msg-body"></div>`);
				this.node.appendChild(this.body);
				(message.parts || []).forEach((p) => this.add_part(p, true));
				if (message.status === "Error") this.show_error(message.error);
				if (message.status === "Stopped") this.note(__("Stopped"));
				if (message.status !== "Running") this.finish(true);
			}
		}

		render_user() {
			const m = this.message;
			if (m.attachments && m.attachments.length) {
				const row = el(`<div class="omni-attachments"></div>`);
				m.attachments.forEach((a) => row.appendChild(el(`<span class="omni-chip">${ICONS.file}<span dir="auto">${esc(a.file_name)}</span></span>`)));
				this.node.appendChild(row);
			}
			if (m.content) {
				const bubble = el(`<div class="omni-bubble" dir="auto"></div>`);
				bubble.textContent = m.content;
				this.node.appendChild(bubble);
			}
		}

		// consecutive tool steps share one collapsible group
		step_group() {
			const last = this.body.lastElementChild;
			if (last && last.classList.contains("omni-steps")) return last;
			const group = el(`<details class="omni-steps" open><summary><span class="omni-steps-label"></span><span class="omni-steps-caret">${ICONS.chevron}</span></summary><ul></ul></details>`);
			this.body.appendChild(group);
			return group;
		}

		refresh_group(group) {
			const items = group.querySelectorAll("li");
			const running = group.querySelector("li[data-status='running']");
			const label = group.querySelector(".omni-steps-label");
			if (running) label.textContent = running.querySelector(".omni-step-label").textContent + "…";
			else label.textContent = items.length === 1 ? items[0].querySelector(".omni-step-label").textContent : __("{0} steps", [items.length]);
			group.classList.toggle("is-running", !!running);
		}

		add_part(part, restoring) {
			this.parts.push(part);
			let node = null;
			if (part.type === "text") {
				node = el(`<div class="omni-text" dir="auto"></div>`);
				node.innerHTML = markdown(part.text);
				this.decorate(node);
				this.body.appendChild(node);
			} else if (part.type === "tool") {
				const group = this.step_group();
				node = el(`<li data-status="${esc(part.status || "running")}"><span class="omni-step-icon"></span><span class="omni-step-label" dir="auto"></span><span class="omni-step-sum"></span></li>`);
				group.querySelector("ul").appendChild(node);
				this.paint_step(node, part);
				this.refresh_group(group);
			} else if (part.type === "table") {
				node = table_view(part);
				this.body.appendChild(node);
			} else if (part.type === "chart") {
				node = chart_view(part);
				this.body.appendChild(node);
				this.chat.draw_when_visible(node);
			} else if (part.type === "action") {
				node = action_view(part, this.chat);
				this.body.appendChild(node);
			} else if (part.type === "file") {
				node = file_view(part);
				this.body.appendChild(node);
			} else if (part.type === "sources") {
				node = sources_view(part);
				this.body.appendChild(node);
			}
			if (node && part.id) this.nodes.set(part.id, node);
			return node;
		}

		paint_step(node, part) {
			node.setAttribute("data-status", part.status || "running");
			const icon = node.querySelector(".omni-step-icon");
			icon.innerHTML = part.status === "done" ? ICONS.check : part.status === "error" ? ICONS.warn : '<span class="omni-spin"></span>';
			node.querySelector(".omni-step-label").textContent = part.label || part.name || "";
			node.querySelector(".omni-step-sum").textContent = part.summary || "";
		}

		update_part(id, patch) {
			const part = this.parts.find((p) => p.id === id);
			const node = this.nodes.get(id);
			if (!part || !node) return;
			Object.assign(part, patch);
			if (part.type === "tool") {
				this.paint_step(node, part);
				this.refresh_group(node.closest(".omni-steps"));
			}
		}

		append_text(delta) {
			let part = this.parts[this.parts.length - 1];
			if (!part || part.type !== "text") {
				part = { type: "text", text: "" };
				this.add_part(part);
				this.live = this.body.lastElementChild;
			}
			part.text += delta;
			this.live = this.live && this.live.isConnected ? this.live : this.body.lastElementChild;
			if (this.raf) return;
			this.raf = requestAnimationFrame(() => {
				this.raf = null;
				const last = this.parts[this.parts.length - 1];
				if (last && last.type === "text" && this.live) {
					this.live.innerHTML = markdown(last.text);
					this.decorate(this.live);
					this.chat.stick();
				}
			});
		}

		decorate(node) {
			node.querySelectorAll("pre").forEach((pre) => {
				if (pre.querySelector(".omni-copy-code")) return;
				const btn = el(`<button class="omni-copy-code" title="${esc(__("Copy"))}">${ICONS.copy}</button>`);
				btn.addEventListener("click", () => copy_text(pre.querySelector("code") ? pre.querySelector("code").textContent : pre.textContent));
				pre.appendChild(btn);
			});
		}

		set_status(text) {
			if (!this.status_node) {
				this.status_node = el(`<div class="omni-thinking"><span class="omni-dots"><i></i><i></i><i></i></span><span class="omni-thinking-text"></span></div>`);
			}
			this.status_node.querySelector(".omni-thinking-text").textContent = text || "";
			this.body.appendChild(this.status_node);
		}

		clear_status() {
			if (this.status_node) this.status_node.remove();
		}

		show_error(text) {
			this.clear_status();
			const box = el(`<div class="omni-error">${ICONS.warn}<span dir="auto"></span></div>`);
			box.querySelector("span").textContent = text || __("Something went wrong.");
			this.body.appendChild(box);
		}

		note(text) {
			this.body.appendChild(el(`<div class="omni-note">${esc(text)}</div>`));
		}

		text() {
			return this.parts.filter((p) => p.type === "text").map((p) => p.text).join("\n\n");
		}

		finish(restoring) {
			this.clear_status();
			this.body.querySelectorAll(".omni-steps").forEach((g) => {
				g.removeAttribute("open");
				this.refresh_group(g);
			});
			if (this.tools) this.tools.remove();
			this.tools = el(`<div class="omni-msg-tools">
				<button class="omni-icon-btn" data-act="copy" title="${esc(__("Copy answer"))}">${ICONS.copy}</button>
				<button class="omni-icon-btn" data-act="retry" title="${esc(__("Try again"))}">${ICONS.retry}</button>
			</div>`);
			this.tools.querySelector("[data-act='copy']").addEventListener("click", () => copy_text(this.text()));
			this.tools.querySelector("[data-act='retry']").addEventListener("click", () => this.chat.regenerate());
			this.node.appendChild(this.tools);
			if (!this.text()) this.tools.querySelector("[data-act='copy']").remove();
		}
	}

	// -------------------------------------------------------------------- chat

	class OmniChat {
		constructor(opts) {
			this.mode = opts.mode || "panel";
			this.cfg = frappe.boot.omni_ai || {};
			this.conversation = null;
			this.views = [];
			this.run = null;
			this.pending_files = [];
			this.use_context = true;
			this.root = el(this.template());
			opts.container.appendChild(this.root);
			this.$ = (sel) => this.root.querySelector(sel);
			this.body = this.$(".omni-scroll");
			this.input = this.$(".omni-input");
			this.bind();
			this.show_welcome();
			this.refresh_context();
			if (this.mode === "page") this.load_history();
		}

		template() {
			const c = this.cfg;
			const logo = c.brand_logo ? `<img src="${esc(c.brand_logo)}" alt="">` : ICONS.spark;
			return `<div class="ai-assistant is-${this.mode}" role="complementary" aria-label="${esc(c.brand_name)}">
				<aside class="omni-side">
					<div class="omni-side-head">
						<button class="omni-btn is-primary omni-new-wide">${ICONS.plus}<span>${esc(__("New chat"))}</span></button>
						<input class="omni-side-search" type="search" placeholder="${esc(__("Search chats"))}" aria-label="${esc(__("Search chats"))}">
					</div>
					<div class="omni-side-list"></div>
				</aside>
				<section class="omni-main">
					<header class="omni-head">
						<span class="omni-logo">${logo}</span>
						<div class="omni-head-titles">
							<div class="omni-brand">${esc(c.brand_name)}</div>
							<div class="omni-conv-title" dir="auto"></div>
						</div>
						<div class="omni-head-actions">
							<button class="omni-icon-btn" data-act="history" title="${esc(__("Chats"))}" aria-label="${esc(__("Chats"))}">${ICONS.history}</button>
							<button class="omni-icon-btn" data-act="new" title="${esc(__("New chat"))}" aria-label="${esc(__("New chat"))}">${ICONS.plus}</button>
							<button class="omni-icon-btn" data-act="expand" title="${esc(__("Open full page"))}" aria-label="${esc(__("Open full page"))}">${ICONS.expand}</button>
							<button class="omni-icon-btn" data-act="close" title="${esc(__("Close"))}" aria-label="${esc(__("Close"))}">${ICONS.close}</button>
						</div>
					</header>
					<div class="omni-scroll"><div class="omni-thread"></div></div>
					<footer class="omni-composer">
						<div class="omni-tray"></div>
						<div class="omni-box">
							<textarea class="omni-input" rows="1" dir="auto" placeholder="${esc(__("Ask {0} anything…", [c.brand_name]))}" aria-label="${esc(__("Message"))}"></textarea>
							<div class="omni-box-actions">
								${c.features && c.features.uploads ? `<button class="omni-icon-btn" data-act="attach" title="${esc(__("Attach a file"))}" aria-label="${esc(__("Attach a file"))}">${ICONS.clip}</button>` : ""}
								<button class="omni-send" data-act="send" title="${esc(__("Send"))}" aria-label="${esc(__("Send"))}">${ICONS.send}</button>
							</div>
						</div>
						<div class="omni-foot-note">${esc(__("{0} can make mistakes. Check important figures.", [c.brand_name]))}</div>
						<input type="file" class="omni-file-input" multiple hidden accept=".png,.jpg,.jpeg,.gif,.webp,.pdf,.csv,.tsv,.txt,.md,.json,.xml,.xlsx,.xlsm,.docx,.html,.sql,.log,.yaml,.yml">
					</footer>
				</section>
			</div>`;
		}

		bind() {
			this.root.addEventListener("click", (e) => {
				const route = e.target.closest("[data-omni-route]");
				if (route && !e.metaKey && !e.ctrlKey) {
					e.preventDefault();
					go(route.getAttribute("data-omni-route"));
					return;
				}
				const btn = e.target.closest("[data-act]");
				if (!btn || !this.root.contains(btn)) return;
				const act = btn.getAttribute("data-act");
				if (act === "send") this.run ? this.stop() : this.send();
				else if (act === "new") this.new_chat();
				else if (act === "history") this.toggle_history();
				else if (act === "close") omni_ai.close();
				else if (act === "expand") {
					const name = this.conversation;
					omni_ai.close();
					frappe.set_route("ai-assistant", name || "");
				} else if (act === "attach") this.$(".omni-file-input").click();
			});
			this.$(".omni-new-wide").addEventListener("click", () => this.new_chat());
			this.input.addEventListener("keydown", (e) => {
				if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
					e.preventDefault();
					if (!this.run) this.send();
				}
			});
			this.input.addEventListener("input", () => this.grow());
			this.input.addEventListener("paste", (e) => {
				const files = Array.from((e.clipboardData && e.clipboardData.files) || []);
				if (files.length && this.cfg.features && this.cfg.features.uploads) {
					e.preventDefault();
					this.upload(files);
				}
			});
			this.$(".omni-file-input").addEventListener("change", (e) => {
				this.upload(Array.from(e.target.files || []));
				e.target.value = "";
			});
			["dragover", "drop"].forEach((type) =>
				this.root.addEventListener(type, (e) => {
					if (!(this.cfg.features && this.cfg.features.uploads) || !e.dataTransfer || !Array.from(e.dataTransfer.types || []).includes("Files")) return;
					e.preventDefault();
					if (type === "drop") this.upload(Array.from(e.dataTransfer.files || []));
				})
			);
			let timer = null;
			this.$(".omni-side-search").addEventListener("input", (e) => {
				clearTimeout(timer);
				timer = setTimeout(() => this.load_history(e.target.value), 250);
			});
			this.body.addEventListener("scroll", () => {
				this.pinned_bottom = this.body.scrollHeight - this.body.scrollTop - this.body.clientHeight < 80;
			});
			this.pinned_bottom = true;
		}

		grow() {
			this.input.style.height = "auto";
			this.input.style.height = Math.min(this.input.scrollHeight, 180) + "px";
		}

		stick(force) {
			if (force || this.pinned_bottom) this.body.scrollTop = this.body.scrollHeight;
		}

		draw_when_visible(node) {
			const attempt = (tries) => {
				if (node.__draw && node.__draw()) return;
				if (tries > 0) setTimeout(() => attempt(tries - 1), 250);
			};
			requestAnimationFrame(() => attempt(40));
		}

		redraw_charts() {
			this.root.querySelectorAll(".omni-chart-card").forEach((n) => n.__draw && n.__draw());
		}

		// ---------------------------------------------------------- welcome

		show_welcome() {
			const c = this.cfg;
			const thread = this.$(".omni-thread");
			thread.replaceChildren();
			this.views = [];
			const first = (frappe.session.user_fullname || "").split(" ")[0];
			const node = el(`<div class="omni-welcome">
				<span class="omni-welcome-logo">${c.brand_logo ? `<img src="${esc(c.brand_logo)}" alt="">` : ICONS.spark}</span>
				<h2 dir="auto">${esc(first ? __("Hello {0}", [first]) : __("Hello"))}</h2>
				<h3 dir="auto">${esc(c.welcome_title || __("How can I help you today?"))}</h3>
				<p dir="auto">${esc(c.welcome_message || "")}</p>
				<div class="omni-prompts"></div>
			</div>`);
			const wrap = node.querySelector(".omni-prompts");
			(c.suggested_prompts || []).forEach((p) => {
				const b = el(`<button class="omni-prompt" dir="auto"></button>`);
				b.textContent = p;
				b.addEventListener("click", () => {
					this.input.value = p;
					this.send();
				});
				wrap.appendChild(b);
			});
			thread.appendChild(node);
			this.set_title("");
		}

		set_title(title) {
			this.$(".omni-conv-title").textContent = title || "";
		}

		// ---------------------------------------------------------- context

		refresh_context() {
			this.context = page_context();
			this.render_tray();
		}

		render_tray() {
			const tray = this.$(".omni-tray");
			tray.replaceChildren();
			if (this.context && this.mode === "panel") {
				const label = context_label(this.context);
				if (label) {
					const chip = el(`<button class="omni-chip is-context${this.use_context ? "" : " is-off"}" title="${esc(__("The assistant can see what you have open. Click to switch this off or on."))}">${ICONS.link}<span dir="auto"></span></button>`);
					chip.querySelector("span").textContent = label;
					chip.addEventListener("click", () => {
						this.use_context = !this.use_context;
						this.render_tray();
					});
					tray.appendChild(chip);
				}
			}
			this.pending_files.forEach((f, i) => {
				const chip = el(`<span class="omni-chip${f.uploading ? " is-busy" : ""}">${f.uploading ? '<span class="omni-spin"></span>' : ICONS.file}<span dir="auto"></span><button title="${esc(__("Remove"))}" aria-label="${esc(__("Remove"))}">${ICONS.close}</button></span>`);
				chip.querySelector("span[dir]").textContent = f.file_name;
				chip.querySelector("button").addEventListener("click", () => {
					this.pending_files.splice(i, 1);
					this.render_tray();
				});
				tray.appendChild(chip);
			});
		}

		// ---------------------------------------------------------- uploads

		upload(files) {
			files.slice(0, 8).forEach((file) => {
				if (file.size > 20 * 1024 * 1024) {
					frappe.show_alert({ message: __("{0} is larger than 20 MB.", [file.name]), indicator: "orange" });
					return;
				}
				const entry = { file_name: file.name, uploading: true };
				this.pending_files.push(entry);
				this.render_tray();
				const form = new FormData();
				form.append("file", file, file.name);
				form.append("is_private", "1");
				form.append("folder", "Home/Attachments");
				fetch("/api/method/upload_file", {
					method: "POST",
					headers: { "X-Frappe-CSRF-Token": frappe.csrf_token, Accept: "application/json" },
					body: form,
					credentials: "same-origin",
				})
					.then((r) => r.json().then((j) => ({ ok: r.ok, j })))
					.then(({ ok, j }) => {
						if (!ok || !j.message) throw new Error("upload failed");
						entry.uploading = false;
						entry.file = j.message.name;
						this.render_tray();
					})
					.catch(() => {
						this.pending_files = this.pending_files.filter((f) => f !== entry);
						this.render_tray();
						frappe.show_alert({ message: __("{0} could not be uploaded.", [file.name]), indicator: "red" });
					});
			});
		}

		// ---------------------------------------------------------- history

		toggle_history(force) {
			const open = force === undefined ? !this.root.classList.contains("show-side") : force;
			this.root.classList.toggle("show-side", open);
			if (open) this.load_history();
		}

		load_history(search) {
			return call("list_conversations", { search: search || "" }, { type: "GET" }).then((rows) => {
				const list = this.$(".omni-side-list");
				list.replaceChildren();
				if (!rows || !rows.length) {
					list.appendChild(el(`<div class="omni-side-empty">${esc(search ? __("No chats match.") : __("Your chats will appear here."))}</div>`));
					return;
				}
				rows.forEach((row) => {
					const item = el(`<div class="omni-side-item${row.name === this.conversation ? " is-active" : ""}" tabindex="0" role="button">
						<span class="omni-side-title" dir="auto"></span>
						<span class="omni-side-meta">${row.pinned ? ICONS.pin : ""}${esc(time_ago(row.last_message_at || row.modified))}</span>
						<span class="omni-side-actions">
							<button data-do="pin" title="${esc(row.pinned ? __("Unpin") : __("Pin"))}">${ICONS.pin}</button>
							<button data-do="rename" title="${esc(__("Rename"))}">${ICONS.edit}</button>
							<button data-do="delete" title="${esc(__("Delete"))}">${ICONS.trash}</button>
						</span>
					</div>`);
					item.querySelector(".omni-side-title").textContent = row.title || __("Conversation");
					const open = () => {
						this.open_conversation(row.name);
						if (this.mode === "panel") this.toggle_history(false);
					};
					item.addEventListener("click", (e) => {
						const act = e.target.closest("[data-do]");
						if (!act) return open();
						e.stopPropagation();
						const what = act.getAttribute("data-do");
						if (what === "pin") call("pin_conversation", { name: row.name, pinned: row.pinned ? 0 : 1 }).then(() => this.load_history(search));
						if (what === "rename") {
							frappe.prompt({ fieldname: "title", label: __("Title"), fieldtype: "Data", default: row.title, reqd: 1 }, (v) => {
								call("rename_conversation", { name: row.name, title: v.title }).then((t) => {
									if (row.name === this.conversation) this.set_title(t);
									this.load_history(search);
								});
							}, __("Rename chat"));
						}
						if (what === "delete") {
							frappe.confirm(__("Delete this chat and its files?"), () => {
								call("delete_conversation", { name: row.name }).then(() => {
									if (row.name === this.conversation) this.new_chat();
									this.load_history(search);
								});
							});
						}
					});
					item.addEventListener("keydown", (e) => e.key === "Enter" && open());
					list.appendChild(item);
				});
			});
		}

		new_chat() {
			if (this.run) this.detach_run();
			this.conversation = null;
			this.pending_files = [];
			this.show_welcome();
			this.render_tray();
			this.set_busy(false);
			if (this.mode === "panel") {
				this.toggle_history(false);
				store.set("last_conversation", null);
			} else {
				this.mark_active();
			}
			this.input.focus();
		}

		mark_active() {
			this.root.querySelectorAll(".omni-side-item").forEach((n) => n.classList.remove("is-active"));
		}

		open_conversation(name) {
			if (this.run) this.detach_run();
			return call("get_conversation", { name }, { type: "GET" })
				.then((data) => {
					this.conversation = data.name;
					store.set("last_conversation", data.name);
					const thread = this.$(".omni-thread");
					thread.replaceChildren();
					this.views = [];
					data.messages.forEach((m) => this.add_message(m));
					this.set_title(data.title === "New conversation" ? "" : data.title);
					this.set_busy(false);
					this.stick(true);
					if (data.active_run) this.attach_run(data.active_run, 0);
					if (this.mode === "page") this.load_history(this.$(".omni-side-search").value);
				})
				.catch(() => {
					store.set("last_conversation", null);
					this.new_chat();
				});
		}

		add_message(message) {
			const welcome = this.$(".omni-welcome");
			if (welcome) welcome.remove();
			const view = new MessageView(this, message);
			this.views.push(view);
			this.$(".omni-thread").appendChild(view.node);
			return view;
		}

		// ---------------------------------------------------------- sending

		set_busy(busy) {
			this.root.classList.toggle("is-busy", !!busy);
			const btn = this.$(".omni-send");
			btn.innerHTML = busy ? ICONS.stop : ICONS.send;
			btn.title = busy ? __("Stop") : __("Send");
			btn.setAttribute("aria-label", btn.title);
		}

		send() {
			const text = this.input.value.trim();
			if (this.pending_files.some((f) => f.uploading)) {
				frappe.show_alert({ message: __("Please wait for the upload to finish."), indicator: "orange" });
				return;
			}
			const files = this.pending_files.filter((f) => f.file);
			if (!text && !files.length) return;
			const run_id = hex(8);
			const args = { message: text, run_id };
			if (this.conversation) args.conversation = this.conversation;
			if (files.length) args.attachments = JSON.stringify(files.map((f) => f.file));
			if (this.mode === "panel" && this.use_context && this.context) args.page_context = JSON.stringify(this.context);

			this.input.value = "";
			this.grow();
			this.pending_files = [];
			this.render_tray();
			this.add_message({ role: "user", content: text, attachments: files.map((f) => ({ file_name: f.file_name })) });
			this.attach_run(run_id, 0, true);
			this.stick(true);

			call("send", args)
				.then((res) => {
					if (!res) return;
					const fresh = !this.conversation;
					this.conversation = res.conversation;
					store.set("last_conversation", res.conversation);
					if (this.run && this.run.id === run_id && res.run_id !== run_id) this.rename_run(res.run_id);
					if (fresh && this.mode === "page") this.load_history();
				})
				.catch(() => {
					// the server said no (limit reached, not configured...). frappe.call already showed why.
					if (this.run && this.run.id === run_id) {
						const view = this.run.view;
						this.detach_run();
						view.node.remove();
						this.views = this.views.filter((v) => v !== view);
						const last = this.views.pop();
						if (last) last.node.remove();
						this.input.value = text;
						this.grow();
						if (!this.views.length && !this.conversation) this.show_welcome();
					}
				});
		}

		regenerate() {
			if (this.run || !this.conversation) return;
			const last = this.views[this.views.length - 1];
			if (!last || last.message.role !== "assistant") return;
			last.node.remove();
			this.views.pop();
			const run_id = hex(8);
			this.attach_run(run_id, 0, true);
			call("regenerate", { conversation: this.conversation, run_id })
				.then((res) => {
					if (res && this.run && this.run.id === run_id && res.run_id !== run_id) this.rename_run(res.run_id);
				})
				.catch(() => this.open_conversation(this.conversation));
		}

		stop() {
			if (!this.run) return;
			this.run.view.set_status(__("Stopping…"));
			call("stop", { run_id: this.run.id });
		}

		// ------------------------------------------------------------ runs

		attach_run(run_id, after, fresh) {
			const view = this.add_message({ role: "assistant", parts: [], status: "Running" });
			view.set_status(__("Thinking…"));
			this.run = { id: run_id, seq: after || 0, view, buffer: new Map(), last_event: fresh ? Date.now() : 0, started: Date.now(), polls: 0 };
			this.set_busy(true);
			omni_ai.__runs.set(run_id, this);
			const tick = () => {
				const run = this.run;
				if (!run) return;
				// realtime normally delivers everything; polling is the safety net and the fallback
				const quiet = Date.now() - run.last_event;
				const delay = quiet > 2500 ? 900 : 3500;
				run.timer = setTimeout(() => {
					if (this.run !== run) return;
					call("poll", { run_id: run.id, after: run.seq }, { type: "GET" })
						.then((res) => {
							if (this.run !== run || !res) return;
							run.polls += 1;
							(res.events || []).forEach((ev) => {
								if (this.receive(run.id, ev)) omni_ai.stats.polled += 1;
							});
							if (this.run !== run) return;
							if (res.missing) {
								// the server has not registered this run yet (the send request is still on its way)
								if (Date.now() - run.started > 30000) {
									run.view.show_error(__("The reply was lost. Please try again."));
									return this.end_run();
								}
								return tick();
							}
							if (res.status === "queued" && res.waiting > 15) {
								run.view.set_status(__("Waiting for a background worker… If this continues, ask your administrator to check the workers."));
							}
							if (res.done && !(res.events || []).length && this.conversation) {
								// finished without us seeing the end (missed events): reload the stored reply
								return this.open_conversation(this.conversation);
							}
							tick();
						})
						.catch(() => this.run === run && tick());
				}, delay);
			};
			tick();
		}

		rename_run(run_id) {
			omni_ai.__runs.delete(this.run.id);
			this.run.id = run_id;
			omni_ai.__runs.set(run_id, this);
		}

		detach_run() {
			if (!this.run) return;
			clearTimeout(this.run.timer);
			omni_ai.__runs.delete(this.run.id);
			this.run = null;
			this.set_busy(false);
		}

		end_run() {
			const run = this.run;
			if (!run) return;
			run.view.finish();
			this.detach_run();
			this.stick();
			if (this.mode === "page") setTimeout(() => this.load_history(this.$(".omni-side-search").value), 2500);
		}

		receive(run_id, ev) {
			const run = this.run;
			if (!run || run.id !== run_id) return false;
			if (!ev || !ev.seq || ev.seq <= run.seq || run.buffer.has(ev.seq)) return false;
			run.buffer.set(ev.seq, ev);
			run.last_event = Date.now();
			while (this.run === run && run.buffer.has(run.seq + 1)) {
				const next = run.buffer.get(run.seq + 1);
				run.buffer.delete(run.seq + 1);
				run.seq += 1;
				this.apply(run, next);
			}
			return true;
		}

		apply(run, ev) {
			const view = run.view;
			if (ev.t === "text") {
				view.clear_status();
				view.append_text(ev.d);
			} else if (ev.t === "part") {
				view.clear_status();
				view.add_part(ev.p);
				this.stick();
			} else if (ev.t === "part_update") {
				view.update_part(ev.id, ev.patch || {});
			} else if (ev.t === "status") {
				const last = view.parts[view.parts.length - 1];
				if (!last || last.type !== "text" || !view.live) view.set_status(__("Thinking…"));
			} else if (ev.t === "error") {
				view.show_error(ev.message);
			} else if (ev.t === "title") {
				this.set_title(ev.title);
			} else if (ev.t === "done") {
				if (ev.status === "Stopped") view.note(__("Stopped"));
				view.message.status = ev.status;
				this.end_run();
			}
			this.stick();
		}
	}

	// ------------------------------------------------------------------- shell

	const omni_ai = {
		__loaded: true,
		__runs: new Map(),
		// which channel delivered the streamed events; handy when diagnosing a site's realtime setup
		stats: { realtime: 0, polled: 0 },
		panel: null,
		chat: null,

		enabled() {
			return !!(frappe.boot && frappe.boot.omni_ai);
		},

		apply_theme(node) {
			const c = frappe.boot.omni_ai;
			node.style.setProperty("--omni-accent", c.accent_color || "#4F46E5");
			node.style.setProperty("--omni-accent-fg", readable_on(c.accent_color || "#4F46E5"));
		},

		ensure() {
			if (this.panel) return;
			this.panel = el(`<div class="omni-panel" aria-hidden="true"><div class="omni-resize" title="${esc(__("Drag to resize"))}"></div></div>`);
			this.apply_theme(this.panel);
			const width = store.get("width", 440);
			this.panel.style.setProperty("--omni-width", Math.min(Math.max(width, 360), Math.max(360, window.innerWidth - 80)) + "px");
			document.body.appendChild(this.panel);
			this.chat = new OmniChat({ container: this.panel, mode: "panel" });

			const handle = this.panel.querySelector(".omni-resize");
			handle.addEventListener("pointerdown", (e) => {
				e.preventDefault();
				handle.setPointerCapture(e.pointerId);
				const rtl = getComputedStyle(document.documentElement).direction === "rtl";
				const move = (ev) => {
					const w = rtl ? ev.clientX : window.innerWidth - ev.clientX;
					const clamped = Math.min(Math.max(w, 360), Math.max(360, window.innerWidth - 80));
					this.panel.style.setProperty("--omni-width", clamped + "px");
					store.set("width", clamped);
				};
				const up = () => {
					handle.removeEventListener("pointermove", move);
					handle.removeEventListener("pointerup", up);
					this.chat.redraw_charts();
				};
				handle.addEventListener("pointermove", move);
				handle.addEventListener("pointerup", up);
			});
			this.panel.addEventListener("keydown", (e) => {
				if (e.key === "Escape" && !document.querySelector(".modal.show")) this.close();
			});
		},

		open(prefill) {
			if (!this.enabled()) return;
			if (frappe.get_route && (frappe.get_route() || [])[0] === "ai-assistant") {
				if (prefill && this.page_chat) this.page_chat.input.value = prefill;
				return;
			}
			const first = !this.panel;
			this.ensure();
			this.place();
			this.panel.classList.add("is-open");
			this.panel.setAttribute("aria-hidden", "false");
			document.body.classList.add("omni-open");
			if (this.launcher) this.launcher.classList.add("is-hidden");
			store.set("open", true);
			if (first) {
				const last = store.get("last_conversation", null);
				if (last) this.chat.open_conversation(last);
			}
			this.chat.refresh_context();
			if (prefill) {
				this.chat.input.value = prefill;
				this.chat.grow();
			}
			setTimeout(() => {
				this.chat.input.focus();
				this.chat.redraw_charts();
			}, 220);
		},

		/** Keep the desk's top bar (v15) usable: the panel starts below it. v16 has no top bar. */
		place() {
			if (!this.panel) return;
			let top = 0;
			const bar = document.querySelector("header.navbar");
			if (bar && bar.offsetParent !== null && window.innerWidth > 767) {
				const rect = bar.getBoundingClientRect();
				if (rect.top <= 0 && rect.height < 120) top = Math.max(0, Math.round(rect.bottom));
			}
			this.panel.style.setProperty("--omni-top", top + "px");
		},

		close() {
			if (!this.panel) return;
			this.panel.classList.remove("is-open");
			this.panel.setAttribute("aria-hidden", "true");
			document.body.classList.remove("omni-open");
			if (this.launcher) this.launcher.classList.remove("is-hidden");
			store.set("open", false);
		},

		toggle() {
			this.panel && this.panel.classList.contains("is-open") ? this.close() : this.open();
		},

		/** Open the assistant and send a question straight away (usable from custom buttons and scripts). */
		ask(text) {
			this.open();
			if (!text) return;
			const chat = (frappe.get_route() || [])[0] === "ai-assistant" ? this.page_chat : this.chat;
			if (!chat || chat.run) return;
			chat.input.value = text;
			chat.send();
		},

		mount_launcher() {
			const c = frappe.boot.omni_ai;
			if (this.launcher || c.launcher_position === "Hidden") return;
			const side = c.launcher_position === "Bottom Left" ? "is-left" : "is-right";
			this.launcher = el(`<button class="omni-launcher ${side}" title="${esc(c.brand_name)} (Ctrl+.)" aria-label="${esc(__("Open {0}", [c.brand_name]))}">
				${c.brand_logo ? `<img src="${esc(c.brand_logo)}" alt="">` : ICONS.spark}
			</button>`);
			this.apply_theme(this.launcher);
			this.launcher.addEventListener("click", () => this.open());
			document.body.appendChild(this.launcher);
		},

		sync_route() {
			const on_page = (frappe.get_route() || [])[0] === "ai-assistant";
			if (this.launcher) this.launcher.classList.toggle("is-away", on_page);
			if (on_page) this.close();
			else if (this.chat) this.chat.refresh_context();
		},

		boot() {
			if (!this.enabled() || this.__booted) return;
			this.__booted = true;
			this.mount_launcher();
			frappe.realtime.on(EVENT, (data) => {
				if (!data || !data.run_id) return;
				const chat = this.__runs.get(data.run_id);
				if (!chat) return;
				(data.events || []).forEach((ev) => {
					if (chat.receive(data.run_id, ev)) this.stats.realtime += 1;
				});
			});
			if (frappe.router && frappe.router.on) frappe.router.on("change", () => this.sync_route());
			$(document).on("form-refresh page-change", () => this.chat && this.chat.refresh_context());
			// every saved document gets an "Ask <assistant> about this" entry in its menu
			$(document).on("form-refresh", (e, frm) => {
				try {
					if (!frm || !frm.page || (frm.is_new && frm.is_new())) return;
					frm.page.add_menu_item(__("Ask {0} about this", [frappe.boot.omni_ai.brand_name]), () => {
						this.open();
						if (this.chat) this.chat.use_context = true;
						if (this.chat) this.chat.refresh_context();
					});
				} catch (err) {
					/* a menu entry is a nicety; never break a form over it */
				}
			});
			document.addEventListener("keydown", (e) => {
				if ((e.ctrlKey || e.metaKey) && e.key === "." && !e.shiftKey && !e.altKey) {
					e.preventDefault();
					if ((frappe.get_route() || [])[0] === "ai-assistant") return;
					this.toggle();
				}
			});
			this.sync_route();
			window.addEventListener("resize", () => this.place());
			if (store.get("open", false) && window.innerWidth > 900) this.open();
		},

		/** Used by the full-page view (ai-assistant page). */
		mount_page(container, conversation) {
			this.apply_theme(container);
			container.replaceChildren();
			this.page_chat = new OmniChat({ container, mode: "page" });
			if (conversation) this.page_chat.open_conversation(conversation);
			return this.page_chat;
		},
	};

	window.omni_ai = omni_ai;
	$(document).on("startup", () => omni_ai.boot());
	$(() => frappe.boot && frappe.boot.omni_ai && setTimeout(() => omni_ai.boot(), 0));
})();
