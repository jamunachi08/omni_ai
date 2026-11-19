"""Redaction of secrets before they reach the audit log. Plain unittest: no Frappe needed."""

import json
import unittest

from omni_ai.engine.redact import REDACTED, redact


class TestRedact(unittest.TestCase):
	def test_secret_values_in_json_are_removed_and_json_stays_valid(self):
		text = json.dumps({"filters": {"password": "hunter2", "status": "Open"}, "api_key": "abc123xyz"})
		out = redact(text)
		self.assertNotIn("hunter2", out)
		self.assertNotIn("abc123xyz", out)
		self.assertEqual(json.loads(out)["filters"]["status"], "Open")
		self.assertEqual(json.loads(out)["filters"]["password"], REDACTED)

	def test_secrets_in_sql_text(self):
		out = redact("select * from `tabUser` where pwd = 'abc123' and name = 'x'")
		self.assertNotIn("abc123", out)
		self.assertIn("name = 'x'", out)

	def test_known_token_shapes(self):
		for secret in (
			"sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV",
			"ghp_" + "a" * 36,
			"xoxb-1234567890-abcdefghij",
			"AKIA" + "ABCDEFGHIJKLMNOP",
			"erpmcp_" + "Zz9" * 12,
			"eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",
		):
			self.assertNotIn(secret, redact(f"called with {secret} today"), secret)

	def test_authorization_headers(self):
		self.assertNotIn("a1b2c3d4", redact("Authorization: token a1b2c3d4:e5f6g7h8"))
		self.assertNotIn("dXNlcjpwYXNz", redact('{"Authorization": "Basic dXNlcjpwYXNz"}'))
		self.assertNotIn("0123456789abcdef", redact("sent Bearer 0123456789abcdefghij"))

	def test_password_in_url_and_private_key(self):
		self.assertEqual(redact("mysql://root:s3cret@db.local/erp"), f"mysql://root:{REDACTED}@db.local/erp")
		key = "-----BEGIN PRIVATE KEY-----\nMIIBVQIBADANBg\n-----END PRIVATE KEY-----"
		self.assertEqual(redact(f"x {key} y"), f"x {REDACTED} y")

	def test_ordinary_text_is_left_alone(self):
		for text in (
			"Invalid token provided",
			"basic configuration page",
			"secret santa gift list",
			'{"fields":["name","api_key"],"doctype":"User"}',  # a field NAME is not a value
			"Sales Invoice SINV-00012 for ACME, total 1,250.00",
			"إجازة سنوية للموظف",
		):
			self.assertEqual(redact(text), text, text)

	def test_personal_data_only_when_asked(self):
		text = "customer ali@example.com phone +966512345678"
		self.assertEqual(redact(text), text)
		masked = redact(text, mask_personal_data=True)
		self.assertNotIn("ali@example.com", masked)
		self.assertNotIn("+966512345678", masked)

	def test_idempotent_and_safe_on_odd_input(self):
		once = redact('{"token":"abcdef123456"}')
		self.assertEqual(redact(once), once)
		self.assertEqual(redact(""), "")
		self.assertIsNone(redact(None))


if __name__ == "__main__":
	unittest.main()
