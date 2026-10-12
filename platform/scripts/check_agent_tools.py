#!/usr/bin/env python3
"""Check the agent tools from platform/AGENTS.md → "Agent tools and rules". Stdlib only.

Run from the repo root:  python3 platform/scripts/check_agent_tools.py
Exit code 0 = everything required works (warnings are allowed).
"""
import json
import os
import re
import subprocess
import urllib.request

REPO = "Adityashandilya555/retail_expansion_platform"
failed = False


def report(level, msg):
    global failed
    failed |= level == "FAIL"
    print(f"  {level:<5} {msg}")


def run(cmd, **kw):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, **kw)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(cmd, 1, "", str(e))


print("Node (REA needs 22.19+, 24.11+ or 26+)")
v = run(["node", "-v"]).stdout.strip()
m = re.match(r"v(\d+)\.(\d+)", v)
if not m:
    report("FAIL", "node not found: install Node 24 LTS")
else:
    major, minor = int(m[1]), int(m[2])
    if major >= 26 or (major == 24 and minor >= 11) or (major == 22 and minor >= 19):
        report("ok", f"node {v}")
    elif major == 25:
        report("WARN", f"node {v}: REA runs with an npm EBADENGINE warning; Node 24 LTS avoids it")
    else:
        report("FAIL", f"node {v} is too old for REA")

print("GitHub")
r = run(["gh", "repo", "view", REPO, "--json", "name"])
report("ok" if r.returncode == 0 else "FAIL", "gh can read the repo" if r.returncode == 0 else f"gh: {r.stderr.strip()[:120]}")

print("REA MCP server")
pin = next(a for a in json.load(open(".mcp.json"))["mcpServers"]["rea"]["args"] if a.startswith("rea-agents@"))
hello = "\n".join(json.dumps(x) for x in (
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "check", "version": "0"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})) + "\n"
r = run(["npx", "-y", pin, "mcp"], input=hello, timeout=300)
tools = None
for line in r.stdout.splitlines():
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    if msg.get("id") == 2:
        tools = len(msg.get("result", {}).get("tools", []))
report("ok" if tools else "FAIL", f"{pin} starts, {tools} tools" if tools else f"{pin} did not answer; try: npx -y {pin} mcp")

print("Sourcegraph")
endpoint = os.environ.get("SRC_ENDPOINT", "http://localhost:7080").rstrip("/")
token = os.environ.get("SRC_ACCESS_TOKEN", "")


def post(path, body):
    req = urllib.request.Request(endpoint + path, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"token {token}", "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, resp.read().decode()


def count(query):
    _, body = post("/.api/graphql", {"query": "query($q:String!){search(query:$q,version:V3){results{matchCount}}}",
                                     "variables": {"q": query}})
    return json.loads(body)["data"]["search"]["results"]["matchCount"]


if not token:
    report("FAIL", "SRC_ACCESS_TOKEN is not set: export it in ~/.zshrc, then open a new terminal")
else:
    try:
        _, body = post("/.api/graphql", {"query": "{ currentUser { username } repositories(first: 50) { nodes { name } } }"})
        data = json.loads(body)["data"]
        report("ok", f"{endpoint} as {data['currentUser']['username']}")
        names = {n["name"] for n in data["repositories"]["nodes"]}
        for repo in ("github.com/operaton/operaton", f"github.com/{REPO}", "github.com/Shrey2149/outpost"):
            report("ok" if repo in names else "FAIL", f"indexed: {repo}" if repo in names else f"not indexed: {repo}")
        n = count("repo:^github.com/operaton/operaton$ rev:v2.1.5 file:^pom.xml$ 2.1.5")
        report("ok" if n else "FAIL", "rev:v2.1.5 search works" if n else "rev:v2.1.5 search found nothing")
        n = count(f"repo:^github.com/{REPO}$ fork:yes file:^platform/AGENTS.md$ Sourcegraph")
        report("ok" if n else "FAIL", "fork:yes search finds our fork" if n else "fork search found nothing")
        status, _ = post("/.api/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "check", "version": "0"}}})
        report("ok" if status == 200 else "FAIL", f"MCP endpoint {endpoint}/.api/mcp answers {status}")
    except Exception as e:  # network, auth or schema errors all mean "not usable"
        report("FAIL", f"{endpoint}: {e}")

print("\nIn Claude Code: open /mcp in this repo and approve `sourcegraph` and `rea` (enable rea only when needed).")
raise SystemExit(1 if failed else 0)
