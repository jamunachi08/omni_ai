# AI Assistant for ERPNext and Frappe

A white-label AI assistant that lives inside the ERPNext / Frappe desk. Users chat with it from a side
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
  engine/agent.py        the reply loop: history, model call, tools, persistence
  engine/stream.py       run events (Redis list + realtime), stop flag
  engine/prompt.py       system prompt
  engine/files.py        attachments to content blocks
  engine/grounding.py    traces figures in an answer to query results
  engine/review.py       independent review of report drafts
  engine/schedule.py     scheduled tasks: due check, run, email
  tools/                 data, reports, sql (guard), display, actions, memory, schedule,
                         automation, workspace
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

## Known limits

- Query Report filters are always mandatory (see above). Script Reports are not generated.
- The assistant cannot edit a saved report in place; it drafts a replacement.
- Charts show up to 60 points and tables up to 300 rows on screen; exports carry up to 50,000 rows.
- Memory holds short notes, not documents. Recall by meaning compares at most the 400 most recent
  notes a user can see.
- The per-minute limit uses fixed one-minute windows, so a short burst across two windows can
  briefly exceed it. The monthly budget is checked before a reply starts; the reply that crosses
  it is allowed to finish.
- Outside AI agents cannot connect to this app directly. That is the separate **ERP MCP Server**
  app, which builds on this one.
- Scheduled tasks run hourly at the earliest and email text and tables, not chart images.
- There is no phone or voice channel.

## Licence

MIT
