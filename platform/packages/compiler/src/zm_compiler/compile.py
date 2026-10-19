"""`workflow.json` → `Release` (#19, S1-08a), ported from the prototype's compiler
(`compile_main` :408, `compile_module` :352, `add_task` :379 in platform/prototype/matrix.py).

Pure and deterministic: no network, no clock, and the same document gives byte-identical output.
"""

import re
from collections.abc import Callable

from zm_compiler.bpmn import End, Proc, set_var
from zm_compiler.forms import build_form
from zm_compiler.release import FormSpec, Group, Release
from zm_schema import WorkflowV1, group_id, validate
from zm_schema.models import (
    FieldPerformer,
    Flow,
    InitiatorAtLeastRule,
    InitiatorPerformer,
    LevelPerformer,
    Outcome,
    ParallelGroup,
    Stage,
    Task,
)

SLUG = re.compile(r"^[a-z][a-z0-9-]{0,40}$")
IDENT = re.compile(r"\b[a-z][a-z0-9_]*\b")
CLOSED = End("Closed", error="CLOSED")
APPROVAL_OUTCOMES = {
    "approve": "Approve",
    "send_back": "Send back",
    "reject": "Reject",
}


class CompileError(ValueError):
    pass


def process_key(client_slug: str, flow_key: str) -> str:
    return f"zm_{client_slug.replace('-', '_')}__{flow_key}"


def form_key(form_id: str) -> str:
    key = f"zm:{form_id}"
    if "${" in key:  # Operaton parses form keys as expressions (BpmnParse.java:2861-2869)
        raise CompileError(f"form key {key!r} must not contain an expression")
    return key


class _Compiler:
    def __init__(self, wf: WorkflowV1, client_slug: str) -> None:
        self.wf = wf
        self.slug = client_slug
        self.levels = {d.key: sorted(lv.n for lv in d.levels) for d in wf.org.departments}
        self.forms: dict[str, FormSpec] = {}
        self.routing: dict[str, list[str]] = {}

    # ── helpers ──

    def groups_for(self, department: str, level: int, or_above: bool) -> list[str]:
        ns = [n for n in self.levels[department] if n >= level] if or_above else [level]
        return [group_id(department, n) for n in ns]

    def cond(self, rule: object) -> str:
        """A rule as JUEL (without `${}`): text passes through; initiator level checks use the
        comma-wrapped `initiator_groups` variable the API sets at case start (S1-10)."""
        if isinstance(rule, InitiatorAtLeastRule):
            dl = rule.initiator_at_least
            checks = [
                f"initiator_groups.contains(',{g},')"
                for g in self.groups_for(dl.department, dl.level, or_above=True)
            ]
            return "(" + " || ".join(checks) + ")"
        return str(rule)

    def rule_fields(self, flow: Flow) -> list[str]:
        """Fields any rule or assignee of the flow reads: they must reach Operaton (D5)."""
        found: list[str] = []

        def add(key: str) -> None:
            if key in self.wf.fields and key not in found:
                found.append(key)

        for stage in flow.stages:
            for t in _tasks(stage):
                rules = [r.when for r in t.route] + ([t.skip_if] if t.skip_if is not None else [])
                for rule in rules:
                    if isinstance(rule, str):
                        for name in IDENT.findall(rule):
                            add(name)
                if isinstance(t.performer, FieldPerformer):
                    add(t.performer.from_field)
        return found

    def form_fields(self, form_id: str | None) -> list[str]:
        if form_id is None:
            return []
        out: list[str] = []
        for s in self.wf.forms[form_id].sections:
            for row in s.rows:
                out += [c.field for c in row if c.field not in out]
        return out

    # ── main process per flow (compile_main) ──

    def main(self, flow: Flow) -> Proc:
        key = process_key(self.slug, flow.key)
        starters: list[str] = []
        for p in flow.start.performers:
            if isinstance(p, LevelPerformer):
                starters += [
                    g
                    for g in self.groups_for(p.department, p.level, p.or_above)
                    if g not in starters
                ]
        p = Proc(key, flow.name, starters=starters or None)
        p.node(
            "start",
            "start",
            f"New {flow.case_label.lower()}",
            form=form_key("start"),
            initiator=True,
            listeners=[
                ("start", set_var("stage", "Created")),
                ("start", set_var("case_status", "open")),
            ],
        )
        stages = flow.stages
        preds = {s.key: list(s.after) for s in stages}
        succs = {k: [s.key for s in stages if k in preds[s.key]] for k in preds}

        def inn(k: str) -> str:
            return f"join_{k}" if len(preds[k]) > 1 else f"call_{k}"

        def out(k: str) -> str:
            return f"split_{k}" if len(succs[k]) > 1 else f"call_{k}"

        roots = [k for k in preds if not preds[k]]
        sinks = [k for k in preds if not succs[k]]
        first = "start"
        if len(roots) > 1:
            first = p.node("split_start", "par")
            p.flow("start", first)
        for k in roots:
            p.flow(first, inn(k))
        for s in stages:
            k = s.key
            if len(preds[k]) > 1:
                p.node(f"join_{k}", "par")
                p.flow(f"join_{k}", f"call_{k}")
            p.node(
                f"call_{k}",
                "call",
                s.name,
                called=f"{key}__{k}",
                listeners=[
                    ("start", set_var("stage", s.name)),
                    ("start", set_var(f"m_{k}", "in progress")),
                    ("end", set_var(f"m_{k}", "done")),
                ],
            )
            p.node(f"closed_{k}", "boundary", "Closed", host=f"call_{k}", error="CLOSED")
            p.flow(
                f"closed_{k}",
                End(
                    f"Closed in {s.name}",
                    terminate=True,
                    sets=[("stage", f"Closed in {s.name}"), ("case_status", f"closed:{k}")],
                ),
            )
            if len(succs[k]) > 1:
                p.node(f"split_{k}", "par")
                p.flow(f"call_{k}", f"split_{k}")
            for nxt in succs[k]:
                p.flow(out(k), inn(nxt))
        last = out(sinks[0])
        if len(sinks) > 1:
            last = p.node("join_end", "par")
            for s in sinks:
                p.flow(out(s), last)
        p.flow(
            last,
            End("Completed", sets=[("stage", "Completed"), ("case_status", "completed")]),
            hint="completed",
        )

        start_form = self.wf.forms.get(flow.start.form)
        self.forms[f"start:{flow.key}"] = build_form(self.wf, start_form)
        rule_fields = self.rule_fields(flow)
        self.routing[f"start:{flow.key}"] = ["initiator", "initiator_groups"] + [
            f for f in self.form_fields(flow.start.form) if f in rule_fields
        ]
        return p

    # ── one process per stage (compile_module + add_task) ──

    def stage(self, flow: Flow, stage: Stage) -> Proc:
        key = f"{process_key(self.slug, flow.key)}__{stage.key}"
        p = Proc(key, stage.name, startable=False)
        steps = stage.tasks
        rule_fields = self.rule_fields(flow)

        def entry(i: int) -> str:
            if i >= len(steps):
                return "done"
            s = steps[i]
            if isinstance(s, ParallelGroup):
                return f"split{i}"
            return f"{s.id}_skip" if s.skip_if is not None else s.id

        p.node("start", "start", "Start")
        p.flow("start", entry(0))
        p.node("done", "end", "Done")
        for i, s in enumerate(steps):
            nxt = entry(i + 1)
            if isinstance(s, ParallelGroup):
                p.node(f"split{i}", "par")
                p.node(f"join{i}", "par")
                for t in s.parallel:
                    p.flow(f"split{i}", t.id)
                    self.task(p, t, f"join{i}", rule_fields)
                p.flow(f"join{i}", nxt)
            else:
                self.task(p, s, nxt, rule_fields)
        return p

    def task(self, p: Proc, t: Task, nxt: str, rule_fields: list[str]) -> None:
        tid = t.id
        approval_ids = self.approval_ids(t)
        after = approval_ids[0] if approval_ids else nxt

        def target(to: str | None) -> str | End:
            if to is None:
                return after
            if to == "@end":
                return "done"
            if to == "@close":
                return CLOSED
            return to

        if t.skip_if is not None:
            c = self.cond(t.skip_if)
            p.node(tid + "_skip", "xor", "Skip?")
            p.flow(tid + "_skip", tid, f"${{!({c})}}")
            p.flow(tid + "_skip", nxt, f"${{{c}}}", "Skip")
        perf = t.performer
        if isinstance(perf, InitiatorPerformer):
            who = {"assignee": "${initiator}"}
        elif isinstance(perf, FieldPerformer):
            who = {"assignee": f"${{{perf.from_field}}}"}
        else:
            who = {"groups": ",".join(self.groups_for(perf.department, perf.level, perf.or_above))}
        p.node(tid, "task", t.name, form=form_key(tid), **who)

        outcome_var = f"{tid}_outcome" if t.outcomes else None
        form = self.wf.forms.get(t.form) if t.form else None
        self.forms[tid] = build_form(
            self.wf, form, outcome_var=outcome_var, outcomes=list(t.outcomes), show=list(t.show)
        )
        routing = [f for f in self.form_fields(t.form) if f in rule_fields]
        if t.outcomes:
            routing += [f"{tid}_outcome", f"{tid}_comment"]
        self.routing[tid] = routing

        outs, routes = t.outcomes, t.route
        if not outs and not routes:
            p.flow(tid, after)
        else:
            self.branches(p, t, after, target)
        if approval_ids:
            self.approvals(p, t, approval_ids, nxt)

    def branches(
        self, p: Proc, t: Task, after: str, target: Callable[[str | None], str | End]
    ) -> None:
        tid, outs, routes = t.id, t.outcomes, t.route
        p.node(tid + "_gw", "xor")
        p.flow(tid, tid + "_gw")
        for o in outs:
            p.flow(
                tid + "_gw",
                target(o.to),
                f"${{{tid}_outcome == '{o.id}'}}",
                o.label,
                hint=f"{tid}_{o.id}",
            )
        if routes and not outs:
            conds = [self.cond(r.when) for r in routes]
            for i, (r, c) in enumerate(zip(routes, conds, strict=True)):
                p.flow(tid + "_gw", target(r.to), f"${{{c}}}", r.label, hint=f"{tid}_r{i}")
            otherwise = " && ".join(f"!({c})" for c in conds)
            p.flow(tid + "_gw", after, "${" + otherwise + "}", "Otherwise")

    @staticmethod
    def approval_ids(t: Task) -> list[str]:
        ids: list[str] = []
        for i, link in enumerate(t.approval.chain if t.approval else []):
            aid = f"{t.id}__appr_{link.department}_L{link.level}"
            ids.append(aid if aid not in ids else f"{aid}_{i}")
        return ids

    def approvals(self, p: Proc, t: Task, ids: list[str], nxt: str) -> None:
        """One user task + gateway per approval chain entry after `t` (send back, reject)."""
        assert t.approval is not None
        chain = t.approval.chain
        back = t.approval.send_back_to or t.id
        for i, (aid, link) in enumerate(zip(ids, chain, strict=True)):
            dept = next(d for d in self.wf.org.departments if d.key == link.department)
            p.node(
                aid,
                "task",
                f"{t.name}: {dept.name} L{link.level} approval",
                form=form_key(aid),
                groups=group_id(link.department, link.level),
            )
            gw = aid + "_gw"
            p.node(gw, "xor")
            p.flow(aid, gw)
            approve_to = ids[i + 1] if i + 1 < len(ids) else nxt
            p.flow(
                gw, approve_to, f"${{{aid}_outcome == 'approve'}}", "Approve", hint=f"{aid}_approve"
            )
            p.flow(
                gw, back, f"${{{aid}_outcome == 'send_back'}}", "Send back", hint=f"{aid}_send_back"
            )
            outcomes = [
                Outcome(id="approve", label=APPROVAL_OUTCOMES["approve"]),
                Outcome(
                    id="send_back", label=APPROVAL_OUTCOMES["send_back"], comment_required=True
                ),
            ]
            if t.approval.allow_reject:
                p.flow(
                    gw, CLOSED, f"${{{aid}_outcome == 'reject'}}", "Reject", hint=f"{aid}_reject"
                )
                outcomes.append(
                    Outcome(id="reject", label=APPROVAL_OUTCOMES["reject"], comment_required=True)
                )
            self.forms[aid] = build_form(
                self.wf, None, outcome_var=f"{aid}_outcome", outcomes=outcomes, show=list(t.show)
            )
            self.routing[aid] = [f"{aid}_outcome", f"{aid}_comment"]


def _tasks(stage: Stage) -> list[Task]:
    return [
        t
        for item in stage.tasks
        for t in (item.parallel if isinstance(item, ParallelGroup) else [item])
    ]


def compile(wf: WorkflowV1, client_slug: str) -> Release:  # noqa: A001 - the spec's name
    """Compile a validated document for one client into a release."""
    if not SLUG.fullmatch(client_slug):
        raise CompileError(f"client slug {client_slug!r} must match {SLUG.pattern}")
    errors = validate(wf.model_dump(mode="json", by_alias=True, exclude_unset=True))
    if errors:
        raise CompileError("invalid workflow: " + "; ".join(str(e) for e in errors))
    c = _Compiler(wf, client_slug)
    bpmn: dict[str, bytes] = {}
    keys: dict[str, str] = {}
    for flow in wf.flows:
        main = c.main(flow)
        keys[flow.key] = main.pid
        bpmn[f"{main.pid}.bpmn"] = main.xml().encode()
        for stage in flow.stages:
            proc = c.stage(flow, stage)
            bpmn[f"{proc.pid}.bpmn"] = proc.xml().encode()
    groups = [
        Group(group_id(d.key, lv.n), f"{d.name} L{lv.n} · {lv.name}")
        for d in wf.org.departments
        for lv in sorted(d.levels, key=lambda lv: lv.n)
    ]
    return Release(
        bpmn=bpmn,
        forms=c.forms,
        ui=wf.apps.model_dump(mode="json", by_alias=True),
        groups=groups,
        routing_vars=c.routing,
        process_keys=keys,
    )
