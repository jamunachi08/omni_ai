"""Posting a scheduled result to a team chat channel (Slack, Microsoft Teams, Google Chat).

Only incoming-webhook addresses of the chosen service are accepted, so a schedule cannot be used
to make the server call arbitrary addresses. Other hosts (for example a self-hosted Mattermost,
which accepts the Slack format) can be allowed in site_config.json:

    "ai_assistant_chat_webhook_hosts": ["chat.example.com"]
"""

import re
from urllib.parse import urlparse

import frappe
import requests

CHANNELS = {
	"Slack": ("hooks.slack.com",),
	"Google Chat": ("chat.googleapis.com",),
	"Microsoft Teams": (".webhook.office.com", ".logic.azure.com", ".powerplatform.com"),
}
MAX_TEXT = 3000


def _extra_hosts() -> list:
	hosts = frappe.conf.get("ai_assistant_chat_webhook_hosts") or []
	return [str(h).lower() for h in hosts] if isinstance(hosts, (list, tuple)) else []


def check_webhook(channel: str, url: str) -> str:
	"""The cleaned URL, or raise ValueError saying why it is not acceptable."""
	url = (url or "").strip()
	if channel not in CHANNELS:
		raise ValueError("Choose Slack, Microsoft Teams or Google Chat.")
	parsed = urlparse(url)
	host = (parsed.netloc or "").lower()
	if host in _extra_hosts() and parsed.scheme in ("http", "https"):
		return url
	if parsed.scheme != "https" or not host or "@" in host:
		raise ValueError("The webhook address must start with https://")
	host = host.split(":")[0]
	for allowed in CHANNELS[channel]:
		if host == allowed or (allowed.startswith(".") and host.endswith(allowed)):
			return url
	raise ValueError(f"This is not a {channel} incoming-webhook address.")


def _norm_url(url) -> str:
	return str(url or "").strip().rstrip("/")


def webhook_approved(url: str) -> bool:
	"""Is this address on the administrator's list of approved chat webhooks (AI Assistant Settings)?"""
	listed = frappe.get_cached_doc("AI Assistant Settings").get("approved_chat_webhooks") or ""
	return bool(_norm_url(url)) and _norm_url(url) in {_norm_url(line) for line in listed.splitlines() if line.strip()}


def plain_text(markdown: str, bold: str = "*") -> str:
	"""Markdown as the simple text chat services render: headings and bold become bold, tables are dropped."""
	lines = []
	for line in (markdown or "").splitlines():
		if re.match(r"^\s*\|", line):
			continue
		line = re.sub(r"^#{1,6}\s+(.*)$", rf"{bold}\1{bold}", line)
		line = re.sub(r"\*\*(.+?)\*\*", rf"{bold}\1{bold}", line)
		lines.append(line)
	text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
	return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1].rstrip() + "…"


def build_body(channel: str, title: str, text: str, link: str) -> dict:
	if channel == "Microsoft Teams":
		return {
			"type": "message",
			"attachments": [
				{
					"contentType": "application/vnd.microsoft.card.adaptive",
					"content": {
						"$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
						"type": "AdaptiveCard",
						"version": "1.4",
						"body": [
							{"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True},
							{"type": "TextBlock", "text": plain_text(text, "**"), "wrap": True},
						],
						"actions": [{"type": "Action.OpenUrl", "title": "Open the full result", "url": link}],
					},
				}
			],
		}
	if channel == "Slack":
		return {"text": f"*{title}*\n{plain_text(text)}\n<{link}|Open the full result>"}
	return {"text": f"*{title}*\n{plain_text(text)}\n{link}"}


def post(channel: str, url: str, title: str, text: str, link: str):
	"""Send the message. Raises on any failure."""
	url = check_webhook(channel, url)
	response = requests.post(url, json=build_body(channel, title, text, link), timeout=15, allow_redirects=False)
	if response.status_code >= 300:
		raise RuntimeError(f"{channel} answered {response.status_code}")
