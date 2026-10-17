"""BPMN 2.0 XML for one process, ported from the prototype's `Proc`
(platform/prototype/matrix.py:160-334).

Changes from the prototype (#19): no `historyTimeToLive` (D7); form keys are plain `zm:<id>`
strings, never expressions; a user task takes either an assignee expression or a comma list of
candidate groups; several listeners can be set on an inline end event. Output is deterministic:
nodes and flows keep insertion order and coordinates are formatted with fixed precision.
"""

from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape, quoteattr

NAMESPACE = "https://z-matrix/workflow"


@dataclass
class End:
    """An end event created inline as a flow target."""

    name: str
    error: str | None = None
    terminate: bool = False
    sets: list[tuple[str, str]] = field(default_factory=list)
    """Variables set by a start listener on the end event, in order."""


def set_var(name: str, value: str) -> str:
    escaped = str(value).replace("'", "\\'")
    return f"${{execution.setVariable('{name}', '{escaped}')}}"


class Proc:
    SIZE = {
        "task": (110, 80),
        "call": (120, 80),
        "xor": (50, 50),
        "par": (50, 50),
        "start": (36, 36),
        "end": (36, 36),
        "boundary": (36, 36),
    }

    def __init__(
        self, pid: str, name: str, starters: list[str] | None = None, startable: bool = True
    ) -> None:
        self.pid, self.name, self.starters, self.startable = pid, name, starters, startable
        self.nodes: dict[str, dict[str, Any]] = {}
        self.flows: list[dict[str, Any]] = []

    def node(self, id_: str, kind: str, name: str = "", **kw: Any) -> str:
        if id_ in self.nodes:
            raise ValueError(f"duplicate BPMN node id {id_!r} in {self.pid}")
        if kw.get("form") and "${" in kw["form"]:
            raise ValueError(f"form key {kw['form']!r} must not contain an expression")
        self.nodes[id_] = dict(kind=kind, name=name, **kw)
        return id_

    def flow(
        self, src: str, tgt: str | End, cond: str | None = None, name: str = "", hint: str = ""
    ) -> None:
        if isinstance(tgt, End):
            listeners = [("start", set_var(k, v)) for k, v in tgt.sets]
            tgt = self.node(
                (hint or src) + "_end",
                "end",
                tgt.name,
                error=tgt.error,
                terminate=tgt.terminate,
                listeners=listeners,
            )
        self.flows.append(
            dict(id=f"f{len(self.flows) + 1}", src=src, tgt=tgt, cond=cond, name=name)
        )

    def xml(self) -> str:
        for f in self.flows:
            if f["src"] not in self.nodes or f["tgt"] not in self.nodes:
                raise ValueError(f"dangling flow {f} in {self.pid}")
        errors = sorted({n["error"] for n in self.nodes.values() if n.get("error")})
        body = [f'<bpmn:error id="Error_{c}" name="{c}" errorCode="{c}" />' for c in errors]
        attrs = f'id="{self.pid}" name={quoteattr(self.name)} isExecutable="true"'
        if self.starters:
            attrs += f' operaton:candidateStarterGroups="{",".join(self.starters)}"'
        if not self.startable:
            attrs += ' operaton:isStartableInTasklist="false"'
        body.append(f"<bpmn:process {attrs}>")
        body += [self._node_xml(i, n) for i, n in self.nodes.items()]
        for f in self.flows:
            a = f'id="{f["id"]}" sourceRef="{f["src"]}" targetRef="{f["tgt"]}"'
            if f["name"]:
                a += f" name={quoteattr(f['name'])}"
            if f["cond"]:
                body.append(
                    f"<bpmn:sequenceFlow {a}>"
                    '<bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">'
                    f"{escape(f['cond'])}</bpmn:conditionExpression></bpmn:sequenceFlow>"
                )
            else:
                body.append(f"<bpmn:sequenceFlow {a} />")
        body += ["</bpmn:process>", self._di()]
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
            'xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" '
            'xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" '
            'xmlns:di="http://www.omg.org/spec/DD/20100524/DI" '
            'xmlns:operaton="http://operaton.org/schema/1.0/bpmn" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            f'id="defs_{self.pid}" targetNamespace="{NAMESPACE}">\n'
            + "\n".join(body)
            + "\n</bpmn:definitions>\n"
        )

    @staticmethod
    def _ext(n: dict[str, Any]) -> str:
        inner = "".join(
            f'<operaton:executionListener event="{ev}" expression={quoteattr(ex)} />'
            for ev, ex in n.get("listeners", [])
        )
        if n["kind"] == "call":
            inner += (
                '<operaton:in businessKey="#{execution.processBusinessKey}" />'
                '<operaton:in variables="all" /><operaton:out variables="all" />'
            )
        return f"<bpmn:extensionElements>{inner}</bpmn:extensionElements>" if inner else ""

    def _node_xml(self, id_: str, n: dict[str, Any]) -> str:
        name = f" name={quoteattr(n['name'])}" if n["name"] else ""
        k, ext = n["kind"], self._ext(n)
        if k == "start":
            form = f' operaton:formKey="{n["form"]}"' if n.get("form") else ""
            init = ' operaton:initiator="initiator"' if n.get("initiator") else ""
            return f'<bpmn:startEvent id="{id_}"{name}{form}{init}>{ext}</bpmn:startEvent>'
        if k == "end":
            inner = (
                f'<bpmn:errorEventDefinition errorRef="Error_{n["error"]}" />'
                if n.get("error")
                else "<bpmn:terminateEventDefinition />"
                if n.get("terminate")
                else ""
            )
            return f'<bpmn:endEvent id="{id_}"{name}>{ext}{inner}</bpmn:endEvent>'
        if k == "task":
            who = (
                f" operaton:assignee={quoteattr(n['assignee'])}"
                if n.get("assignee")
                else f' operaton:candidateGroups="{n["groups"]}"'
            )
            return (
                f'<bpmn:userTask id="{id_}"{name} operaton:formKey="{n["form"]}"{who}>'
                f"{ext}</bpmn:userTask>"
            )
        if k == "xor":
            return f'<bpmn:exclusiveGateway id="{id_}"{name} />'
        if k == "par":
            return f'<bpmn:parallelGateway id="{id_}"{name} />'
        if k == "call":
            return (
                f'<bpmn:callActivity id="{id_}"{name} calledElement="{n["called"]}" '
                f'operaton:calledElementBinding="deployment">{ext}</bpmn:callActivity>'
            )
        if k == "boundary":
            return (
                f'<bpmn:boundaryEvent id="{id_}"{name} attachedToRef="{n["host"]}">'
                f'<bpmn:errorEventDefinition errorRef="Error_{n["error"]}" /></bpmn:boundaryEvent>'
            )
        raise ValueError(k)

    # auto layout: column = longest path from start, rows by branch order, loops drawn over the top
    def _layout(self) -> tuple[dict[str, int], dict[str, int], set[tuple[str, str]]]:
        succ: dict[str, list[str]] = {i: [] for i in self.nodes}
        for f in self.flows:
            succ[f["src"]].append(f["tgt"])
        back: set[tuple[str, str]] = set()
        state: dict[str, int] = {}
        order = list(self.nodes)

        for root in sorted(order, key=lambda i: self.nodes[i]["kind"] != "start"):
            if root in state:
                continue
            stack = [(root, iter(succ[root]))]
            state[root] = 1
            while stack:  # iterative DFS (large stages must not hit the recursion limit)
                u, it = stack[-1]
                v = next(it, None)
                if v is None:
                    state[u] = 2
                    stack.pop()
                elif state.get(v) == 1:
                    back.add((u, v))
                elif v not in state:
                    state[v] = 1
                    stack.append((v, iter(succ[v])))
        fwd = [(f["src"], f["tgt"]) for f in self.flows if (f["src"], f["tgt"]) not in back]
        col = dict.fromkeys(order, 0)
        for _ in order:
            for u, v in fwd:
                col[v] = max(col[v], col[u] + 1)
            for i, n in self.nodes.items():
                if n["kind"] == "boundary":
                    col[i] = max(col[i], col[n["host"]])
        row: dict[str, int] = {}
        used: set[tuple[int, int]] = set()

        def place(i: str, pref: int) -> None:
            r = pref
            while (col[i], r) in used:
                r += 1
            row[i] = r
            used.add((col[i], r))

        for u in sorted(order, key=lambda i: (col[i], order.index(i))):
            n = self.nodes[u]
            if n["kind"] == "boundary":
                row[u] = row[n["host"]]
            elif u not in row:
                place(u, 0)
            for k, v in enumerate([v for (a, v) in fwd if a == u]):
                if v not in row:
                    place(v, row[u] + (1 if n["kind"] == "boundary" else k))
        return col, row, back

    def _di(self) -> str:
        col, row, back = self._layout()
        box = {}
        for i, n in self.nodes.items():
            w, h = self.SIZE[n["kind"]]
            cx, cy = 120 + col[i] * 165, 120 + row[i] * 140
            if n["kind"] == "boundary":
                cx, cy = cx + 20, cy + 40
            box[i] = (cx - w / 2, cy - h / 2, w, h)
        out = [
            f'<bpmndi:BPMNDiagram id="dia_{self.pid}"><bpmndi:BPMNPlane id="plane_{self.pid}" '
            f'bpmnElement="{self.pid}">'
        ]
        for i, (x, y, w, h) in box.items():
            out.append(
                f'<bpmndi:BPMNShape id="{i}_di" bpmnElement="{i}"><dc:Bounds x="{x:.0f}" '
                f'y="{y:.0f}" width="{w}" height="{h}" /></bpmndi:BPMNShape>'
            )
        loops = 0
        for f in self.flows:
            sx, sy, sw, sh = box[f["src"]]
            tx, ty, tw, th = box[f["tgt"]]
            scx, scy, tcx, tcy = sx + sw / 2, sy + sh / 2, tx + tw / 2, ty + th / 2
            if (f["src"], f["tgt"]) in back:
                top = min(scy, tcy) - 58 - 8 * (loops % 4)
                loops += 1
                pts = [(scx, sy), (scx, top), (tcx, top), (tcx, ty)]
            elif abs(scy - tcy) < 1:
                pts = [(sx + sw, scy), (tx, tcy)]
            elif tcy > scy:
                pts = [(scx, sy + sh), (scx, tcy), (tx, tcy)]
            else:
                pts = [(sx + sw, scy), (tcx, scy), (tcx, ty + th)]
            wps = "".join(f'<di:waypoint x="{x:.0f}" y="{y:.0f}" />' for x, y in pts)
            edge = f'<bpmndi:BPMNEdge id="{f["id"]}_di" bpmnElement="{f["id"]}">'
            out.append(f"{edge}{wps}</bpmndi:BPMNEdge>")
        out.append("</bpmndi:BPMNPlane></bpmndi:BPMNDiagram>")
        return "\n".join(out)
