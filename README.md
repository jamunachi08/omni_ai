# Neotec AI for ERPNext and Frappe

Three things in one app, built to run a company on ERPNext with fewer mistakes and less manual work:

1. **Entry checks** stop wrong entries at the moment of saving: duplicates, unusual prices and
   amounts, wrong dates, missing data. Fixed rules, no AI involved.
2. **The Control Room** shows every morning what needs attention in sales, purchasing, accounts,
   stock, projects and HR, and holds what the assistant prepared for your approval.
3. **The assistant** answers questions, analyses data, builds reports, and prepares documents and
   emails that take effect only when a person approves them.

The assistant is a white-label AI assistant that lives inside the ERPNext / Frappe desk. Users chat with it from a side
panel on any screen or from a full page. It answers general questions, analyses the site's data with the
user's own permissions, draws charts, exports spreadsheets, drafts reports that the user can save into
the system's report list, and prepares document changes that run only after the user confirms.

It is powered by the Claude API (your own API key) and carries your name, logo and colour. Nothing in
the interface mentions the AI vendor unless you choose to show it.

- Works on **Frappe / ERPNext v15 and v16**.
- Works on **Frappe Cloud, self-hosted and on-premise** benches. No extra Python or Node packages, no
  asset build step.
- English and Arabic interface, right-to-left layout, light and dark themes, phone layout.

## What users get

| | |
|---|---|
| **Chat anywhere** | Floating button on every desk page, `Ctrl + .` shortcut, "Ask … about this" in every document menu, and a full-page view with chat history. |
| **Knows what you are looking at** | The open document, list or report is offered as context (one click to switch off). |
| **Data answers** | Finds the right DocTypes, reads their fields, queries records, runs existing reports (General Ledger, Stock Balance and so on) and, for permitted roles, read-only SQL for joins. |
| **Tables, charts, files** | Sortable and searchable tables, bar / line / pie charts, Excel and CSV downloads. |
| **Report builder** | "Build me a report of …" produces a previewed draft. **Save report** adds it to the report list and to an "AI Reports" workspace. Nothing is saved without that click. |
| **Guarded actions** | Create, update, submit, cancel or delete business documents, always through a confirmation card and always with the user's own permissions. |
| **Attachments** | Images, PDFs, Excel, CSV, Word and text files can be attached and analysed. |
| **Web search** | Optional, for current information outside the system. |
| **Memory** | Remembers preferences and business rules across conversations. The user accepts each note; company-wide notes also need a System Manager's approval. |
| **Accuracy checks** | Large figures in an answer are traced to the query results; a report draft gets an independent review before it is shown. |
| **Scheduled tasks** | "Email me this every Monday at 8" runs the question on a schedule and emails the result. |
| **Automations** | Starts workflows you register (for example n8n webhooks) after the user confirms. |
| **Knowledge library** | Answers from your own policies, manuals and procedures, and names the document it used. |
| **Key figures** | Summaries open with headline tiles (revenue, orders, overdue…) taken from the queries. |
| **Email drafts** | "Send the customer a reminder" prepares the email on a card; it goes out only when the user presses Send. |
| **Writing help** | A small button on long text fields in any form: fix spelling, translate Arabic ↔ English, improve, make formal, shorten. Always a preview first. |
| **Alerts** | A scheduled task can stay silent unless there is something to report, and can also post to Slack, Microsoft Teams or Google Chat. |
| **Read-only chats** | A lock button makes a conversation look-only: nothing can be proposed or changed from it. |
| **Privacy shield** | Personal data is masked before it leaves your server, and sensitive DocTypes are never sent to an outside AI provider. |

## How the three parts work together

| Area | Mistakes stopped at entry | Watched in the Control Room | Prepared for approval |
|---|---|---|---|
| **Sales** | Selling below cost, price far from the price list, unusually large invoice, wrong dates, duplicate customer | Orders past delivery date, delivered but not invoiced, expired quotations, old drafts | Follow-up emails, invoices and tasks |
| **Purchasing** | Supplier invoice entered twice, price well above the last purchase, unusually large invoice | Orders past their required date, received but not invoiced, pending material requests | Supplier chasers, purchase documents |
| **Accounts** | Payment reference used twice, unusually large payment or journal, dates out of range | Overdue receivables and payables, unallocated payments, unposted drafts | Payment reminders, journal drafts |
| **Stock** | Stock entry dates out of range, duplicate items | Negative stock, items below reorder level, expiring and expired batches | Material requests, transfers |
| **Projects** | Tasks and projects that end before they start | Overdue tasks, projects past their end date, unsubmitted timesheets | Task updates, reminders |
| **HR** (with the HR app) | Employee entered twice, unusual expense claims, leave that ends before it starts | Leave and expense claims waiting, contracts and passports about to expire | Reminders, HR documents |
| **Master data** | Duplicate customers, suppliers, items and tax IDs | Customers and suppliers without a tax ID, stock items without a cost | Corrections |

Nothing in the right-hand column happens by itself: every document and email waits for a person.

## Entry checks

Rules live under **AI Assistant Check Rule** (System Managers). They run on every save and submit,
from the desk, the API, imports and the assistant alike. They are plain rules evaluated by code:
instant, free, and the same entry always gets the same verdict.

| Check | What it catches |
|---|---|
| **Duplicate** | Another document with the same values in the listed fields. Optionally ignoring capitals, spaces and punctuation, so "Al-Noor Trading" and "alnoor trading" are the same customer. |
| **Required** | Fields that must be filled, including fields in a child table (`items.cost_center`). |
| **Condition** | A field must be =, >, <, in a list… compared with a value or with another field (`field:posting_date`). |
| **Date Window** | A date in the future, or more than N days in the past. |
| **Price Deviation** | An item rate above or below the last purchase rate, the cost (valuation rate) or the price list rate by more than a tolerance. Compared per stock unit in company currency. |
| **Unusual Amount** | An amount several times the usual one for that customer, supplier or employee (the median of their last 50 documents; needs at least 5). |

- **When It Fails**: *Warn* shows a message and lets the user continue; *Block* stops the save.
  **Roles That May Continue Anyway** turns a block into a warning for those roles.
- **Run On**: every save (including drafts) or only on submit.
- About 25 starter rules are created at install for the DocTypes your site has. **They all start as
  warnings**, so installing the app cannot stop anyone's work. Turn the ones that matter to you
  into Block, adjust tolerances, delete what you do not want; your changes are kept on upgrade.
- Every hit is recorded in **AI Assistant Check Log**, including entries that were stopped, and
  summarised in the Control Room.
- A rule that cannot run (for example its field was removed) is skipped and logged in the Error
  Log; it never stops people from working.
- Switch everything off with *Check Entries When They Are Saved* in the settings.

Checks reduce mistakes; they do not replace approval workflows, ERPNext's own validations or a
review of the books.

## Control Room

Open it from the search bar ("Control Room") or the assistant page's menu.

- **What needs attention**: about 30 fixed monitors across the areas in the table above. Each shows
  a count and, where it makes sense, an amount; a click opens the same records as a filtered list,
  and **Ask** hands the finding to the assistant. Counts follow the viewer's permissions, so each
  person sees their own picture. HR monitors appear only when the HR app is installed.
- **Waiting for your approval**: every card the assistant prepared for you that you have not
  decided yet, from chats and from scheduled tasks, with Approve and Dismiss.
- **Entry checks, last 7 days** (System Managers): how many entries were questioned or stopped, by
  rule.

The assistant can read the same findings ("what needs my attention today?").

### Automation with approval

A scheduled task marked **May Prepare Actions for Approval** can prepare documents and emails while
nobody is at the screen: "every morning, prepare reminder emails for invoices more than 30 days
overdue", "every Sunday, draft material requests for items below their reorder level". Nothing is
created or sent by the run. Each item waits in the Control Room; the result email lists what is
waiting. Approved documents pass the same entry checks as hand-made ones.

Switch this off for the whole site with *Scheduled Tasks May Prepare Actions for Approval*.

## Calculations, document trail and printing

- **Unusual values, forecasts and shares** are computed by code on query results, not estimated:
  "any unusual purchase prices this quarter?", "forecast next month's sales", "which customers make
  up 80% of revenue?". A forecast is a straight-line trend with a range and is presented as an
  estimate.
- **Document trail**: "where is this order stuck?" follows a document back to what it came from and
  forward to what was made from it (order → delivery → invoice → payment) and names the missing step.
- **Print**: every answer has a print button that opens a clean page with the tiles, tables and
  charts; the browser's print dialog also saves it as PDF.

## Install

### Frappe Cloud

1. Put this folder in a Git repository you control (GitHub, private is fine) and push it.
2. In the Frappe Cloud dashboard open your **bench group → Apps → Add App**, choose the repository and
   branch, then **Deploy**.
3. Open your **site → Apps → Install** and pick **AI Assistant**.

On Frappe Cloud custom apps are added to your own bench group; if **Add App** is not offered, check that your plan includes a private bench.

### Self-hosted or on-premise

```bash
cd frappe-bench
bench get-app /path/to/omni_ai            # or the Git URL of your copy
bench --site your-site install-app omni_ai
bench restart                              # production; on a dev bench restart `bench start`
```

`bench get-app` links the app's assets. If the launcher does not appear, run
`bench build --app omni_ai` once and reload.

### Upgrading from v15 to v16

Nothing to change. The same code runs on both; after the upgrade run `bench migrate` as usual.

## Set up (5 minutes)

1. Search for **AI Assistant Settings**.
2. **Connection**: paste your Claude API key (from <https://console.anthropic.com>), save, then press
   **Test Connection**. It confirms the key and lists the models the key can use.
3. **Branding**: assistant name, logo, accent colour, welcome text and suggested prompts.
4. Give users the **AI Assistant User** role. System Managers have access automatically.
5. Reload the browser. The launcher appears at the bottom corner.

Optional:

- Keep the API key out of the database by putting it in `site_config.json` as `ai_assistant_api_key`.
- **Capabilities** tab: switch individual abilities on or off and choose which roles may use SQL and save
  SQL-based reports (both start as System Manager only).
- **Limits and Logs** tab: messages per minute and per day for each user, a monthly token allowance
  per user, row limits, retention.

## Other AI providers and local models

The **Provider** field on the Connection tab chooses how the assistant talks to the model:

- **Claude** (default): the Claude API, or a gateway that speaks the same format.
- **OpenAI-compatible**: any service with a chat-completions endpoint and tool calling — OpenAI,
  Azure OpenAI, OpenRouter, or a model on your own hardware through Ollama, vLLM or LM Studio. Set
  **Base URL** (for example `http://localhost:11434/v1` for Ollama) and the model name; the API key
  may be left empty for a local server.

Press **Test Connection** after switching. For an OpenAI-compatible service it also checks that the
model can call tools, because without tool calling the assistant cannot read your data.

What differs with an OpenAI-compatible provider: web search is not available, PDF attachments are
sent as extracted text rather than as pages, and the Effort and Prompt Caching settings have no
effect. Everything else (permissions, cards, reports, memory, schedules, checks) works the same.
Answer quality depends on the model: small local models often choose the wrong fields or stop
early. Try your usual questions before rolling one out.

## Privacy shield

On the **Privacy and Knowledge** tab of the settings:

- **Mask Personal Data.** Before text goes to the AI provider, email addresses, phone numbers
  (international numbers written with `+`, and Saudi mobiles), IBANs, payment-card numbers and Saudi
  ID / Iqama numbers are replaced with placeholders such as `[[EMAIL_1]]`. The list of what each
  placeholder stands for stays on your server. The answer, and any lookups the assistant asks for,
  are converted back, so users read real values and "find the customer with this email" still
  works. IBAN, card and ID numbers are recognised by their check digits, so ordinary amounts and
  document numbers are left alone. Masking also applies to writing help, report review, titles and
  the embeddings service.
  **Not masked:** names, addresses, free text, images and PDF pages. It reduces what leaves the
  server; it is not anonymisation.
- **Where the AI Provider Runs** is your statement: an outside service, inside the country, or on
  your own servers.
- **Sensitive DocTypes** (for example Employee, Salary Slip) are never read for an outside
  provider: not by queries, SQL or reports based on them, not even the name of an open record.
  Child tables of a sensitive DocType are covered too. With the provider inside the country or on
  your own servers they are available again, under the user's normal permissions.

A report that reads a sensitive DocType through its own code, but is based on another DocType, is
not caught by this list: switch off *Allow Running Existing Reports* or restrict that report's roles
if this matters.

## Knowledge library

Add documents under **AI Assistant Knowledge** (System Managers): attach a PDF, Word (.docx), Excel,
CSV or text file, or paste the text. On save the text is read and cut into passages; the Status
field shows how many. The assistant searches the passages for policy and procedure questions and
names the document it answers from.

- **Only for Roles** limits a document to users with one of those roles. The library cannot be
  read any other way from the chat.
- Search is by shared words, in English and Arabic. If *Recall by meaning* is set up for memory,
  passages are also matched by meaning.
- Scanned PDFs have no text to read: paste the text instead. One document holds up to about
  400,000 characters.
- What you add is sent to the AI provider when it is relevant to a question. Do not add documents
  that must not leave your organisation unless the provider runs on your own servers.

## Writing help, email and read-only chats

- **Writing help.** Every editable long text field in a form gets a small ✦ button with six
  actions. The user sees the original beside the suggestion, can edit it (plain fields), and
  nothing changes until they press *Use this text* and save. Formatting in rich-text fields is
  kept. If a number in the suggestion differs from the original, a warning says which.
  Switch it off with *Writing Help on Form Fields*.
- **Email.** The assistant can prepare an email (to at most 10 addresses). The card shows
  recipients, subject and the full text; *Send email* sends it as the user through the site's
  outgoing email account and, when it belongs to a document, files it in that document's
  timeline. Switch it off with *Allow Drafting Emails*.
- **Read-only chats.** The lock button in the chat header switches a conversation to read-only:
  the assistant can look things up and explain, but has no way to propose a change, a report, a
  note, a schedule, an email or an automation.
- **Never Change These DocTypes** (Capabilities tab) lists DocTypes the assistant must not propose
  changes for, in addition to the system DocTypes that are always excluded.

## Limits and outages

- **Messages per minute** (default 10) and **Daily message limit** stop one user from flooding the
  provider. **Monthly token budget** caps each user's use per calendar month; 0 means no cap. The
  *AI Assistant Usage* report shows who used what.
- If the AI provider fails three times within two minutes, the assistant stops calling it for one
  minute and tells users so, instead of making every user wait for a timeout. The same applies to
  each automation webhook separately. A wrong key or a rejected request does not trigger the pause.

## Memory

When a user states a lasting preference, rule or correction, the assistant offers a note on a card
with the exact text. Nothing is remembered silently.

- **Personal** notes are saved when the user accepts them and apply only to that user.
- **Company** notes apply to everyone, so after the user accepts one it waits as *Proposed* until a
  System Manager approves it in **AI Assistant Memory** (the list is the approval inbox).
- With each message the most relevant approved notes are passed to the assistant as context. They
  never override permissions or the confirmation steps.
- By default "relevant" means the note shares words with the question. To match by meaning instead
  ("turnover" finds a note about "revenue"), switch on **Recall by meaning** on the Memory tab and
  enter an embeddings service (any OpenAI-compatible `/embeddings` endpoint, including a local one)
  and model. Note text and each question are then sent to that service. If it cannot be reached,
  recall falls back to shared words.
- Notes that look like passwords or keys are refused. Users can delete their own notes; System
  Managers can edit, reject or delete any note.

## Accuracy checks

- **Figure check.** After a data answer, every large number in the text is looked up in what the
  queries returned (rows, column totals, row counts, and what the user wrote). If a number cannot be
  found, the assistant is asked once to recompute it with a query; the earlier text is folded away as
  a draft. If it still cannot be traced, the answer carries a visible warning listing the figures.
  This is a safety net, not a proof: a wrong number that equals some other figure in the results
  passes, and a correct derived figure (a ratio, for example) can be flagged.
- **Report review.** Before a SQL report draft is shown, a separate AI request tries to find logic
  errors (double counting through joins, missing status or period filters). Its notes appear on the
  draft and go back to the assistant, which can redraft; the older draft is then withdrawn. The
  review costs one extra request per draft and never blocks saving.

Both can be switched off under **Memory and Automation** in the settings.

## Scheduled tasks

Ask in the chat ("send me the overdue invoices every Sunday at 9") or create an **AI Assistant
Schedule** record. Each run happens as the task's owner, with their permissions, and the answer is
emailed with its tables and any exported files; charts are replaced by a link to the conversation.

- Needs the Frappe scheduler and an outgoing email account. If email is not set up the task shows
  the reason in **Last Result**.
- Runs start on the hour (site time zone).
- Unattended runs cannot propose changes, reports, notes or automations: nobody is there to confirm.
- Users can send results only to their own address; System Managers can add other recipients.
- A disabled user's tasks stop running.

### Alerts and team chat

- **Send Only When There Is Something to Report** turns a task into an alert ("warn me when any
  invoice is more than 60 days overdue"). It still runs on its schedule, but when the assistant
  finds nothing, nobody is notified and *Last Result* shows "Nothing to report". Users can ask for
  this in the chat.
- **Also Post To** sends a short text version to a Slack, Microsoft Teams or Google Chat channel
  through that channel's incoming webhook, with a link to the full result. Only addresses of the
  chosen service are accepted. To allow another host (for example a self-hosted Mattermost, which
  accepts the Slack format) add it to `site_config.json`:
  `"ai_assistant_chat_webhook_hosts": ["chat.example.com"]`.
  Anyone in that channel sees the summary, so use a channel whose members may see the data.

## Automations (n8n and other webhooks)

1. In n8n create a workflow that starts with a **Webhook** node (POST) and copy its Production URL.
   In the node, require a header (Header Auth) so only this site can call it.
2. In ERPNext create an **AI Assistant Automation**: a name, what it does (the assistant reads this
   to decide when to offer it), the URL, the secret header name and value, the parameters, and the
   roles allowed to run it. Use **Send Test Request** to check it.
3. Users with one of those roles can now ask for it in the chat. They get a card with the parameters
   and nothing is sent until they press **Run**.

The request body is JSON:

```json
{"automation": "Send WhatsApp", "parameters": {"phone": "+9665…", "text": "…"},
 "requested_by": "user@example.com", "site": "erp.example.com", "test": false}
```

The URL and the secret never reach the AI model or the browser. A 2xx reply counts as success; the
first 500 characters of the reply are shown to the user.

## How permissions and safety work

- **Reads** go through Frappe's permission engine as the logged-in user: role permissions, User
  Permissions (row level) and field-level permissions all apply. If the user cannot see it in the desk,
  the assistant cannot see it either.
- **Read-only SQL** is the one path without row-level permissions, so it is restricted to roles you
  choose and guarded: a single `SELECT`, no comments or backslashes, no write or file keywords, a row
  cap and a time limit. Only backtick-quoted DocType tables the user may read can be named; any other
  name that matches a table or schema in the database is rejected wherever it appears. See
  `SECURITY.md` for the review that led to this.
- **Writes** never happen from the model directly. The assistant proposes; the user sees exactly what
  will change and presses Confirm; the change runs through the normal document API as that user, with
  all validations. System and configuration DocTypes (Core, Custom, Integrations, Workflow, Printing,
  Website, Email modules) cannot be changed through the assistant.
- **Reports**: Report Builder reports are saved views that follow normal permissions; any assistant user
  may save them. Query (SQL) reports can be saved only by roles you allow, and the SQL passes the same
  guard again at save time. Filter scripts are generated by the app from validated data, never written
  by the model.
- **Credentials**: field names that hold passwords, keys or tokens are never read by any tool, are
  removed from SQL results (including `SELECT *`), and DocTypes that hold integration secrets are
  hidden from the assistant.
- **Audit**: every data access is recorded in **AI Assistant Tool Log** (user, tool, arguments, rows,
  duration). **AI Assistant Usage** shows messages and tokens per user.
- **The API key** is stored encrypted and never sent to the browser.

### What leaves your server

Messages, attached files and the query results the assistant reads are sent to the AI provider you
configure (by default the Claude API) in order to produce answers. With Recall by meaning on, memory
notes and questions are also sent to the embeddings service you configure. Review your provider's data terms
and your own data policies before enabling the assistant for sensitive data, and use the Capabilities
and role settings to limit what it can reach.

## How report creation works

1. The user asks for a report in plain language.
2. The assistant inspects the DocTypes, tests the logic on real data, and drafts the report.
3. A card shows the preview rows, the filters and (for SQL reports) the SQL.
4. **Save report** creates a normal Frappe `Report` record owned by the user. It opens in the standard
   report view, appears in the report list for its DocType and module, and is added as a shortcut to
   the **AI Reports** workspace (name configurable).
5. To change it, ask for the change: a new draft is prepared.

In a Query Report every filter is mandatory (that is how Frappe formats report SQL), so the assistant
gives each filter a default such as "start of this year" or `%` for "any text".

## Running without background workers or websockets

- Replies stream over Frappe's realtime channel and fall back automatically to polling, so the
  assistant works where websockets are blocked.
- Replies are produced by a background worker (`long` queue). On a site without workers set
  **Execution Mode** to **Inline**.

## Troubleshooting

| Symptom | Check |
|---|---|
| No launcher button | The user has the *AI Assistant User* role; the assistant is enabled; `bench build --app omni_ai`; hard reload. |
| "The assistant is not connected yet" | API key missing. Add it and press Test Connection. |
| "Waiting for a background worker…" | Workers are not running (`bench doctor`), or switch Execution Mode to Inline. |
| Reply appears in bursts, not smoothly | Realtime (socket.io) is not reachable; polling is being used. Functional, just less smooth. |
| Provider rejects the request | A gateway that does not accept `cache_control`: switch off Prompt Caching. Wrong model name: use Test Connection to list models. |
| "Requests are paused for N more seconds" | The provider failed repeatedly; the assistant retries by itself after a minute. Check the provider's status and the Error Log. |
| "You are sending messages too quickly" / "used this month's AI allowance" | The limits on the Limits and Logs tab. |
| A local model answers but never reads data | The model does not support tool calling. Test Connection reports this; choose a model that does. |
| A report cannot be saved | Query Reports need a role listed under *Roles Allowed to Save Query Reports*. |

Errors are written to **Error Log** with titles starting "AI Assistant".

## For developers

```
omni_ai/
  api/chat.py            whitelisted endpoints used by the interface
  engine/client.py       streaming client for the Messages API (requests only)
  engine/openai_client.py  the same interface for OpenAI-compatible services; embeddings
  engine/guard.py        per-minute limit, monthly budget, circuit breaker
  engine/shield.py       personal-data masking (plain Python, no Frappe import)
  engine/delivery.py     posting to Slack, Teams and Google Chat
  engine/simple.py       omni_ai.ask() for other apps
  checks/                entry-check engine and starter rules (no AI)
  control/               Control Room monitors (no AI)
  api/control.py         Control Room page data
  api/writing.py         writing help for form fields
  engine/agent.py        the reply loop: history, model call, tools, persistence
  engine/stream.py       run events (Redis list + realtime), stop flag
  engine/prompt.py       system prompt
  engine/files.py        attachments to content blocks
  engine/grounding.py    traces figures in an answer to query results
  engine/review.py       independent review of report drafts
  engine/schedule.py     scheduled tasks: due check, run, email
  tools/                 data, reports, sql (guard), display, actions, memory, schedule,
                         automation, knowledge, email, analysis, trail, workspace
  ai_assistant/          DocTypes, the full-page view, the usage report
  public/js/omni_ai.js   the whole interface, one plain script
  public/css/omni_ai.css
  translations/ar.csv
  tests/test_omni_ai.py  offline tests (provider replaced by a fake)
```

Run the tests: `bench --site your-site run-tests --app omni_ai`

Open the assistant from your own scripts:

```js
omni_ai.open();                         // open the panel
omni_ai.ask("Summarise this customer"); // open and send
```

Add a tool: create a `Tool(name, description, input_schema, handler)` in a module under `tools/` and
add it to `all_tools()` in `tools/__init__.py`. Handlers receive `(args, ctx)` and return a dict; raise
`ToolError` for problems the model can correct.

### Using the AI from your own apps

```python
import omni_ai

summary = omni_ai.ask("Summarise this complaint in one sentence: " + text, system="Reply in Arabic.")
```

`ask()` uses the provider, key, personal-data masking, monthly budget and outage pause from the
settings. It has no tools, so it cannot read or change anything in the system. It raises
`frappe.ValidationError` when the assistant is not set up or the user's budget is used up.

## Known limits

- Query Report filters are always mandatory (see above). Script Reports are not generated.
- The assistant cannot edit a saved report in place; it drafts a replacement.
- Charts show up to 60 points and tables up to 300 rows on screen; exports carry up to 50,000 rows.
- Memory holds short notes, not documents. Recall by meaning compares at most the 400 most recent
  notes a user can see.
- The per-minute limit uses fixed one-minute windows, so a short burst across two windows can
  briefly exceed it. The monthly budget is checked before a reply starts; the reply that crosses
  it is allowed to finish.
- Entry checks compare with what is in the system: a price check needs a last purchase rate or a
  cost on the item, an amount check needs five earlier documents. With no history they stay silent.
- The duplicate check that ignores spelling details finds "Al-Noor Trading" / "alnoor trading" but
  not a name written as one word against the same name written as three.
- Control Room monitors are fixed in code; they cannot be added or edited from the desk yet. Use a
  scheduled alert for a condition of your own.
- "Items below their reorder level" counts all such items and opens the standard reorder report; it
  is not limited to the warehouses a user may see.
- A forecast is a straight line through the history. It knows nothing about seasons or events.
- Masking recognises formats, not meaning: names and free text pass through, and a phone number
  written without `+` in a non-Saudi format is not recognised. A 10-digit number that happens to
  pass the ID check digit (about one in ten) is masked; the user still sees it correctly, but the
  assistant cannot calculate with it.
- Writing help does not count towards the monthly token budget (it does count towards the
  per-minute limit). It handles up to 12,000 characters per field and is not offered inside
  child-table rows.
- The Teams message format follows Microsoft's Workflows webhook; older Office 365 connectors may
  show it differently.
- Outside AI agents cannot connect to this app directly. That is the separate **ERP MCP Server**
  app, which builds on this one.
- Scheduled tasks run hourly at the earliest and email text and tables, not chart images.
- There is no phone or voice channel.

## Licence

MIT
