# Z-Matrix Platform Blueprint (text copy)

> Source of truth with diagrams: https://claude.ai/artifact/5wzWQ8tFq6bTyjENpmLJe3
> Text extraction for agents; the text inside the diagrams is under "Diagram notes" at the end. Section numbers (§01–§18) and task ids (S1-xx, S2-xx) match the artifact.
> **POC scope overrides** live in `platform/AGENTS.md` → "POC scope" and the scope note: one pilot client provisioned by the worker — Supabase platform project + one client project (2 projects), one Operaton on Railway.
> **Repos:** the blueprint names the prototype `Adityashandilya555/operaton-plat`; we use its fork **`Shrey2149/outpost`** as the prototype reference, and all new code + issues live in **`Adityashandilya555/retail_expansion_platform`** (our Operaton fork, code under `platform/`).

## Decisions already made
- **Isolation:** Each client gets its own Operaton server and its own Supabase project.
- **Hierarchy:** Department level ladders, defined by each client.
- **App shape:** One shell. Department apps live inside it.
- **Agent:** Admin configurator first. Form pre-fill later.
- **Onboarding:** Sign up, we approve, the workspace builds itself.
- **Live sites:** Admin chooses stay or migrate at every publish.
- **Hosting:** Supabase now. AWS-ready: the database stores keys, not URLs.

# Part 1
How, what, why
Fourteen short sections. Each one starts with a picture.

## № 01 · Why
Every rule lives in code today
Matrix-bd works, but it gives every client the same flow, the same three levels and the same screens.
Left: what Matrix-bd hard-codes today. Right: the same ideas as data, different for each client. || means two departments work in parallel.
About 18 kinds of rules are written in code, from the BD state machine to Blue Tokai's (rent+CAM)×1.18.
Every client gets the same flow, the same three levels and the same screens.
A change for one client means a code release for all of them.
Operaton's own screens (Tasklist, Cockpit) are generic, built on AngularJS, and look the same for every client on a server.
The goal: each client's flow, levels, forms and screens become data their admin owns.

## № 02 · What
Four parts, one product
A multi-client platform where each client's site lifecycle runs on Operaton, looks like Z-Matrix, and is configured by the client's own admin with an agent.
Engine
Operaton engines
Decides what happens next and who must do it. Each client gets its own Operaton server, from the official image.
reads and writes: the client's own database
Door
Platform API
The only way in. Checks who you are, which client you belong to, and what your level allows.
talks to: engines, client databases, Claude
Face
Z-Matrix shell
Every screen people see. Tokens, components, blocks and layouts, installed once and arranged per client.
talks to: the Platform API only
Helper
Admin agent
Turns a conversation into configuration: departments, levels, steps, forms, pages, KPIs. Never code.
edits: the client's draft, nothing else
Platform adminapproves workspaces, watches health
Business adminowns the flow, people and KPIs
Department L1…Lndoes and approves tasks
Observerreads everything, changes nothing

## № 03 · How Operaton works
How Operaton handles workflows and forms
Operaton has no config.json or workflow.json. Those come from our prototype. Operaton reads BPMN, DMN and form files, nothing else.
The prototype (github.com/Adityashandilya555/operaton-plat; we use its fork Shrey2149/outpost) writes JSON. Its compiler turns that JSON into the three file types Operaton understands and sends them in one request.
What the compiler emits · BPMN
<userTask id="lg_licences"
name="Statutory licences"
operaton:candidateGroups="legal__L1"
operaton:formKey="embedded:deployment:forms/lg_licences.html" />
What Tasklist renders today · embedded form
<input type="text"
cam-variable-name="fssaiNo"
cam-variable-type="String" required>
<input type="file"
cam-variable-name="fssaiCopy" cam-variable-type="File">
Operaton reads three kinds of files: BPMN (the flow), DMN (decision tables) and forms.
They arrive together in one deployment. Each deployment is a frozen version: v1, v2, v3.
A user task points at its form with formKey or formRef. Operaton finds that form inside the same deployment.
That is why clients can already have different forms: each client's forms ship inside that client's deployment.
workspace.json and catalogue.json are our language. matrix.py translates them.
In the new platform the task carries formKey="zm:lg_licences". Our shell looks the form up in the client's release and draws it with Z-Matrix fields.

## № 04 · Operaton's own UI
Can Operaton's screens be changed per client?
Operaton has five ways to change its screens. All five were checked in its source code. Only one works per client.
Left: Operaton's apps share one configuration per server. Right: our shell reads the UI config stored in each client's release.
Mechanism | Where it lives | What it can change | Per client? | On the fly? |
config.js (Tasklist, Cockpit, Admin) | app/tasklist/scripts/config.js | App name, locales, extra scripts via customScripts | No, one per server | No, a file on the server |
Plugin JARs (AppPlugin) | JAR on the server's classpath | Panels at plugin points like tasklist.task.detail, cockpit.dashboard (AngularJS) | No | No, needs a restart |
user-styles.css | app/*/styles/user-styles.css | Colours, logo, spacing | No, one theme | No |
webapps-neo plugins | plugins.json read once at boot (VITE_PLUGINS_URL) | A new page, a dashboard widget, an API namespace. The task screen is closed to plugins. | No, server-wide | At next page load, full privileges |
Forms in a deployment | BPMN + form files via POST /deployment/create | Only the form panel of a task | Yes | Yes, a new release |
Answer
No Operaton endpoint installs a design for one client. Plugins and styles are installed once per server. The only thing Operaton ships per client on the fly is a deployment. We copy that idea: each client's screens are described inside its release, and our own shell draws them.
Classic Tasklist and Cockpit customisation is server-wide: one config.js, one CSS file, one plugin set for everyone.
Plugins are code with full privileges: AngularJS for the classic apps, Preact for webapps-neo.
webapps-neo deliberately keeps the task screen closed to plugins, so a client's task layout can't be changed there.
Each client's Operaton runs headless, REST only. Its own screens stay off for clients; only our operations team can use them.

## № 05 · The core idea
How every user gets their own interface
The design packages are built once and pre-installed. The agent only arranges them for each client. The system then filters them for each person.
Layer 1 is our code. Layer 2 is each client's data. Layer 3 is computed per login.
Your question, answered
Yes. The packages are already installed in the shell. The agent only picks and arranges them for each client, within what each block's manifest allows. No client ever gets its own code or CSS.
What the agent writes · Burger King release v3
"legal": {
"nav": { "label": "Legal",
"visible_to": ["legal.*", "admin"] },
"pages": [{
"key": "overview",
"template": "department",       1
"regions": {
"hero": [                         2
{ "block": "kpi_tile",
"props": { "kpi": "legal_avg_days" } },
{ "block": "kpi_tile",
"props": { "kpi": "licences_pending" } } ],
"main": [
{ "block": "task_inbox",         3
"props": { "scope": "team" } },
{ "block": "site_table",         4
"props": { "columns": ["code", "city", "due"] } } ]
} }] }
What the shell draws from it
Z-MatrixBurger King
HomeBDLegalDesignBudget

## № 03 · Legal & Statutory
Legal overview
1
12.4
avg days in Legal
7
licences pending
2
Team queue
BK-0042Statutory licencesL2 · due 2d
BK-0039Lease draftoverdue
BK-0051RERA checkdue 5d
3
Sites · code · city · due
BK-0042Bengaluru9 Oct
BK-0039Pune4 Oct
4
Same Legal app, three people
Legal executive
legal.L1 · scope: own sites
Sees
My tasks
Licence and lease forms
Sites assigned to them
Hidden
Rent and budget sections
Approvals, Studio
Head of Legal
legal.L3 · scope: department
Sees
Team queue and L3 approvals
Legal KPIs
Every Legal site
Hidden
Studio and agent
Other departments' approvals
Business admin
client admin · scope: all
Sees
Every department app
All approvals and KPIs
Agent dock, Preview, Publish
Hidden
Other clients, always
Installed once: we build tokens, components, blocks and layouts from the Z-Matrix handoff files. They ship inside the shell.
Per client: the arrangement is configuration stored in that client's release, written by the admin through the agent.
Per user: the API filters the release by department, level and scope before the shell draws anything.
The agent sees manifests (name, description, allowed props), never React code or CSS.
Something new needed, like a site map? We build one block once. After the next platform release, every client's agent can place it.

## № 06 · Architecture
The system in one picture
One API in front. Behind it, every client has its own Operaton server and its own Supabase project.
The API keeps one table: client → Operaton address, database and bucket. Each Operaton connects only to its own client's operaton schema.
The browser only talks to the Platform API. It never reaches Operaton or Supabase.
Users log in once, to the platform. They never sign in to Operaton or see its screens.
Each client's Operaton is private: REST only, behind Operaton's built-in login. Only the API and the worker hold its service account.
No Java to write: every instance is the official operaton/operaton image, set up with environment variables, as in the prototype.
Every request carries one client id from the login token: one Operaton, one database pool, one bucket.
Past a few dozen clients, small clients can share one Operaton. The client → address table makes that a data change, not a rewrite.

## № 07 · Isolation
Each client in its own vault, with a clean exit to AWS
Each client's vault is its own Supabase project plus its own Operaton server. Nothing is shared between vaults.
The exit works because the database stores file keys like sites/BK-0042/lg_licences/fire_noc/…, never full Supabase URLs.
One Supabase project and one Operaton server per client: separate data, separate compute, separate backups.
Operaton upgrades go one client at a time, so a new version can be tried on one client first.
Two schemas per client: operaton for engine tables and app for form data, documents, decisions and users.
Browsers never reach Supabase: the Data API is off and anonymous users have no grants.
Connections use Supabase's session pooler on port 5432, because Operaton needs prepared statements.
Platform admins see health and status, never a client's business data.

## № 08 · Hierarchy
Departments, levels and approvals
Each client names its departments and decides how many levels each one has. A department hands the site on only after its sign-off chain approves.
A three-level Legal department. Any approver can approve, reject or send back; only the main paths are drawn.
Each client defines its departments and their levels, with their own names.
A task is done by a level, by the person who created the site, or by someone picked in an earlier form.
Approvals walk up an ordered chain of levels. Each approver can approve, reject or send back.
or_above lets higher levels do lower-level work, like Matrix-bd's supervisor access.
Visibility per membership: own sites, department, or all. Sensitive form sections can be hidden from lower levels.
Moving people between levels is instant. Changing the ladder itself is a publish.

## № 09 · Forms, files, approvals
Forms, files and approve or reject
Forms are built from a fixed set of Z-Matrix fields on a 12-column grid. Any step can ask for any file type through a document slot.
Field catalogue · each maps to one Z-Matrix input
texttextareanumbercurrency ₹percentdatedate rangeselectmulti-selectyes / norating 1–5user pickeraddress + map pinchecklistline itemscomputeddocument slotinfo note
Statutory licences · formtask lg_licences
Licence details
FSSAI numbertext · required
Valid untildate
Licenceschecklist · FSSAI · Fire NOC · Shop & Est. · Health trade · Signage
FSSAI copyslot · pdf, jpg · ≤ 25 MB · required
Fire NOCslot · pdf · required · keep versions
Commercialshidden for L1
Licence feescurrency ₹
Total incl. GSTcomputed · fees × 1.18
Document slot rule
{ "slot": "fire_noc",
"accept": ["pdf"],
"max_mb": 25,
"required": true,
"keep_versions": true }
// stored as a key + sha256, never a URL:
// sites/BK-0042/lg_licences/fire_noc/7f3a-noc.pdf
Approval bar
Approve
Reject ▾
Send back ▾
L2 approved · L3 pending
The agent only chooses fields and places them on the grid. It can't invent a field type.
Each form compiles to one JSON Schema, checked in the browser and again by the API.
"Any file at any step" means document slots with their own rules: types, size, required, versions.
Files go straight into the client's private bucket. The database keeps the key and a checksum.
Matrix-bd's 9 DD items and 5 licences become a checklist. Its 11 budget lines become line items.

## № 10 · The agent
The admin's agent edits a draft, a person publishes
Version 1 is a configurator for business admins on the landing page. It builds the first flow during onboarding and makes changes later.
The dashed box is the agent's whole world: the draft. Everything that changes production sits outside it.
Onboarding conversation · first login after approval
Pick a template→
Departments and levels→
Steps and approvals→
Documents and SLAs→
Pages and KPIs→
Preview→
Publish v1
LangChain, LangGraph and the rest
Piece | What it is | In v1? | Why |
Anthropic Python SDK | Official client. Runs the call → tools → repeat loop. | Yes | Least code, and full access to strict tools, caching, effort and fallbacks. |
LangChain | Wrapper library for models, prompts, tools, retrievers | No | Our tools are plain typed functions. Wrappers lag new Claude API features. |
LangGraph | Agent graphs with checkpoints and pause / resume | Later | Useful when agents work inside flows and must wait days for a person: pre-fill, AI review steps. |
LangSmith | Hosted tracing and evaluation | Dev only | Optional. Client data stays in our databases. |
MCP | Standard way to expose tools to Claude apps | Optional | The same tools for our own team, like the prototype's mcp_server.py. |
The agent's tools · all draft-only, all strict JSON schemas
Readget_overview · get_task · get_form · list_blocks · get_block_manifest · list_field_types
Orgadd_department · update_department · set_levels
Flowadd_stage · set_after · add_task · move_task · set_approval_chain · add_document_slot · set_sla
Formsadd_field · update_field · remove_field · arrange_form
UIset_page · place_block · update_block · remove_block · set_nav · set_accent
KPIs and checksadd_kpi · update_kpi · validate_draft · diff_vs_live · preview_link
Model: claude-opus-5-5. Effort high for onboarding, medium for small edits.
Prompt caching on the tool list and the system prompt (manifests, field catalogue, templates), which only change with platform releases.
Strict tool schemas with automatic tool choice. Append-only history in the client's own database.
The client is fixed by the server. Every change is a logged JSON patch with undo.
The agent sees configuration and head-counts, not site data, so no client personal data reaches the model in v1.

## № 11 · Live changes
Changing a flow while sites are running
Every publish is a new frozen version. The admin decides what happens to sites already in progress.
Operaton builds and checks the migration plan first (/migration/generate, /migration/validate). Nothing moves until the admin confirms.
New sites always start on the newest release.
By default, running sites finish on the version they started with.
"Also move running sites" shows an impact screen first: who moves cleanly, whose step was removed, which tasks are open.
Rollback means publishing the old configuration again as a new version.

## № 12 · KPIs
KPIs inside each vault, benchmarks outside
KPIs are computed inside each client's database. A read-only relay copies cleaned facts out, for platform-wide benchmarks.
The agent can add a KPI tile by choosing a definition type and two milestones. It never writes SQL.
Default KPI pack in every template
Sites by stageDays LOI → launchTime per departmentApproval turnaround by levelSLA breachesSend-back rateBudget variance · GFC vs actual

## № 13 · Tech stack
What it is built with
Layer | Choice | Why |
Web shell | React 18 · TypeScript · Vite · React Router · TanStack Query | The Z-Matrix kits are React; typed manifests and props |
Look | Z-Matrix tokens as CSS variables · Lucide icons · no Tailwind | Matches the shipped design kit exactly |
Flow map, charts | @xyflow/react · Recharts | Small and well known |
Forms | Own renderer · JSON Schema (Ajv, jsonschema) · JsonLogic rules | Exact Z-Matrix fields; the same rules in browser and API |
API and worker | Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2 async · Alembic · httpx | Same stack as Matrix-bd; the prototype compiler is Python |
Workflow engine | Official operaton/operaton image · one container per client · REST only · built-in login | No Java to write; each client upgrades on its own |
Data | Supabase: one project per client + one platform project | Your isolation decision |
Files | Supabase Storage, private bucket per client · keys only | Copy to S3 later without code changes |
Agent | Anthropic Python SDK · claude-opus-5-5 · strict tools | Least code; current API features |
Auth | Own JWT: workspace code + email + password · SSO later | Users stay inside each client's database |
Email | Resend, sent by the worker | Already used by Matrix-bd |
Hosting | Vercel for the web · containers for the API, the worker and one Operaton per client, in the Supabase region | Low latency to client databases |
Dev and CI | Docker Compose + Supabase CLI · throwaway Postgres in CI | Local works like production |
Observability | Sentry · JSON logs tagged by client | Find one client's problem fast |

## № 14 · From Matrix-bd
Where each hard-coded rule goes
Matrix-bd today (code) | On the platform (configuration) |
ALLOWED_TRANSITIONS state machine, copied into the frontend | Tasks and outcomes in the flow, compiled to BPMN |
Design gate: legal DD positive and finance approved | after: [legal, finance] plus a gate condition |
Executive → supervisor → admin chains in every service | approval.chain using the client's own levels |
Module lists in 4 backend lists, 5 DB checks, 12 frontend registries | One org.departments list plus apps |
9 DD items and 5 licences as fixed columns; automatic "legal approved" | Checklist fields plus a gate: all yes or n/a |
11 fixed budget lines; closure seeded from GFC | Line-item field with template rows plus seed_from |
(rent+cam)*1.18, BT-CITY-XXXX, competitor fields | Computed field · site-code pattern · template fields |
25 MB cap and MIME allowlist | Document-slot rules per step |
Hard-coded notification recipients | Notification rules: event → level, role or creator → channel |
Near-duplicate Overview and Queue pages | One department template with blocks |
Launch staging loop: exec → supervisor → admin | Approval chain with editable sections per level |
Legal change-request recovery | send_back_to plus a change-request task |

# Part 2
Developer task list
Two 14-day sprints. Each ends with a demo that proves the sprint goal.
Plan · Team and timeline
28 days, two demos
The plan assumes the team below, about 65 person-days of capacity per sprint. Each sprint plans about 50, leaving room for reviews, bugs and meetings.
2
Frontend · React + TS
2
Backend · Python
1
Platform · Java + DevOps
1
QA engineer
½
Designer
1
PM
Show tasks for
Everyone
Frontend
Backend
Platform
QA
Design
PM
Sprint 1 · Days 1–14
Foundation
Sprint goal
Two clients run on their own Operaton servers, and an L1 user completes a real task in the Z-Matrix shell.
ID | Task | Owner | Est. (pd) | Depends on | Done when |
- S1-01 | Monorepo, CI and Docker Compose dev stack | PLAT | 1.5 | — | docker compose up starts the API, the worker and one local Operaton per test client; CI runs lint and tests on every PR. |
- S1-02 | Operaton per client: container template from the official operaton/operaton image (REST only, built-in login, full history kept, the client's database URL), private network, per-client service account, health check | PLAT | 2 | S1-01, S1-03 | BK and Starbucks each run their own Operaton; each reaches only its own database; calls without the service account get 401. |
- S1-03 | Supabase setup script: platform project + 2 client projects; app and operaton schemas, roles, Data API off, private bucket, Operaton SQL scripts | PLATBE | 2 | — | The script rebuilds all three projects from zero; the anon key can't read any table. |
- S1-04 | Control-plane schema (clients, requests, admins, templates) and encrypted client secrets | BE | 1.5 | S1-03 | BK and Starbucks rows exist; secrets are unreadable without the env key. |
- S1-05 | API skeleton: client-context resolver, engine REST client, error model, OpenAPI → TypeScript SDK | BE | 3 | S1-02, S1-04 | A BK token can only reach BK's pool, Operaton and bucket (unit-tested). |
- S1-06 | Auth: workspace-code login, JWT access + refresh, users and memberships (department, level, scope) | BE | 2 | S1-05 | Seeded L1, L2, L3 users log in per client; a wrong workspace code fails. |
- S1-07 | Workspace definition JSON Schema v1 (org, flow, forms, apps, kpis) + Pydantic models | BE | 2 | — | Both templates validate; 20 broken samples fail with a path and a message. |
- S1-08 | Compiler port from matrix.py: levels → candidate groups, approval chains, send-back and reject routes, formKey pointer, deploy to the client's Operaton, releases table | BE | 4 | S1-02, S1-07 | Publishing the BK template creates release v1 in BK's Operaton only. |
- S1-09 | Templates: QSR launch (Burger King) and Café launch (Starbucks), at least BD, then Legal and Design in parallel, then Budget | BEPM | 2 | S1-07 | Both compile; the PM signs off the step lists. |
- S1-10 | Tasks API: list (mine, team), detail with UI spec, submit (pending → complete → committed), start a site | BE | 3 | S1-06, S1-08 | An L1 submit creates the L2 approval; a crash mid-submit is reconciled. |
- S1-11 | @zm/tokens from colors_and_type.css: light, dark, fonts, motion | FE | 1.5 | S1-01 | The token page matches the Z-Matrix preview pages. |
- S1-12 | @zm/ui batch 1: Button, Tabs, TextField, Number, Currency, Select, Date, Toggle, StatusPill, StageDot, Avatar, PageHeader, Sidebar, TopBar, DataTable, Drawer, Toast, MetricCard | FE ×2 | 6 | S1-11 | Every component has a light and dark preview and a visible keyboard focus. |
- S1-13 | Shell: login, router, GET /api/ui, template and region renderer, static block registry, theme | FE | 3 | S1-05, S1-11 | Changing a release's UI config changes the page with no frontend deploy. |
- S1-14 | Blocks batch 1: TaskInbox, SiteTable, StageTracker | FE | 3 | S1-10, S1-12, S1-13 | L1 sees own tasks and L2 sees the team queue, from the real API. |
- S1-15 | @zm/forms v1: sections, rows, 12-column grid; text, number, currency, date, select, yes/no; Ajv validation | FE | 3 | S1-07, S1-12 | The same bad input is rejected in the browser and by the API. |
- S1-16 | Isolation suite v1: cross-client ids return 404, each Operaton lists only its own definitions, credentials open one database only | QA | 1.5 | S1-05, S1-08 | Runs in CI and fails on any cross-client read. |
- S1-17 | Test harness: seeded fixtures for two clients, API test client, Playwright skeleton | QA | 3 | S1-01 | One command runs API and browser tests against fresh fixtures. |
- S1-18 | Observability and environments: Sentry, JSON logs tagged by client, dev and staging | PLAT | 2 | S1-01 | Every log line and error carries the client slug and request id. |
- S1-19 | Design QA of batch 1 against the Z-Matrix preview pages | DES | 2 | S1-12 | Signed-off list with no open P1 visual issues. |
- S1-20 | Hosting spike: create, start, stop and update an Operaton container through the hosting provider's API (Railway, Fly or ECS); pick one | PLAT | 1.5 | S1-02 | A script starts a new client's Operaton in minutes and removes it cleanly. |
Planned ≈ 50 pd · FE 16.5 · BE 17.5 · PLAT 8 · QA 4.5 · DES 2 · PM 1
Demo on day 14
BK Legal L1 opens the task inbox→
fills "Statutory licences" and submits→
L2 sees the approval in the team queue→
Starbucks runs a different flow on its own Operaton→
a BK token can't read any Starbucks data
Sprint 2 · Days 15–28
Configure and run
Sprint goal
A new client signs up, is approved, shapes its flow with the agent, publishes it, and its people move a site through three departments.
ID | Task | Owner | Est. (pd) | Depends on | Done when |
- S2-01 | Sign-up form and platform console: approve, or reject with a reason | FEBE | 2 | S1-04, S1-13 | Approve starts provisioning; reject emails the reason. |
- S2-02 | Provisioning worker: Supabase project and keys, SQL, bucket, start the client's Operaton container through the hosting API, registry entry, seed admin and template; status page | PLATBE | 5 | S1-02, S1-03, S1-20 | Approval gives a working workspace, with its own Operaton, and no manual step; a failed step re-runs safely. |
- S2-03 | Draft and change log (JSON patches with author and conversation) with undo | BE | 2 | S1-07 | Every change is listed with its author; undo restores the previous draft exactly. |
- S2-04 | Agent tools v1 with strict schemas: read, org, flow, forms, UI, validate, diff | BE | 4 | S2-03 | Each tool is unit-tested; bad arguments never touch the draft. |
- S2-05 | Agent loop: Anthropic SDK, claude-opus-5-5, effort per task, prompt caching, streaming, append-only history, token budget, refusal fallback | BE | 3 | S2-04 | Streams to the dock; cache reads show in usage; history is never rewritten. |
- S2-06 | Onboarding interview prompt and template picker | BEPM | 2 | S2-05 | A new admin reaches a valid draft in one conversation. |
- S2-07 | Agent dock UI: chat, streaming, change chips ("Added: Fire NOC upload"), undo, Preview and Publish | FE | 3 | S2-05 | Each change is visible as it happens and can be undone. |
- S2-08 | Preview mode (draft with sample data) and diff against live | FEBE | 3 | S1-13, S2-03 | Preview equals what users get after publish; the diff lists tasks, fields and blocks. |
- S2-09 | Publish: compile → deploy → activate; running sites stay on their version | PLATFE | 2 | S1-08, S2-08 | New sites use the new release; running sites are unaffected. |
- S2-10 | Files: upload URL and confirm, slot rules, FileSlot with versions, signed download, PDF and image preview | BEFE | 3 | S1-10, S1-12 | A wrong type or size is refused; the database holds keys only. |
- S2-11 | Approvals: ApprovalBar, reject reasons, send back with comment, chain progress, decisions table | FEBE | 3 | S1-10 | Send back returns the task to the chosen step; the L2 → L3 chain works. |
- S2-12 | UI and blocks batch 2: SitePipeline, FlowMap, ApprovalsQueue, DocumentChecklist, KpiTile; checklist, line-item, computed and file fields | FE ×2 | 6 | S1-12 | All render from config for both clients. |
- S2-13 | Home and department templates finished: hero strip, FlowMap with live counts, my tasks, approvals, agent dock; Queue, Pipeline, Sites, History, SiteDrawer | FE | 3 | S2-12 | Both templates render from release config. |
- S2-14 | KPI v1: views v_stage_spans, v_task_spans; duration and count KPIs; two per template | PLATBE | 2 | S1-10 | Tiles match hand-computed values on seeded history. |
- S2-15 | Access rules: nav, page and section visibility by level; own, department or all scope; studio admin-only | BEFE | 2 | S1-06, S1-13 | L1 can't see hidden sections in the UI or the API; non-admins get 403 on studio. |
- S2-16 | End-to-end: port matrix.py check (one site, three departments, L1 to L3) for both clients; Playwright per role | QA | 3 | S2-09, S2-11 | Green in CI for both clients. |
- S2-17 | Agent eval set: 30 admin requests with expected draft changes, run on every prompt change | QABE | 2 | S2-05 | Pass rate reported in CI; no regressions before a release. |
- S2-18 | Security pass: service-account password rotation for each Operaton, Data API check per project, backups verified, secret handling review | PLAT | 2 | S2-02 | Checklist signed; an automated check fails if a client project exposes a schema. |
- S2-19 | Design QA of batch 2, agent dock and preview | DES | 2 | S2-07, S2-13 | Signed-off list with no open P1 visual issues. |
- S2-20 | Demo data and run-book | PMQA | 1 | all | The full demo runs end to end in under 15 minutes. |
Planned ≈ 55 pd · FE 20 · BE 19.5 · PLAT 7.5 · QA 4.5 · DES 2 · PM 1.5
Demo on day 28
Burger King signs up→
platform admin approves; workspace builds itself→
agent: "QSR template, Legal has 3 levels, add a Fire NOC upload with L2 approval"→
Preview, then Publish v1→
a site moves through BD, then Legal and Design in parallel, with one send-back→
Home counts and KPIs update
Done means
merged after reviewtests pass in CIworks for both clientsshown in the demo
Next · Sprint 3 onward
Backlog
Planned, but outside these 28 days.
Migrate running sites, with impact screen
SLA timers and escalation
Email notifications
Warehouse relay and platform dashboards
Fleet tooling: migrate-all and one-client-at-a-time Operaton upgrades
Shared Operaton for small clients, if dozens sign up
Observer polish
Mobile capture for field executives
Single sign-on
Read-only assistant for all users
Form pre-fill agent (consider LangGraph)
Geography, reports-to and amount-based approvals
AWS exit drill
ActivityFeed and HeroTile blocks

# Part 3
UI per client
Every client now has its own Operaton. The screens don't change: one Z-Matrix shell serves every client, and each client's look and arrangement is data in its own database.

## № 15 · One shell
One shell, three clients
Operaton decides what is open and who must act. It never draws a screen. The shell draws everything, from each client's own configuration.
Same shell, same blocks. Each client's own release decides the page, and each client's own Operaton and database fill it with data.
Piece | Where it lives | How many |
Tokens, components, blocks, layout templates | The shell: one React build | One for all clients |
A client's pages, forms, nav, accent, KPIs | That client's release, in its own database | One per client, versioned |
Open tasks, and who must act | That client's Operaton | One per client |
Form answers, documents, decisions | That client's database and bucket | One per client |
Operaton's own screens (Tasklist, Cockpit) | Switched off: run.sh --rest or OPERATON_BPM_WEBAPP_ENABLED=false | None for clients |
In one line
A separate Operaton per client changes where tasks come from. It doesn't change how screens are built: there is still one shell, and nothing on screen comes from Operaton.

## № 16 · One screen, step by step
How a Burger King screen is built
A Legal supervisor (level L2) at Burger King opens the Legal app. Six steps, under a second.
Operaton only answers step 5's question: which tasks are open for legal__L2, each with a formKey like zm:lg_licences. The shell finds that form in BK's release and draws it with Z-Matrix fields.
Login happens once, on the platform. Nobody ever signs in to Operaton.
The UI config is read from that client's database, so a client's screens can't leak into another client's app.
Filtering by department, level and scope happens in the API, before the shell sees anything.
The shell code is identical for every client. Only the data it receives differs.

## № 17 · Three clients, one design system
Same components, three different apps
Each client's admin shaped their app with the agent. The blocks are the same; the template, fields, levels and accent are theirs.
Burger King
QSR launch · release v3
Departments · levels
BD · 2Legal · 3Design · 2Budget · 2BA + HSO audit · 2Operations · 2
Config the agent wrote · Home
"home": {
"template": "home",
"regions": {
"hero": [
{ "block": "kpi_tile",
"props": {
"kpi": "loi_to_launch" } }],
"main": [
{ "block": "flow_map" },
{ "block": "task_inbox" }] } }
What the shell draws
Z-MatrixBurger KingHomeLegalDesignAudit

## № 01 · Home
Launch overview
38
days LOI → launch
12
sites in motion
BD 12→Legal 5→Design 8→Budget 3→Audit 2
My tasks
BK-0042Joint BA + HSO auditdue 1d
BK-0051Fire NOC uploadL2
Who sees what
BA auditor (L1): the audit checklist with photo slots
Audit lead (L2): the joint BA + HSO sign-off
CPO (business admin): every app, plus final closure
Starbucks
Café launch · release v7
Departments · levels
BD · 2Legal · 2Design · 3Finance · 2NSO · 3Operations · 2
Config the agent wrote · NSO
"nso": {
"template": "department",
"regions": {
"main": [
{ "block": "site_pipeline",
"props": { "columns": [
"equipment", "water_tds",
"bar_setup", "training"]
} }] } }
What the shell draws
Z-MatrixStarbucksHomeDesignNSOFinance

## № 05 · New store openings
Bar readiness
Equip­ment
2
sites
Water TDS
1
site
Bar setup
1
site
Training
2
sites
SB-0098Water TDS 120 ppmpass
Who sees what
Barista trainer (NSO L1): the training checklist only
NSO lead (NSO L3): the whole pipeline and the bar sign-off
CFO (business admin): every app, plus financial closure
Matrix Retail
Gated retail · release v2
Departments · levels
BD · 2Legal · 2Finance · 2Design · 4Project · 2NSO · 2Launch · 3
Config the agent wrote · Design
"design": {
"template": "department",
"regions": {
"hero": [
{ "block": "stage_tracker",
"props": { "stages": [
"recce", "2d", "3d",
"gfc", "boq"] } }],
"main": [
{ "block": "approvals_queue",
"props": {
"level": "design.L3" } }] } }
What the shell draws
Z-MatrixMatrix RetailBDLegalDesignProject

## № 04 · Design
Design stages
Recce
2D
3D
GFC
BOQ
Waiting for L3
MX-03113D renderL3 sign-off
MX-0302GFC drawingsdue 3d
Who sees what
Design executive (L1): uploads 2D, 3D and GFC files
Design head (L3): 3D and GFC sign-offs
Brand admin (L4): final design approval
All three use the same blocks: kpi_tile, flow_map, task_inbox, site_pipeline, stage_tracker, approvals_queue.
What differs is data: the template per page, which blocks sit where, their settings, the fields, the levels and the accent.
Accents come from the Z-Matrix palette (copper, teal, plum), so every app still looks like Z-Matrix.
Matrix Retail's four-level Design ladder and Starbucks' NSO track exist only in their own config. No code was written for either.

## № 18 · Changing the UI
Two ways the screens change
We change the building blocks for everyone. A client's admin changes the arrangement for their own company only.
Top lane: our code, for everyone. Bottom lane: one client's configuration, for that client only.
Design-system change (a new button style or a new block): one shell deploy, and every client has it the same day.
One client's layout change: the agent edits that client's draft and the admin publishes. Nothing is redeployed.
Something only one client needs: build the block once in the shared library and switch it on in that client's config. Never fork the frontend per client.
One rule: clients sit on different releases, so the shell must keep drawing every block version any live client uses. A breaking block change ships with a config migration, run one client at a time.
Checked against: the Operaton source (engine, webapps, webapps-neo), the operaton-plat prototype and its docs, the Matrix-bd repository, Supabase documentation, and customer_store_launch_flows.md.
Prepared October 2026. This page describes a plan; no code has been written for it yet.


## Diagram notes (text inside the artifact's diagrams)

> Extracted from the SVG diagrams of the artifact. Each entry: the diagram's description, then its labels in reading order.

### why — Every rule lives in code today
**What it shows:** Today one Matrix-bd codebase holds every rule and serves all clients the same flow. Next, each client has its own flow, levels and database, described as configuration.

Labels: TODAY · MATRIX-BD · Burger King · Starbucks · Blue Tokai · same flow for all · One codebase holds every rule · state_machine.py · BD transitions · workflow_unlocks.py · department gates · 11 budget lines · 5 licences · 9 DD items · module lists in 12 frontend registries · (rent+CAM)×1.18 · BT-CITY-XXXX · 3 fixed levels: Executive → Supervisor → Business admin · rules move · into config · NEXT · Z-MATRIX PLATFORM · Burger King · release v3 · 3 levels in Legal · BD → Legal || Design → Budget → BA+HSO → Launch · Starbucks · release v7 · own NSO track · BD → Legal || Design → Budget → NSO → Audit → CFO · Matrix Retail · release v2 · 4 levels in Design · 8 gated phases · Recce → 2D → 3D → GFC → BOQ · Each client: own flow, own levels, own screens, own database

### operaton — How Operaton handles workflows and forms
**What it shows:** Our prototype JSON files are compiled by matrix.py into BPMN, DMN and form files. They are sent to Operaton in one deployment, which becomes an immutable release. The engine runs sites and Tasklist loads each task's form from the same release.

Labels: WE WRITE · PROTOTYPE JSON · OPERATON UNDERSTANDS · WHAT OPERATON DOES · catalogue.json · department presets · workspace.json · a.k.a. workflow.json · tasks · fields · roles · order · compile · matrix.py · validate → generate · emits · .bpmn · the flow · XML: tasks, gateways, timers · .dmn · rule tables · optional · forms · 3 styles · embedded .html (cam-variable-*) · Camunda Form .form (formRef) · generated (operaton:formData) · each client's release carries its own forms · POST · Deployment = release vN · immutable · versioned per key · POST /deployment/create · ACT_RE_DEPLOYMENT · BYTEARRAY · parse · Engine runs a site · token waits at a user task · GET /task/{id}/form · Tasklist shows the form · formKey = embedded: · deployment:forms/x.html · read from the same release · application.yaml · engine-wide: database, history, security, job executor · one per Operaton server · same template for every client

### operaton-ui — Can Operaton's screens be changed per client?
**What it shows:** Operaton's apps give every client the same screens because config, CSS and plugins are installed once per server. The Z-Matrix shell reads each client's release and draws a different arrangement for each client from the same design system.

Labels: OPERATON'S OWN APPS · Burger King · Starbucks · Matrix · log in · Tasklist · Cockpit · one config.js · one CSS · one plugin set · same look for every client · Z-MATRIX SHELL · Burger King · Starbucks · Matrix · BK v3 · Sbux v7 · Matrix v2 · UI config · One shell · Z-Matrix packages installed · one design system, a different arrangement per client

### interface — How every user gets their own interface
**What it shows:** Three layers. Installed once: tokens, components, blocks with manifests and layout templates. Per client: each release holds pages, forms, navigation and accent written by the admin with the agent. Per user: the release is filtered by department, level and scope into what each person sees.

Labels: 1 · INSTALLED ONCE IN THE SHELL · ours · the same for every client · ships with platform releases · Tokens · colours · type · spacing · @zm/tokens · Components · Button · Field · Table · @zm/ui · Blocks + manifests · TaskInbox · KpiTile · @zm/blocks · Layout templates · home · department · site · regions: hero · main · the agent picks and arranges these · manifests only · 2 · PER CLIENT · WRITTEN BY THE ADMIN WITH THE AGENT · stored inside each client's release · Burger King · release v3 · pages · forms · nav · accent · home.hero = [kpi_tile, kpi_tile] · Starbucks · release v7 · pages · forms · nav · accent · legal.main = [task_inbox] · Matrix Retail · release v2 · pages · forms · nav · accent · design.main = [stage_tracker] · 3 · PER USER · AUTOMATIC · filtered by department, level and scope · BK release v3 + who is logged in · Legal executive · L1 · my tasks · rent and budget hidden · Head of Legal · L3 · team queue · L3 approvals · KPIs · Business admin · every app · agent dock · Publish

### architecture — The system in one picture
**What it shows:** The browser talks only to the Platform API. The API calls Claude for the agent and the platform project for the client registry. For each client it calls that client's own Operaton container over a private connection and that client's Supabase project with its own pool. Each Operaton container connects only to its own client's project.

Labels: Browser · Z-Matrix shell · home · department apps · studio · HTTPS + JWT (client id) · Platform API · FastAPI · login · client lookup · tasks · files · KPIs · studio · agent · publish · Claude API · agent loop (configurator) · agent · Worker · provisioning · notifications · warehouse relay · reconciler · same codebase · zm-platform · Supabase · platform: clients · requests · client → Operaton address · wh: warehouse facts · control-plane SQL · provisions · private REST · per-client service account · client SQL · own pool · ONE OPERATON PER CLIENT · ONE SUPABASE PROJECT PER CLIENT · Operaton · bk · own container · REST only · JDBC :5432 · zm-bk · Supabase · app · operaton · files · Operaton · sbux · own container · REST only · zm-sbux · Supabase · app · operaton · files · Operaton · matrix · own container · REST only · zm-matrix · Supabase · app · operaton · files · Users log in only to the platform. Each request is bound to one client: its Operaton, its database pool, its bucket.

### isolation — Each client in its own vault, with a clean exit to AWS
**What it shows:** The Platform API opens a separate connection to each client's vault. Each vault is that client's own Operaton server plus its Supabase project with the app schema, the operaton schema and a private file bucket, with no path between vaults. Moving one client to AWS takes four steps: dump, restore, sync the bucket, switch URLs.

Labels: Platform API · separate links per client · separate · zm-bk · Burger King · API off · RLS deny · Operaton · own server · app · our data · operaton · engine data · files · private · no path between clients · zm-sbux · Starbucks · API off · RLS deny · Operaton · own server · app · our data · operaton · engine data · files · private · zm-matrix · Matrix Retail · API off · RLS deny · Operaton · own server · app · our data · operaton · engine data · files · private · EXIT ONE CLIENT TO AWS · 4 STEPS · 1 · pg_dump · copy the database · 2 · pg_restore → RDS · same tables, same rows · 3 · rclone --checksum · bucket → S3, verified · 4 · switch URLs · registry + Operaton env

### levels — Departments, levels and approvals
**What it shows:** The Legal department has three levels. A licence task done by L1 goes to L2 for approval and L3 for sign-off, then hands off to Budget. L2 can send it back with a comment; L3 can reject and close the site.

Labels: LEGAL · LEVELS SET BY THE CLIENT · L3 · Head of Legal · L2 · Legal Supervisor · L1 · Legal Executive · names and number of levels · are the client's choice · Statutory licences · done by legal.L1 · submit · L2 approves · legal.L2 · approve · L3 signs off · legal.L3 · hand off · Budget · finance.L1 · send back · comment required · reject · Close site · reason chips · legal.L2 → BPMN candidate group legal__L2

### agent — The admin's agent edits a draft, a person publishes
**What it shows:** The admin sends a message to the Platform API, which gives Claude the prompt and tools. Claude calls typed tools that only change the draft; validation errors go back to Claude. When the draft is ready the admin previews it and a person clicks Publish, which compiles and deploys a release.

Labels: Admin · agent dock · types a request · message · Platform API · fixes the client · AGENT CAN ONLY TOUCH THE DRAFT · prompt + tools · claude-opus-5-5 · plans the change · tool calls · Typed tools · strict schemas · apply · Draft config · JSON patch + log · validate · errors back · ready · Preview · draft in the shell · looks right · Publish · a person clicks · deploy · Release vN · client's Operaton · Publish and migration are buttons a person presses. The model has no tool for them.

### live — Changing a flow while sites are running
**What it shows:** Sites A, B and C start on v1. When v2 is published the admin either lets them finish on v1, which is the default, or migrates them after an impact screen shows which sites move cleanly and which need a decision.

Labels: v1 published · sites A · B · C start on v1 · v2 published · Stay (default) · A · B · C finish on v1 · new sites start on v2 · Migrate · Impact screen · A: moves cleanly · B: step removed → choose · 3 open tasks affected · Batch migration · A and B now on v2 · Rollback = publish v1's config again as v3. Adding or moving people never needs a publish.

### kpis — KPIs inside each vault, benchmarks outside
**What it shows:** Inside each client's database, SQL views over engine history and app tables feed KPI definitions that draw tiles and charts. A relay copies sanitized facts into the platform warehouse for benchmarks, usage and billing.

Labels: Client database · operaton.act_hi_* · app.submissions · app.decisions · stays in the project · read · SQL views · v_stage_spans · v_task_spans · v_sla · v_decisions · SQL · KPI definitions · config · agent can add · duration · count · ratio · sum · sla · draw · Tiles and charts · 12.4 · days/dept · 7 · SLA misses · relay · read-only · incremental · sanitized · Warehouse · zm-platform.wh · facts only: no names, files or free text · Platform view · benchmarks · usage · billing

### team — 28 days, two demos
**What it shows:** Sprint 1 runs from day 1 to day 14 and ends with a demo of two isolated clients. Sprint 2 runs from day 15 to day 28 and ends with a demo of sign-up, agent configuration and a site moving through the flow.

Labels: Sprint 1 · days 1–14 · foundation · Sprint 2 · days 15–28 · configure and run · Day 1 · Day 8 · Day 15 · Day 22 · Day 28 · Demo 1: two isolated clients · Demo 2: sign up → agent → run a site

### ui-shell — One shell, three clients
**What it shows:** One Z-Matrix shell serves all clients. After login the Platform API finds the client and filters by level. Each client has its own UI config stored in its own database, its own Operaton for tasks, and its own database and bucket for answers and documents. The shell draws a different screen for Burger King, Starbucks and Matrix Retail.

Labels: One Z-Matrix shell · the same code for every client · tokens · components · blocks · layout templates · built and deployed once · after login: GET /api/ui · Platform API · finds the client · filters by level · Burger King · QSR launch · UI config · release v3 · stored in BK's own database · Operaton · bk · open tasks · who acts · formKey · Database + bucket · answers · documents · decisions · Starbucks · Café launch · UI config · release v7 · stored in Starbucks' own database · Operaton · sbux · open tasks · who acts · formKey · Database + bucket · answers · documents · decisions · Matrix Retail · Gated retail · UI config · release v2 · stored in Matrix's own database · Operaton · matrix · open tasks · who acts · formKey · Database + bucket · answers · documents · decisions · drawn by the shell · Home · KPIs, flow map, my tasks · NSO · bar readiness pipeline · Design · stages and L3 sign-offs

### ui-build — How a Burger King screen is built
**What it shows:** Sequence: the shell logs the user in through the Platform API; the API looks up Burger King in the registry, reads Burger King's active UI config from its database and returns the version filtered for this user's level; the shell draws the template and blocks; each block asks the API for data, which comes from Burger King's Operaton for open tasks and from its database for form answers and file links.

Labels: Shell (browser) · Platform API · BK database · BK Operaton · 1 · log in: workspace, email, password · 2 · look up BK in the registry · Operaton address · DB · bucket · 3 · read the active UI config · release v3 · pages · forms · nav · 4 · this person's UI, filtered to legal.L2 · draws template + blocks · 5 · each block asks for its data · open tasks for legal__L2 · form answers · file links · 6 · data back; formKey picks the form

### ui-changes — Two ways the screens change
**What it shows:** Two paths. Platform release: change a component or add a block, test it against every client's live config, deploy the shell once, and every client gets it. Client publish: the admin asks the agent, previews the draft, publishes a new release into that client's database, and only that client changes, with no deploy.

Labels: PLATFORM RELEASE · OURS · ALL CLIENTS AT ONCE · Change a component · or add a new block · Test every live config · all clients' releases · Deploy the shell once · one frontend deploy · Every client gets it · same day, same look · CLIENT PUBLISH · THEIRS · ONE CLIENT ONLY · Admin asks the agent · “show licences pending” · Draft and preview · same shell, draft config · Publish release vN · into that client's database · Only that client changes · no deploy, others untouched



Related: the scope note · [[PLATFORM]] · [[HOW-OPERATON-WORKS]]
