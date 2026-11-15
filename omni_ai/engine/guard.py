"""Cost and availability guards: a per-minute message limit, a monthly token budget and a circuit breaker.

All counters live in Redis and expire by themselves, so nothing here needs a table or a cleanup job.
"""

import time

import frappe
from frappe import _
from frappe.utils import get_first_day, nowdate

from omni_ai.engine.client import ProviderError
from omni_ai.engine.stream import fresh_cache_value
from omni_ai.utils import get_settings, to_int

FAILURES_TO_OPEN = 3  # failed calls (each already retried by the client) ...
FAILURE_WINDOW = 120  # ... within this many seconds open the breaker ...
OPEN_SECONDS = 60  # ... for this long; then one call is let through to test the service


class ServicePaused(ProviderError):
	"""The breaker is open: the service has been failing and calls are paused for a short while."""


# -- rate limit ----------------------------------------------------------------------


def check_rate(user: str):
	"""Allow at most N messages per user per minute (fixed one-minute windows)."""
	limit = to_int(get_settings().messages_per_minute)
	if limit <= 0:
		return
	key = frappe.cache.make_key(f"omni_ai:rate:{user}:{int(time.time() // 60)}")
	try:
		count = frappe.cache.incr(key)
		if count == 1:
			frappe.cache.expire(key, 90)
	except Exception:
		return  # never block chatting because Redis hiccupped
	if count > limit:
		frappe.throw(
			_("You are sending messages too quickly. Please wait a minute and try again."),
			getattr(frappe, "TooManyRequestsError", frappe.ValidationError),
		)


# -- monthly budget ---------------------------------------------------------------------


def tokens_this_month(user: str) -> int:
	row = frappe.db.sql(
		"""select coalesce(sum(coalesce(input_tokens, 0) + coalesce(output_tokens, 0)), 0)
		from `tabAI Assistant Message` where owner = %s and role = 'assistant' and creation >= %s""",
		(user, f"{get_first_day(nowdate())} 00:00:00"),
	)
	return to_int(row[0][0]) if row else 0


def check_budget(user: str):
	budget = to_int(get_settings().monthly_token_budget)
	if budget > 0 and tokens_this_month(user) >= budget:
		frappe.throw(_("You have used this month's AI allowance. It resets on the 1st of next month."))


# -- circuit breaker ----------------------------------------------------------------------


def _key(target: str, what: str) -> str:
	return f"omni_ai:breaker:{target}:{what}"


def check_open(target: str, label: str = "The AI provider"):
	"""Raise ServicePaused when calls to `target` are paused."""
	until = fresh_cache_value(_key(target, "open_until")) or 0
	remaining = int(float(until) - time.time())
	if remaining > 0:
		raise ServicePaused(f"{label} has been failing, so requests are paused for {remaining} more seconds. Please try again shortly.")


def record_failure(target: str):
	now = time.time()
	failures = [t for t in (fresh_cache_value(_key(target, "failures")) or []) if now - t < FAILURE_WINDOW] + [now]
	frappe.cache.set_value(_key(target, "failures"), failures, expires_in_sec=FAILURE_WINDOW)
	if len(failures) >= FAILURES_TO_OPEN:
		frappe.cache.set_value(_key(target, "open_until"), now + OPEN_SECONDS, expires_in_sec=OPEN_SECONDS)
		frappe.cache.delete_value(_key(target, "failures"))


def record_success(target: str):
	if fresh_cache_value(_key(target, "failures")):
		frappe.cache.delete_value(_key(target, "failures"))


def is_outage(exc) -> bool:
	"""Only failures that mean 'the service is down' count; a bad key or a bad request does not."""
	status = getattr(exc, "status", None)
	kind = getattr(exc, "kind", None)
	if status in (500, 502, 503, 504, 529):
		return True
	return status is None and kind in ("connection_error", "overloaded_error", "incomplete")
