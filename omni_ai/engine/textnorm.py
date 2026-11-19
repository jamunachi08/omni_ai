"""Text normalisation for matching: Arabic spelling variants, a light Arabic stemmer, tokenising.

Plain Python, no Frappe import, so it can be tested anywhere.

Why it exists: the same Arabic word is written in several ways (إجازة / اجازة / اجازه, with or without
diacritics, with or without ال, singular or plural). Keyword search and duplicate checks that compare
letters exactly miss those. Normalising both the query and the stored text the same way makes them match.

Order matters: diacritics must be removed BEFORE splitting text into words, because a combining mark is
not a "word character" and would otherwise cut the word in two.
"""

import re
import unicodedata

# letters written in more than one way, folded to one form
_LETTERS = {
	"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",  # alef with hamza / madda / wasla -> alef
	"ى": "ي", "ئ": "ي", "ی": "ي",  # alef maksura, hamza on yeh, Persian yeh -> yeh
	"ة": "ه",  # teh marbuta -> heh (people type one for the other constantly)
	"ؤ": "و",  # hamza on waw -> waw
	"ک": "ك",  # Persian kaf -> kaf
}
_TABLE = {ord(k): v for k, v in _LETTERS.items()}
for _i in range(10):  # Arabic-Indic and Persian digits -> 0-9
	_TABLE[0x0660 + _i] = str(_i)
	_TABLE[0x06F0 + _i] = str(_i)
_TABLE[0x0640] = None  # tatweel (the stretching line)
for _cp in list(range(0x0600, 0x0700)) + list(range(0x08D3, 0x0900)):
	if unicodedata.category(chr(_cp)) == "Mn":  # fatha, damma, shadda, sukun, dagger alef, Quranic marks...
		_TABLE[_cp] = None

_WORD_RE = re.compile(r"[^\W\d_]{2,}|\d+", re.UNICODE)
_ARABIC_START = re.compile(r"[\u0621-\u064A]")

# attached article / prepositions; longest first. Only removed when 3+ letters remain.
_PREFIXES = ("وال", "بال", "كال", "فال", "لل", "ال")
# plural, dual, pronoun and feminine endings (after normalisation ة is already ه); longest first
_SUFFIXES = ("ات", "ون", "ين", "ان", "ها", "هم", "يه", "ه", "ي")
_MIN_STEM = 3
# words whose ending is part of the word, not an inflection
_KEEP = frozenset({"الله"})

# letters that have more than one spelling, used to build a forgiving SQL LIKE pattern
_VARIANT_LETTERS = "اأإآٱىيئةهؤو"
_VARIANT_RE = re.compile(f"[{_VARIANT_LETTERS}0-9]")  # digits too: 123 may be stored as ١٢٣


def normalize_text(text) -> str:
	"""Fold Arabic spelling variants, drop diacritics and tatweel, unify digits. Latin text is unchanged."""
	return str("" if text is None else text).translate(_TABLE)


def stem_arabic(word: str) -> str:
	"""Strip a leading ال-type prefix and one common ending from an already normalised Arabic word."""
	if not word or word in _KEEP or not _ARABIC_START.match(word):
		return word
	for prefix in _PREFIXES:
		if word.startswith(prefix) and len(word) - len(prefix) >= _MIN_STEM:
			word = word[len(prefix):]
			break
	for suffix in _SUFFIXES:
		if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM:
			word = word[: -len(suffix)]
			break
	return word


def normalize_set(words) -> frozenset:
	"""Normalise a list of words (for example a stop-word list) so it can be compared with tokens()."""
	return frozenset(normalize_text(w).lower() for w in words)


def tokens(text, stopwords=frozenset(), stem: bool = True) -> list:
	"""Words of `text` in comparable form: normalised, lower case, without filler words, Arabic stemmed.

	`stopwords` must already be normalised (see normalize_set).
	"""
	found = _WORD_RE.findall(normalize_text(text).lower())
	return [stem_arabic(w) if stem else w for w in found if w not in stopwords]


def like_pattern(word: str) -> str:
	"""A SQL LIKE fragment for `word` that matches its other spellings: every letter or digit with variants becomes `_`."""
	return _VARIANT_RE.sub("_", word)


def best_prefilter_word(words: list):
	"""The word to pre-filter a duplicate search on: the one with the fewest variant letters, then the longest."""
	if not words:
		return None
	return min(words, key=lambda w: (len(_VARIANT_RE.findall(w)), -len(w)))
