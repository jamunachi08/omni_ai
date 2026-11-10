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
- **Limits and Logs** tab: daily message limit per user, row limits, retention.

## How permissions and safety work

- **Reads** go through Frappe's permission engine as the logged-in user: role permissions, User
  Permissions (row level) and field-level permissions all apply. If the user cannot see it in the desk,
  the assistant cannot see it either.
- **Read-only SQL** is the one path without row-level permissions, so it is restricted to roles you
  choose and guarded: a single `SELECT`, no comments, no write or file keywords, only DocType tables
  the user may read, credentials tables and columns blocked, a row cap and a time limit.
- **Writes** never happen from the model directly. The assistant proposes; the user sees exactly what
  will change and presses Confirm; the change runs through the normal document API as that user, with
  all validations. System and configuration DocTypes (Core, Custom, Integrations, Workflow, Printing,
  Website, Email modules) cannot be changed through the assistant.
- **Reports**: Report Builder reports are saved views that follow normal permissions; any assistant user
  may save them. Query (SQL) reports can be saved only by roles you allow, and the SQL passes the same
  guard again at save time. Filter scripts are generated by the app from validated data, never written
  by the model.
- **Audit**: every data access is recorded in **AI Assistant Tool Log** (user, tool, arguments, rows,
  duration). **AI Assistant Usage** shows messages and tokens per user.
- **The API key** is stored encrypted and never sent to the browser.

### What leaves your server

Messages, attached files and the query results the assistant reads are sent to the AI provider you
configure (by default the Claude API) in order to produce answers. Review your provider's data terms
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
| A report cannot be saved | Query Reports need a role listed under *Roles Allowed to Save Query Reports*. |

Errors are written to **Error Log** with titles starting "AI Assistant".

## For developers

```
omni_ai/
  api/chat.py            whitelisted endpoints used by the interface
  engine/client.py       streaming client for the Messages API (requests only)
  engine/agent.py        the reply loop: history, model call, tools, persistence
  engine/stream.py       run events (Redis list + realtime), stop flag
  engine/prompt.py       system prompt
  engine/files.py        attachments to content blocks
  tools/                 data, reports, sql (guard), display, actions, workspace
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
- Conversation memory is per conversation. There is no memory across conversations.
- Scheduled or automatic runs (for example a weekly emailed summary) are not included.

## Licence

MIT
