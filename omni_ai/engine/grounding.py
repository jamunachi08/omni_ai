"""Check that the large figures in an answer can be traced to what the queries returned.

The main risk of a data assistant is a confident wrong number: a total added up in its head, a
figure carried over from memory, a typo. This check is deterministic and cheap. It takes every
large number in the final text and looks for it in the material of this conversation: tool
results, full result sets, column totals, row counts, and what the user wrote. Numbers it cannot
find are sent back to the assistant once to be recomputed with a query; if they still cannot be
traced, the answer is shown with a visible caveat.

It is a safety net, not a proof: a wrong number that happens to equal some other figure in the
results passes, and a correct derived figure (a ratio, a growth amount) can be flagged.
"""

import re

MIN_VALUE = 1000  # smaller numbers (counts, days, percentages) are not checked
MAX_SOURCE_NUMBERS = 200000
PAIRWISE_LIMIT = 250
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")
UNITS = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}

CANDIDATE_RE = re.compile(
	r"(?<![\w.,/:#-])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
	r"(?:\s?(k|m|mn|b|bn|thousand|million|billion)\b)?(?![\w/:-]|[.,]\d)",
	re.I,
)
SOURCE_RE = re.compile(r"\d+(?:\.\d+)?")
STRIP_RES = (
	re.compile(r"```.*?```", re.S),  # code blocks
	re.compile(r"`[^`\n]*`"),  # inline code
	re.compile(r"\]\([^)]*\)"),  # link targets
	re.compile(r"https?://\S+"),
)


def _source_numbers(text: str) -> set:
	text = (text or "").translate(ARABIC_DIGITS)
	out = set()
	for variant in (text, re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)):
		for token in SOURCE_RE.findall(variant):
			try:
				out.add(round(float(token), 2))
			except ValueError:
				pass
			if len(out) > MAX_SOURCE_NUMBERS:
				return out
	return out


def collect_sources(texts: list, results: list) -> dict:
	"""Every number the answer may legitimately quote."""
	seen, headline = set(), set()
	for text in texts:
		numbers = _source_numbers(text)
		seen |= numbers
		headline |= numbers
	for result in results:
		rows = result.get("rows") or []
		seen.add(float(len(rows)))
		width = max((len(r) for r in rows), default=0)
		totals = [0.0] * width
		numeric = [False] * width
		for row in rows:
			for index, value in enumerate(row):
				if isinstance(value, (int, float)) and not isinstance(value, bool):
					seen.add(round(float(value), 2))
					totals[index] += float(value)
					numeric[index] = True
		for index in range(width):
			if numeric[index]:
				seen.add(round(totals[index], 2))
				headline.add(round(totals[index], 2))
	# simple arithmetic between figures the assistant actually saw (differences, sums of two)
	big = sorted(n for n in headline if abs(n) >= 100 and not (1900 <= n <= 2100 and n == int(n)))[-PAIRWISE_LIMIT:]
	derived = set()
	for i, a in enumerate(big):
		for b in big[i + 1 :]:
			derived.add(round(a + b, 2))
			derived.add(round(abs(a - b), 2))
	return {"seen": seen, "derived": derived}


def candidates(answer: str) -> list:
	"""[(text as written, value, tolerance)] for the large numbers in an answer."""
	text = (answer or "").translate(ARABIC_DIGITS)
	for pattern in STRIP_RES:
		text = pattern.sub(" ", text)
	out, done = [], set()
	for match in CANDIDATE_RE.finditer(text):
		raw, unit = match.group(1), (match.group(2) or "").lower()
		if text[match.end() : match.end() + 1] == "%" or text[match.end() : match.end() + 2] == " %":
			continue
		digits = raw.replace(",", "")
		if len(digits) > 1 and digits[0] == "0" and "." not in digits:
			continue  # a code or phone number, not a quantity
		try:
			value = float(digits)
		except ValueError:
			continue
		scale = UNITS.get(unit, 1.0)
		decimals = len(digits.split(".")[1]) if "." in digits else 0
		if scale == 1.0 and "," not in raw and decimals == 0 and 1900 <= value <= 2100:
			continue  # a year
		value *= scale
		if value < MIN_VALUE:
			continue
		written = match.group(0).strip()
		if written in done:
			continue
		done.add(written)
		out.append((written, value, 0.5 * scale * (10**-decimals) + 0.011))
	return out


def untraced(answer: str, sources: dict) -> list:
	"""The figures in `answer` that match nothing in `sources`, as written."""
	import bisect

	seen, derived = sorted(sources["seen"]), sorted(sources["derived"])

	def near(pool, value, tolerance):
		index = bisect.bisect_left(pool, value - tolerance)
		return index < len(pool) and pool[index] <= value + tolerance

	missing = []
	for written, value, tolerance in candidates(answer):
		# a rounded figure ("about 129,000") counts when within half a percent of a real one;
		# a figure derived from two others must match as written
		if near(seen, value, max(tolerance, 0.005 * value)) or near(derived, value, tolerance):
			continue
		missing.append(written)
	return missing[:12]
