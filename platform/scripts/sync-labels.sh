#!/bin/sh
# Create/update Z-Matrix labels + milestones. Run from repo root: platform/scripts/sync-labels.sh [owner/repo]
REPO="${1:-Adityashandilya555/retail_expansion_platform}"
python3 - "$REPO" <<'PY'
import re, subprocess, sys
repo = sys.argv[1]
for line in open("platform/labels.yml"):
    m = re.search(r'name: "([^"]+)", color: "([^"]+)", description: "([^"]*)"', line)
    if m:
        n, c, d = m.groups()
        subprocess.run(["gh", "label", "create", n, "--repo", repo, "--color", c, "--description", d, "--force"], check=True)
        print("label", n)
for t in ["Sprint 1 — Foundation", "Sprint 2 — Configure and run", "Backlog"]:
    r = subprocess.run(["gh", "api", f"repos/{repo}/milestones", "-f", f"title={t}"], capture_output=True, text=True)
    print("milestone", t, "created" if r.returncode == 0 else "exists")
PY
