"""Arabic normalisation and tokenising. Plain unittest: no Frappe needed."""

import unittest

from omni_ai.engine.textnorm import best_prefilter_word, like_pattern, normalize_set, normalize_text, stem_arabic, tokens


class TestNormalize(unittest.TestCase):
	def test_spelling_variants_fold_together(self):
		self.assertEqual(normalize_text("إجازة"), normalize_text("اجازه"))
		self.assertEqual(normalize_text("أحمد"), normalize_text("احمد"))
		self.assertEqual(normalize_text("مستشفى"), normalize_text("مستشفي"))
		self.assertEqual(normalize_text("مسؤول"), "مسوول")

	def test_diacritics_and_tatweel_removed(self):
		self.assertEqual(normalize_text("الموظَّفون"), "الموظفون")
		self.assertEqual(normalize_text("كــتاب"), "كتاب")

	def test_arabic_digits_become_ascii(self):
		self.assertEqual(normalize_text("٢١ يوم"), "21 يوم")
		self.assertEqual(normalize_text("۱۲۳"), "123")

	def test_latin_text_untouched(self):
		self.assertEqual(normalize_text("Annual Leave 21"), "Annual Leave 21")
		self.assertEqual(normalize_text(None), "")


class TestTokens(unittest.TestCase):
	def test_diacritics_do_not_split_a_word(self):
		# without normalising first, a combining mark cuts the word in two
		self.assertEqual(tokens("الموظَّفون"), tokens("الموظفون"))
		self.assertEqual(len(tokens("الموظَّفون")), 1)

	def test_inflections_meet_in_one_stem(self):
		forms = ["إجازة", "اجازه", "الإجازة", "الإجازات", "للإجازات"]
		self.assertEqual({stem_arabic(normalize_text(f)) for f in forms}, {"اجاز"})
		self.assertEqual({tokens(f)[0] for f in ["سنوية", "سنوي", "السنوية", "السنوي"]}, {"سنو"})
		self.assertEqual({tokens(f)[0] for f in ["الموظف", "الموظفين", "للموظفين", "موظفون"]}, {"موظف"})

	def test_short_words_are_not_over_stemmed(self):
		for word in ("علي", "وجه", "الله", "الا"):
			self.assertEqual(stem_arabic(normalize_text(word)), normalize_text(word), word)

	def test_latin_words_only_lowercased(self):
		self.assertEqual(tokens("Annual LEAVE days"), ["annual", "leave", "days"])

	def test_stopwords_match_whatever_the_spelling(self):
		stop = normalize_set(["إلى", "the"])
		self.assertEqual(tokens("الى الموظف the office", stop), ["موظف", "office"])

	def test_query_finds_text_written_differently(self):
		text = "تُمنح إجازة سنوية مدتها 21 يوماً لكل موظف."
		for query in ("اجازه سنويه", "الإجازات السنوية", "إجازة سنوية"):
			self.assertTrue(set(tokens(query)) <= set(tokens(text)), query)
		self.assertIn("21", tokens(text))
		self.assertIn("21", tokens("٢١"))  # Arabic-Indic digits meet ASCII


class TestPrefilter(unittest.TestCase):
	def test_like_pattern_wildcards_variant_letters(self):
		# ا و ة have other spellings, so they become single-character wildcards; ل and ح do not
		self.assertEqual(like_pattern("الواحة"), "_ل__ح_")
		self.assertEqual(like_pattern("شركة"), "شرك_")

	def test_like_pattern_leaves_plain_letters(self):
		self.assertEqual(like_pattern("Noor"), "Noor")
		self.assertEqual(like_pattern("شركت"), "شركت")

	def test_best_word_prefers_the_most_selective(self):
		self.assertEqual(best_prefilter_word(["الواحة", "شركت"]), "شركت")  # fewer wildcard letters wins
		self.assertEqual(best_prefilter_word(["123", "Noor"]), "Noor")  # digits are wildcards too
		self.assertEqual(best_prefilter_word(["abcd", "abcdef"]), "abcdef")  # then the longest
		self.assertIsNone(best_prefilter_word([]))


class TestEntryCheckNorm(unittest.TestCase):
	"""The duplicate check's norm() needs the Frappe-loaded module; skipped when Frappe is absent."""

	def test_norm_treats_arabic_variants_as_equal(self):
		try:
			from omni_ai.checks.engine import norm
		except ImportError:
			self.skipTest("Frappe is not installed")
		self.assertEqual(norm("شركة  الواحة"), norm("شركه الواحه"))
		self.assertEqual(norm("أحمد محمد"), norm("احمد محمد"))
		self.assertEqual(norm("Al-Noor  Trading Co."), norm("alnoor trading co"))  # the old behaviour stays
		self.assertNotEqual(norm("Al Noor Trading"), norm("Al Noor Trading 2"))


if __name__ == "__main__":
	unittest.main()
