"""Cross-reference rules of `workflow.json` v1 that JSON Schema can't express (#16).

`semantic_errors` collects every problem (it doesn't stop at the first), each with a path such as
`flows[0].stages[2].tasks[0].outcomes[1].to`, so the agent and the owner console can point at it.
"""

import re
from collections.abc import Iterator

from zm_schema.errors import Error, join
from zm_schema.models import (
    DeptLevel,
    FieldPerformer,
    InitiatorAtLeastRule,
    LevelPerformer,
    ParallelGroup,
    Stage,
    Task,
    WorkflowV1,
)

FIELD_KEY = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
# Operaton group ids, widened by our image (S1-02, platform/infra/operaton/default.yml).
GROUP_ID = re.compile(r"^[a-zA-Z0-9_-]+$")
TERMINALS = {"@close", "@end"}


def group_id(department: str, level: int) -> str:
    """Operaton candidate group for a department level, e.g. `legal__L2`."""
    return f"{department}__L{level}"


# Exactly the ids `group_id` makes for a valid org: a department key Operaton accepts (1-40 chars)
# and a level from 1. `zm_api.users.mirror` manages only these, never other groups a user holds.
LEVEL_GROUP = re.compile(r"^[a-zA-Z0-9_-]{1,40}__L[1-9][0-9]*$")


def stage_tasks(stage: Stage, base: str) -> Iterator[tuple[Task, str, bool]]:
    """(task, path, in_parallel_group) for every task of a stage, in order."""
    for i, item in enumerate(stage.tasks):
        if isinstance(item, ParallelGroup):
            for j, t in enumerate(item.parallel):
                yield t, join(base, "tasks", i, "parallel", j), True
        else:
            yield item, join(base, "tasks", i), False


def ancestors(graph: dict[str, set[str]], key: str) -> set[str]:
    """Every stage that must finish before `key` (transitively through `after`)."""
    seen: set[str] = set()
    todo = list(graph.get(key, set()))
    while todo:
        a = todo.pop()
        if a not in seen:
            seen.add(a)
            todo.extend(graph.get(a, set()))
    return seen


def semantic_errors(doc: WorkflowV1) -> list[Error]:
    errs: list[Error] = []
    err = lambda path, msg: errs.append(Error(path, msg))  # noqa: E731

    # ── org ──
    levels: dict[str, set[int]] = {}
    for i, d in enumerate(doc.org.departments):
        p = join("org.departments", i)
        if d.key in levels:
            err(join(p, "key"), f"duplicate department key '{d.key}'")
            continue
        ns = [lv.n for lv in d.levels]
        if sorted(ns) != list(range(1, len(ns) + 1)):
            err(join(p, "levels"), f"levels must be numbered 1..{len(ns)} without gaps, got {ns}")
        bad = [g for g in (group_id(d.key, n) for n in ns) if not GROUP_ID.fullmatch(g)]
        if bad:
            err(join(p, "key"), f"group id '{bad[0]}' is not allowed by Operaton ([a-zA-Z0-9_-]+)")
        levels[d.key] = set(ns)

    def check_dept_level(dl: DeptLevel | LevelPerformer, path: str) -> None:
        if dl.department not in levels:
            err(join(path, "department"), f"unknown department '{dl.department}'")
        elif dl.level not in levels[dl.department]:
            err(join(path, "level"), f"department '{dl.department}' has no level {dl.level}")

    # ── fields and forms ──
    for key, f in doc.fields.items():
        p = join("fields", key)
        if not FIELD_KEY.fullmatch(key):
            err(p, f"field key '{key}' must match {FIELD_KEY.pattern}")
        if f.type in {"select", "multi_select"} and not f.options:
            err(join(p, "options"), f"a {f.type} field needs options")
        ids = [o.id for o in f.options or []]
        dupes = sorted({x for x in ids if ids.count(x) > 1})
        if dupes:
            err(join(p, "options"), f"duplicate option id '{dupes[0]}'")
        if f.type == "document" and f.document is None:
            err(join(p, "document"), "a document field needs a document rule (accept, max_mb)")

    form_fields: dict[str, set[str]] = {}
    for fid, form in doc.forms.items():
        used: set[str] = set()
        for si, s in enumerate(form.sections):
            sp = join("forms", fid, "sections", si)
            if s.hidden_below_level:
                check_dept_level(s.hidden_below_level, join(sp, "hidden_below_level"))
            for ri, row in enumerate(s.rows):
                for ci, cell in enumerate(row):
                    if cell.field not in doc.fields:
                        err(join(sp, "rows", ri, ci, "field"), f"unknown field '{cell.field}'")
                    used.add(cell.field)
                if sum(c.span for c in row) > 12:
                    err(join(sp, "rows", ri), "a row spans more than 12 columns")
        form_fields[fid] = used

    def check_form(form: str | None, path: str) -> set[str]:
        if form is None:
            return set()
        if form not in doc.forms:
            err(path, f"unknown form '{form}'")
            return set()
        return form_fields[form]

    def check_performer(perf: object, path: str) -> None:
        if isinstance(perf, LevelPerformer):
            check_dept_level(perf, path)
        elif isinstance(perf, FieldPerformer):
            f = doc.fields.get(perf.from_field)
            if f is None or f.type != "user":
                err(join(path, "from_field"), f"'{perf.from_field}' is not a user-type field")

    def check_rule(rule: object, path: str) -> None:
        if isinstance(rule, InitiatorAtLeastRule):
            check_dept_level(rule.initiator_at_least, join(path, "initiator_at_least"))

    # ── flows ──
    flow_keys: set[str] = set()
    task_seen: dict[str, str] = {}
    stage_keys_all: set[str] = set()
    for fi, flow in enumerate(doc.flows):
        fp = join("flows", fi)
        if flow.key in flow_keys:
            err(join(fp, "key"), f"duplicate flow key '{flow.key}'")
        flow_keys.add(flow.key)
        for pi, perf in enumerate(flow.start.performers):
            check_performer(perf, join(fp, "start.performers", pi))
        start_fields = check_form(flow.start.form, join(fp, "start.form"))

        keys = [s.key for s in flow.stages]
        stage_keys_all.update(keys)
        filled_by_stage: dict[str, set[str]] = {}
        for si, stage in enumerate(flow.stages):
            sp = join(fp, "stages", si)
            if keys.index(stage.key) != si:
                err(join(sp, "key"), f"duplicate stage key '{stage.key}' in flow '{flow.key}'")
            if stage.department not in levels:
                err(join(sp, "department"), f"unknown department '{stage.department}'")
            for ai, a in enumerate(stage.after):
                if a not in keys:
                    err(join(sp, "after", ai), f"unknown stage '{a}' in flow '{flow.key}'")

            tasks = list(stage_tasks(stage, sp))
            in_stage = {t.id for t, _, _ in tasks}
            in_parallel = {t.id for t, _, par in tasks if par}
            for t, tp, par in tasks:
                if t.id in task_seen:
                    err(join(tp, "id"), f"task id '{t.id}' already used at {task_seen[t.id]}")
                else:
                    task_seen[t.id] = join(tp, "id")
                check_performer(t.performer, join(tp, "performer"))
                filled = check_form(t.form, join(tp, "form"))
                filled_by_stage.setdefault(stage.key, set()).update(filled)

                targets = [
                    (o.to, join(tp, "outcomes", oi, "to")) for oi, o in enumerate(t.outcomes)
                ]
                targets += [(r.to, join(tp, "route", ri, "to")) for ri, r in enumerate(t.route)]
                if t.approval and t.approval.send_back_to:
                    targets.append((t.approval.send_back_to, join(tp, "approval.send_back_to")))
                if par and (t.outcomes or t.route or t.approval):
                    err(tp, "tasks inside a parallel group cannot branch or approve")
                for to, path in targets:
                    if to is None or (to in TERMINALS and "outcomes" in path):
                        continue
                    if to not in in_stage:
                        err(path, f"'{to}' is not a task in stage '{stage.key}'")
                    elif to in in_parallel:
                        err(path, f"'{to}' is inside a parallel group and can't be a branch target")
                if t.approval:
                    for ci, link in enumerate(t.approval.chain):
                        check_dept_level(link, join(tp, "approval.chain", ci))
                for ri, r in enumerate(t.route):
                    check_rule(r.when, join(tp, "route", ri, "when"))
                if t.skip_if is not None:
                    check_rule(t.skip_if, join(tp, "skip_if"))

        # stage order: no cycles
        graph = {s.key: set(s.after) & set(keys) for s in flow.stages}
        pending = {k: set(v) for k, v in graph.items()}
        while pending:
            ready = [k for k, deps in pending.items() if not deps]
            if not ready:
                err(join(fp, "stages"), f"stage order has a cycle among {sorted(pending)}")
                break
            for k in ready:
                del pending[k]
            for deps in pending.values():
                deps.difference_update(ready)

        # `show`: only fields filled earlier (start form, ancestor stages, earlier tasks here)
        for si, stage in enumerate(flow.stages):
            sp = join(fp, "stages", si)
            before = set(start_fields)
            for a in ancestors(graph, stage.key):
                before |= filled_by_stage.get(a, set())
            for i, item in enumerate(stage.tasks):
                group = item.parallel if isinstance(item, ParallelGroup) else [item]
                group_filled: set[str] = set()
                for j, t in enumerate(group):
                    tp = (
                        join(sp, "tasks", i, "parallel", j)
                        if isinstance(item, ParallelGroup)
                        else join(sp, "tasks", i)
                    )
                    for shi, key in enumerate(t.show):
                        if key not in doc.fields:
                            err(join(tp, "show", shi), f"unknown field '{key}'")
                        elif key not in before:
                            err(
                                join(tp, "show", shi),
                                f"field '{key}' is not filled by an earlier step",
                            )
                    group_filled |= form_fields.get(t.form, set()) if t.form else set()
                before |= group_filled

    # ── apps ──
    page_keys: set[str] = set()
    for pi, page in enumerate(doc.apps.pages):
        pp = join("apps.pages", pi)
        if page.key in page_keys:
            err(join(pp, "key"), f"duplicate page key '{page.key}'")
        page_keys.add(page.key)
        if page.department is not None and page.department not in levels:
            err(join(pp, "department"), f"unknown department '{page.department}'")
        if page.layout == "case" and page.flow not in flow_keys:
            err(
                join(pp, "flow"),
                "a page with layout 'case' must set flow to one of the document's flows",
            )
        elif page.flow is not None and page.flow not in flow_keys:
            err(join(pp, "flow"), f"unknown flow '{page.flow}'")
    for ni, nav in enumerate(doc.apps.nav):
        np_ = join("apps.nav", ni)
        if nav.page not in page_keys:
            err(join(np_, "page"), f"unknown page '{nav.page}'")
        for vi, v in enumerate(nav.visible_to):
            if v != "*" and v not in levels:
                err(join(np_, "visible_to", vi), f"unknown department '{v}'")

    # ── kpis ──
    kpi_keys: set[str] = set()
    for ki, k in enumerate(doc.kpis):
        kp = join("kpis", ki)
        if k.key in kpi_keys:
            err(join(kp, "key"), f"duplicate kpi key '{k.key}'")
        kpi_keys.add(k.key)
        for name, value in (("from", k.from_), ("to", k.to)):
            if value is not None and value not in stage_keys_all:
                err(join(kp, name), f"unknown stage '{value}'")

    return errs
