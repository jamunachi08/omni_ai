# Security review notes

Version 1.1.0 followed a structured review of the app's own code (SQL injection, access control,
secrets, file upload, cross-site scripting, request forgery to other servers). This file records
what was found and changed, and what is still open. It was a self-review by the author of the code,
not an independent audit.

## Found and fixed in 1.1.0

| # | Severity | Finding | Fix |
|---|---|---|---|
| 1 | High | The read-only SQL guard only checked tables that directly followed `FROM` or `JOIN`. A comma join (`` FROM `tabToDo` t, __Auth a ``) or a backtick-quoted internal table reached tables that are not DocTypes, including `__Auth`, which holds password hashes and encrypted secrets. Exploitable by a user with the SQL role, or through text that tricks the assistant into writing such a query for that user. | Every name in the query, quoted or not, is compared with the list of tables and schemas in the database; only backtick-quoted DocType tables with read permission are accepted. Names starting with `__` are always refused. |
| 2 | Medium | `SELECT *` on a table such as User returned `api_key` and `reset_password_key`, because the credential check looked at the query text only. | Credential columns are removed from every result set by name. |
| 3 | Medium | String literals were recognised by a general SQL parser whose rules differ from MariaDB's (backslash escapes, `#` comments without a space). A crafted query could hide keywords from the checks. | The guard now scans the query itself with MariaDB's quoting rules, refuses backslashes, and refuses `#`, `--` and `/*` outside literals. |
| 4 | Low | Permission-aware tools could read fields named like credentials when they were plain Data fields (`api_key` on User). | Such field names are refused in queries and left out of documents, whatever their field type. More secret-bearing DocTypes are hidden. |
| 5 | Low | Users could edit their own conversation records through the REST API (title, owner field, internal note fields). | Users now have read-only access to those records; the owner field cannot be changed. |
| 6 | Low | The confirm-to-change tool blocked configuration modules, but a few code- or markup-bearing DocTypes live in ordinary modules (Number Card, Dashboard Chart, Custom HTML Block, Web Form and others). | These are blocked by name as well. |
| 7 | Low | `.xlsx` and `.docx` attachments are zip archives and were opened without checking their expanded size. | Files over 25 MB, or expanding to more than 150 MB, are refused. |

## Checked, no change needed

- Chat endpoints check that the conversation, run, file and action belong to the caller.
- Replies are rendered through an allow-list sanitiser; raw HTML from the model or from data is shown
  as text; links are limited to http(s), mailto and in-desk routes; images are not rendered.
- The API key is stored encrypted, never sent to the browser and never placed in prompts.
- Saved reports: SQL is validated again at save time; filter scripts are generated from validated
  data, not written by the model.
- Changes run through the document API as the confirming user.

## Open risks to manage

- **Prompt injection.** Text inside records, files and web pages can try to steer the assistant. It
  cannot bypass permissions or the confirmation cards, but it can make the assistant propose
  something misleading, or word an answer wrongly. Users should read cards before confirming.
- **SQL and row-level permissions.** SQL checks DocType-level read permission only. Keep the SQL
  role list short. A saved Query Report shows its results to everyone with the report's roles.
- **Web search** (off by default) sends search terms to the AI provider's search tool; a manipulated
  assistant could put data into a search term.
- **Memory.** A note is context for later answers. Review company notes before approving them.
- **Automations** send the confirmed parameters to the URL an administrator registered. Protect the
  webhook with the secret header and use HTTPS.
- **Entry checks are not a security boundary.** They catch mistakes. A user allowed to change a
  rule, or holding a role that may continue anyway, can pass them; rules do not apply to changes
  made directly in the database. Keep rule editing with System Managers.
- **Scheduled preparation.** A task marked for it can prepare documents and emails unattended. The
  data it reads can contain text that tries to steer it; whatever it prepares waits for a person,
  who sees the full content before approving. Approve items as carefully as you would a
  colleague's draft, and switch the feature off if nobody will review the inbox.
- **Control Room counts** are made with the viewer's permissions, except the reorder-level count,
  which is site-wide.
- **Email.** The assistant can address an email to anyone the user or a record names. A
  manipulated assistant could draft a message carrying data to an outside address; the card shows
  every recipient and the full text, and nothing is sent without the user's click. Switch email
  drafting off if that review cannot be relied on.
- **Team chat posts** go to an incoming webhook of Slack, Teams or Google Chat only (or hosts the
  server administrator lists in site_config). The schedule's owner chooses the channel; whoever is
  in it sees the summary.
- **Masking is partial by design** (formats with check digits or clear shapes). Treat it as
  reducing exposure, not as a guarantee, and use Sensitive DocTypes for data that must not leave.
- **Knowledge documents** are sent to the provider when relevant, and their text can contain
  instructions aimed at the assistant. Add documents from sources you trust.
- **Other providers.** With an OpenAI-compatible provider or an embeddings service, the same data
  goes to that service instead; a plain-`http` base URL is only appropriate for a server on the
  same machine or private network. The provider and embeddings keys are stored encrypted.
- **Limits are per user.** The per-minute, daily and monthly limits protect against runaway use by
  one account, not against many accounts; watch the usage report.
- **Data sent to the AI provider.** Messages, attachments and query results leave the server.

Report a problem privately to the people who maintain your copy of this app.
