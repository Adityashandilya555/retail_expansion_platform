# Sprint 1 demo: run-book

About 15 minutes. One client, Matrix Retail, on its own Operaton and vault. Three sites at three stages, Legal's inbox, a task form, and a submission that moves the case on. Made by `scripts/demo_s1.py` (#31).

## Before (the day before, once)
These steps assume the pilot client is provisioned and `ready` in the owner console.
1. **Templates and the draft.** Run `uv run zm-api templates sync`. Provisioning already seeded the draft (S2-02b).
2. **Publish v1.** In the owner console, go to Clients → Matrix Retail → Publish. Or call `POST /owner/clients/<id>/publish` with an owner token.
3. **Demo users.** Run `railway run uv run zm-api users seed --client <slug> packages/templates/matrix-retail/demo-users.json`. It prints each new user's password once.
4. **Users file.** Put those passwords in `platform/.demo-users.json`. The file is gitignored; never commit it and never paste it into chat or an issue.
   ```json
   {"users": [
     {"email": "bd.l1@matrix-retail.demo", "password": "…", "department": "bd", "level": 1},
     {"email": "bd.l2@matrix-retail.demo", "password": "…", "department": "bd", "level": 2},
     {"email": "legal.l1@matrix-retail.demo", "password": "…", "department": "legal", "level": 1},
     {"email": "legal.l2@matrix-retail.demo", "password": "…", "department": "legal", "level": 2}
   ]}
   ```
5. **Dry run.** Run the driver below on the cloud pilot, and record its output in #31.

## Run the driver (about 1 minute)
```bash
uv run python scripts/demo_s1.py --api https://<api domain> --client <workspace code> --app-url https://<client app domain>
```
It exits 0 and prints three codes, for example `MX-0001 (at Legal), MX-0002 (first stage), MX-0003 (Legal done)`, plus the app URL and the workspace code. If it exits 1, the message says which step or check failed.

## Show (about 12 minutes)
| # | Who | Screen | What it shows |
|---|---|---|---|
| 1 | — | Client app URL → workspace code | The sign-in screen in Matrix Retail's look: the code picks the client. |
| 2 | BD L2 | Sites | Three sites: one at its first stage, one at Legal, one past Legal (cases blocks: #92). |
| 3 | Legal L1 | My work | `Fill DD checklist` under the first site. |
| 4 | Legal L1 | That task | The form from the release. Fill a few items and submit. |
| 5 | Legal L2 | My work | `Finalise DD verdict` now waits for Legal L2. It shows the checklist answers read-only; they come from the client's database, not from Operaton. |
| 6 | BD L1 | My work | No Legal task here: departments only see their own work. |

Talking point: each client has its own Operaton and its own database. `pytest -m isolation` proves one client can't reach another's.

## Run it again
No reset is needed. The driver always starts three new cases, and the case counter keeps counting, so a second run gives MX-0004 to MX-0006 next to the first three. Every new code is new to the engine, so tasks never mix between runs.

**Don't reset `app.case_counters`.** The engine's earlier instances keep their business keys (MX-0001, …). A restarted counter would hand those codes out again, and the old instances' tasks would show up under the new cases.

## Clean up (after the demo, demo-only client)
> **Warning:** this deletes every case of the client. Use it only on a client that exists just for the demo, never on one with real work.

1. **Hide the demo cases.** The person running the demo does this in the pilot vault's SQL editor (Supabase dashboard → SQL):
   ```sql
   delete from app.cases;   -- cascades to app.submissions and app.case_values; the counter stays
   ```
   Tasks of instances without a case row are left out of every list.
2. **Optionally, end the engine's instances.** The client's engine has no public domain (the worker never gives it one). So this runs from inside the Railway project, for example `railway ssh` into the `api` service, against `http://operaton-<slug>.railway.internal:8080/engine-rest`:
   1. `GET /process-instance?processDefinitionKeyLike=zm_%`
   2. `DELETE /process-instance/<id>` for each one.

   It runs as `zm-service`. Its password is the client's sealed secret `service_account_password`; read it in that shell with `zm_api.secrets.unseal`, and don't print or paste it anywhere.
3. **Draft edits.** To start the draft over from the template: `zm-api drafts seed --client <slug> --template matrix-retail`.
