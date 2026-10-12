---
description: Check one spec issue for validity and refine it before anyone implements it (read-only unless you add --apply and confirm)
argument-hint: <issue number> [--apply]
---
Arguments: `$ARGUMENTS`. The first word is the issue number N in Adityashandilya555/retail_expansion_platform; `--apply` allows editing the issue after I confirm.
Goal: decide whether issue N is still valid, and make it implementable **before** `/implement-issue N`. Never write code here.

## Read first
- `gh issue view N --repo Adityashandilya555/retail_expansion_platform --comments`, and every issue listed under "Depends on" (state, and for closed ones the merged PR).
- Plan issue #7 (decisions D1–D18, sprint tables, verified Operaton map).
- `platform/AGENTS.md` (especially "Agent tools and rules"), `platform/docs/PRODUCT.md` (overrides the blueprint), and the `platform/docs/BLUEPRINT.md` section the issue cites (§NN).

## Check, and record each as ✅ / ⚠️ / ❌ with evidence
1. **Form.** Every `zm_spec` section is present (Context · Spec · How · Operaton references · Acceptance criteria · Test plan · Out of scope · Depends on · Environment variables). Labels: exactly one `sprint:*`, ≥ 1 `area:*`, one `type:*`, one `priority:*`, one `size:*`, the `via:*` labels its How steps use, `needs:human` iff there is a `HUMAN:` step. The milestone matches the sprint label. Size ≤ L, otherwise propose a split.
2. **Product and conventions.** Nothing contradicts `PRODUCT.md` (two surfaces, agent only in the owner console, change requests reviewed by a person, additive field versioning, theme packs on fixed layouts) or `AGENTS.md` (our code only under `platform/`, Operaton unmodified unless `area:engine`, client id only from the JWT, cross-client reads return 404, file keys not URLs, secrets never in git).
3. **Dependencies.** Each dependency's state. For merged ones, read what actually landed (files, function names, routes, tables, env vars) and list every place this spec assumes something different. An open dependency is not a failure, but say what is blocked.
4. **Operaton references.** Verify every path, line range, endpoint, property and default with Sourcegraph, following "Code search: Sourcegraph rules": at `main` **and** at the pinned release (`rev:v2.1.5`). Give corrected lines with their revision, and flag any behaviour that differs between the two. For more than about five lookups, delegate to the `operaton-researcher` subagent.
5. **Repo facts.** Every file, directory, command, workflow, label, script and package named in the spec exists, or the spec creates it. Env vars match `platform/.env.example`; new ones are listed under "Environment variables" with the platform that holds them.
6. **Acceptance criteria.** Each is Given/When/Then, observable, and covered by the test command in "Test plan". Every Spec bullet has at least one criterion, and every criterion traces to the Spec.
7. **Overlap.** Search other issues (`gh issue list --repo Adityashandilya555/retail_expansion_platform --state all --search "<key terms>"`) for duplicated or conflicting scope, including Backlog.
8. **Time-sensitive facts.** Re-check pinned versions, vendor plan limits, API fields and action SHAs against the vendor's docs or registry. Note the date checked.

## Output (in chat)
1. **Verdict:** Ready / Needs refinement / Invalid, in one line with the main reason.
2. **Findings table:** check · status · evidence (paths with revision, issue links, doc URLs).
3. **Proposed rewrite:** only the sections that change, as replacement text, in the template's wording style.
4. **Questions for Adi:** decisions the spec can't make on its own.

## Applying (only with `--apply`, after I say yes)
- Update the issue body with `gh issue edit N --body-file …`, keeping every template section.
- Fix labels or milestone, if the checks found them wrong.
- Add one comment: "Refined on <YYYY-MM-DD>: <three-line summary>; checked against <revisions>".
- Never change scope silently. A scope change (added or removed behaviour, a different size, a new dependency) goes in that comment, addressed to Adi, and the issue body says "pending Adi" until he answers.
