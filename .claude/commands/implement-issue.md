---
description: Implement one spec issue end to end (spec → tests → code → PR)
argument-hint: <issue number>
---
Issue: #$ARGUMENTS in Adityashandilya555/retail_expansion_platform.

1. Read the issue (GitHub MCP or `gh issue view $ARGUMENTS --repo Adityashandilya555/retail_expansion_platform --comments`) and every issue in "Depends on". If a dependency is still open, stop and tell me.
2. Restate the spec in 5–10 lines: what changes, files, contracts, env vars. Flag anything ambiguous or contradicting AGENTS.md and ask before coding.
3. Branch `$ARGUMENTS-<slug>` from an up-to-date `main`.
4. Write tests from the acceptance criteria first; show they fail.
5. Implement. For platform steps use the MCP named in the issue's `via:*` label; confirm with me before anything that costs money, deletes resources, or changes production env vars. Stop at any `HUMAN:` step and tell me exactly what to click.
6. Run all checks for what you touched (platform: ruff, pytest, tsc, eslint, vitest, playwright; engine: ./mvnw -pl <module> test). Fix until green.
7. Update `.env.example` / docs if needed. Commit with conventional commits.
8. Push and open a PR with `gh pr create --repo Adityashandilya555/retail_expansion_platform --base main` (never against operaton/operaton): title `#$ARGUMENTS <issue title>`, body with `Closes #$ARGUMENTS`, summary, test evidence, env vars added. Tick the acceptance boxes you verified in the PR body (not in the issue).
