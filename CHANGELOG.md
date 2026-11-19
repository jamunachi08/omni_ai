# Changelog

## 1.5.1

**Security**
- **SQL `*` on credential tables.** Credential columns were removed from results by name, but a UNION
  (or a CTE column list) could give a `*` column another name and return it. `*` is now refused on any
  table that has a credential column; `count(*)` and `*` on other tables still work.
- **Credential field names** now also cover `secret` (not "secretary"), names ending in `token`, `csid`
  and `basic_auth`, so custom fields such as ZATCA CSIDs and consumer secrets are never read. Report
  results drop such columns too. The ERP MCP Token and Settings DocTypes are hidden from the assistant.
- **Saved Query Reports** get a Roles table: only the creator's roles that can read every DocType in the
  SQL may run them (System Manager if none qualifies). Before, anyone who could read the report's main
  DocType ran its SQL, which does not apply their own permissions.
- **Scheduled task chat posts**: users who are not System Managers can only post to webhooks listed in the
  new **Approved Chat Webhooks** setting, matching the rule that emails go only to their own address.
  Existing tasks with an unlisted webhook keep emailing and skip the post.
- **Audit log**: Tool Log and Check Log rows can no longer be deleted from the desk, and the Tool Log has
  its own retention (**Keep Audit Log For (days)**, 365 by default, 0 = forever) instead of following the
  conversation retention.
- **Exports**: text that a spreadsheet would run as a formula (`=`, `+`, `-`, `@` ...) is prefixed with an
  apostrophe in CSV and Excel exports.
- **Duplicate entry check** no longer shows the ID of a matching record the user cannot read, and escapes
  the values it shows.

**Fixes**
- Pressing Confirm twice (or in two tabs) could run an action twice. The action row is now locked while it
  runs; the second press waits and then reports it is already completed.
- Index on AI Assistant Message (owner, role, creation) for the per-user daily and monthly limits, which
  run on every message.

**Other**
- README: Inline execution is for testing only (it is cut off by the web request timeout, about two minutes
  on Frappe Cloud).
- Unused imports removed; author set to Neotec Integrated Solutions; Arabic translations for the new text.
- New Frappe-free tests for these guards (`tests/test_guards.py`).

**Upgrading**: run `bench migrate` (new settings fields, log permissions, index). Then, if anyone posts
scheduled results to chat, add those webhook addresses to **Approved Chat Webhooks**. Saved Query Reports
made before 1.5.1 have no Roles: review them under Report list, filter Report Type = Query Report.

## 1.5.0

- **Arabic matching** in the knowledge library, memory recall and the duplicate entry check:
  hamza forms of alef, ة/ه, ى/ي, diacritics, the stretching line and Arabic-Indic digits are treated
  as the same; a leading ال and common plural/feminine endings are ignored when matching words.
  Fixes words with diacritics being cut in two. Nothing needs re-indexing: words are compared when
  a question is asked.
- The duplicate check's database pre-filter now tolerates those spelling variants (it picks the
  word with the fewest ambiguous letters and uses wildcards for the rest).
- **Tool Log**: secrets (passwords, tokens, API keys, Authorization headers, private keys, passwords
  in URLs) are replaced by `[REDACTED]` before arguments and errors are written; with personal-data
  masking on, personal data is masked too.
- **Tool Log** has new columns: *Source* (Chat, Scheduled or MCP), *Client* and *MCP Token*, filled by
  the ERP MCP app 1.3+.
- `omni_ai.tools.log_call` is now public (the old `_log` name still works).
- Tests for the normaliser and the redactor (no Frappe needed).

**Upgrading**: run `bench migrate` for the new Tool Log columns.
