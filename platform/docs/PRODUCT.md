# Z-Matrix — product spec (overrides the blueprint where they differ)

Decided by Adi, 2026-10-09. The blueprint (`platform/docs/BLUEPRINT.md`) still holds for architecture, isolation, Operaton usage, compiler, forms, KPIs. This file decides **who does what** and **what clients may change**.

## Two surfaces, never Operaton's UI
| Surface | Who | Entry |
|---|---|---|
| **Owner console** | Us — platform owners/admins | Owner landing page (separate domain/path), owner login |
| **Client app** | People of one client company | Client landing page → **workspace code** (issued by us) → email + password |
Operaton's Tasklist/Cockpit/webapps are never shown to anyone outside our ops team. Every client screen is drawn by our shell.

## Owner console (us)
1. **Create client** — name, workspace code (we issue it), template. No public sign-up.
2. **Provision** — the worker creates the client's own Supabase project + its own Operaton on Railway, registry row, nothing manual.
3. **Configure with the agent** — we describe the client's flow on screen; the agent (Claude API, `claude-opus-5-5`) turns it into the client's draft **`workflow.json`**: departments/levels, flow and approvals, forms (fields), document types, apps/pages, KPIs. Typed tools, logged patches, undo.
4. **Choose look** — pick a **theme pack** and a **layout set** from our predefined catalogue (see UI below). No custom CSS, no custom components.
5. **Preview → publish** — a person clicks Publish; the compiler deploys release vN to that client's Operaton and stores UI config in that client's database.
6. **Invite the client admin** by email (the one person per client who manages people).
7. **Review change requests** from clients (fields, document types — see below), apply approved ones via the agent, publish a new version.
8. **Monitor** — per client: number of user accounts (active/invited/deactivated), sites in progress, last publish/version, health of its Operaton and database. Counts and status only — owners don't browse a client's business data.

## Client app (per client, isolated)
- **Login:** landing page → workspace code → email + password. The code picks the client vault (its Operaton, database, bucket); a user exists only inside that client.
- **Client admin** (designated by us): invites people by email, assigns department + level, deactivates people. Cannot change the flow, forms or UI.
- **Everyone else:** sees only what their department, level and scope allow; does tasks, fills forms, uploads documents, approves/rejects/sends back.
- **Change requests** (client admin): "add field", "rename field label", "add document type", "make field required/optional", "add select option". Submitted in the app → queued in the owner console → reviewed by a human → applied by us → new version. Nothing a client submits goes live without our review.

## What clients can vary: data, not design
- **Fields and document types are the main flexibility.** Each template ships **default fields/document types enabled up front** plus an **optional catalogue** that can be switched on by request.
- **Versioned and additive.** Example: v1 has `city`; v2 adds `state` and `district`. Rules:
  - adding fields/document types/options = new version, existing records keep their values, new fields are empty (or defaulted) on old records;
  - renaming changes the label, never the stored key;
  - removing = hide from new forms, keep stored data;
  - type changes or making a field required for running sites need an explicit migration decision in the owner console (default: required only for new sites);
  - every form submission stores the release/version it was made on.
- Field types come from the fixed catalogue in blueprint §09; clients choose among them, never invent one.

## UI: theme packs on a fixed set of layouts
- **Layouts ("screen types")** are a fixed set we build: home, department overview, queue, site detail, form, approvals, documents, people admin, landing/login. Each has fixed regions.
- **Theme packs** are complete looks we build and maintain: tokens (type, colour, spacing, radius, motion) + styled components (buttons, inputs, tables, form fields, file slots, approval bar, cards). Choosing a pack restyles every screen at once.
- **Blocks** (task inbox, KPI tile, pipeline, flow map, stage tracker, document checklist…) sit in layout regions; the agent places them per department in `workflow.json`.
- A client gets a theme pack + layouts + blocks chosen by us (client-side theme switching only among packs we enable — optional, later).
- **Impeccable** (impeccable.style) is a **development-time** design skill for our team's agents: we use it to build, critique and audit theme packs and layouts (`DESIGN.md` per pack, its detector in PR checks). It is not loaded into the client app at runtime. Check its licence before vendoring anything.

## Agent scope
- **Now:** owner console only — we talk to it per client.
- **Later (Backlog):** a restricted agent for the client admin that can only draft change requests (still human-reviewed).

## Deltas vs. the blueprint
| Blueprint | Now |
|---|---|
| Client signs up, platform admin approves (S2-01) | We create clients in the owner console; workspace code issued by us |
| Business admin configures with the agent (S2-06/S2-07) | **We** configure with the agent in the owner console |
| Client admin owns flow, people, KPIs | Client admin owns **people** and **change requests** only |
| Agent places blocks + sets accent | Agent places blocks; look = theme pack + fixed layouts |
| — | New: change-request queue with human review; field/document-type versioning rules; per-client account counts in owner console; client landing with workspace code |
