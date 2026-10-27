// Fixtures for the renderer, the shell, the blocks' tests and /dev/layouts: a `GET /app/ui` answer,
// stand-in blocks (so the shell's tests need no API), the Sprint 1 demo's sites, and an API client
// that answers from fixtures.
import { createApiClient, type ApiClient, type components } from "@zm/api-client";
import { sampleDdChecklist } from "@zm/forms/samples";
import caseFieldsManifest from "../../blocks/case_fields/manifest.json";
import caseHeaderManifest from "../../blocks/case_header/manifest.json";
import caseTableManifest from "../../blocks/case_table/manifest.json";
import caseTasksManifest from "../../blocks/case_tasks/manifest.json";
import taskInboxManifest from "../../blocks/task_inbox/manifest.json";
import type { AppUi, BlockEntry, BlockManifest, BlockRegistry } from "../registry";

type CaseRow = components["schemas"]["CaseRow"];
type CaseDetail = components["schemas"]["CaseDetail"];
type StageOut = components["schemas"]["StageOut"];
type TaskDetail = components["schemas"]["TaskDetail"];

/** Legal L1's `GET /app/ui` on release v1 (the API has already dropped other departments' pages). */
export const sampleUi: AppUi = {
  client: { name: "Matrix Retail", theme: "quartz", accent: "teal" },
  release: { version: 1 },
  nav: [
    { page: "home", label: "Home", visible_to: ["*"] },
    { page: "legal", label: "Legal", visible_to: ["legal", "management"] },
  ],
  pages: [
    { key: "home", layout: "standard", regions: { main: [{ block: "task_inbox", props: { scope: "mine" } }] } },
    {
      key: "legal",
      layout: "standard",
      department: "legal",
      regions: {
        main: [
          { block: "task_inbox", props: { scope: "team" } },
          { block: "case_table", props: { stages: ["legal"] } },
        ],
      },
    },
    {
      key: "site",
      layout: "case",
      flow: "store_launch",
      regions: {
        header: [{ block: "case_header", props: { title_field: "site_name" } }],
        overview: [{ block: "case_fields", props: {} }],
        tasks: [{ block: "case_tasks", props: {} }],
      },
    },
  ],
  flows: [
    {
      key: "store_launch",
      name: "Store launch",
      case_label: "Site",
      case_code: "MX-{seq:4}",
      stages: [
        { key: "bd_qualification", name: "BD qualification", department: "bd" },
        { key: "legal", name: "Legal", department: "legal" },
        { key: "design", name: "Design", department: "design" },
      ],
      start_fields: [
        { key: "site_name", label: "Site name", type: "text" },
        { key: "city", label: "City", type: "text" },
      ],
    },
  ],
};

function stub(name: string, regions: string[], properties: Record<string, unknown> = {}): BlockEntry {
  const manifest: BlockManifest = {
    name,
    version: 1,
    description: `Stand-in for ${name}`,
    regions,
    props: { type: "object", additionalProperties: false, properties },
  };
  return {
    manifest,
    Block: ({ props }) => (
      <section aria-label={name}>
        {name} {JSON.stringify(props)}
      </section>
    ),
  };
}

const standIn = (manifest: BlockManifest): BlockEntry => ({ ...stub(manifest.name, []), manifest });

/** Stand-ins with the real blocks' manifests (and one block that doesn't exist yet). */
export const testBlocks: BlockRegistry = {
  task_inbox: { 1: standIn(taskInboxManifest) },
  case_table: { 1: standIn(caseTableManifest) },
  case_header: { 1: standIn(caseHeaderManifest) },
  case_fields: { 1: standIn(caseFieldsManifest) },
  case_tasks: { 1: standIn(caseTasksManifest) },
  kpi_tile: { 1: stub("kpi_tile", ["hero"]) },
};

// --- the Sprint 1 demo's sites (#31): one at its first stage, one at Legal, one past Legal -----

const STAGES = sampleUi.flows[0]?.stages ?? [];
const AT = "2026-10-20T09:00:00Z";

/** The flow's stages in the given states, in order. */
function stages(...states: StageOut["state"][]): StageOut[] {
  return STAGES.map((s, i) => {
    const state = states[i] ?? "queued";
    return { ...s, state, started_at: state === "queued" ? null : AT, ended_at: state === "done" ? AT : null };
  });
}

function row(code: string, site: string, city: string, states: StageOut["state"][], over: Partial<CaseRow> = {}): CaseRow {
  return {
    code,
    flow: "store_launch",
    title: site,
    status: "open",
    stages: stages(...states),
    fields: { site_name: site, city },
    open_tasks: 1,
    my_tasks: 0,
    created_at: AT,
    release_version: 1,
    ...over,
  };
}

export const sampleCases: CaseRow[] = [
  row("MX-0001", "Indiranagar", "Bengaluru", ["done", "open"], { my_tasks: 1 }),
  row("MX-0002", "Koramangala", "Bengaluru", ["open"]),
  row("MX-0003", "Bandra West", "Mumbai", ["done", "done", "open"]),
];

/** MX-0001 as Legal L1 sees it: at Legal, with the DD checklist to fill. */
export function sampleCase(over: Partial<CaseDetail> = {}): CaseDetail {
  const [first] = sampleCases;
  if (!first) throw new Error("no sample case");
  return {
    ...first,
    sections: [
      {
        key: "site",
        title: "Site",
        fields: [
          { key: "site_name", label: "Site name", type: "text", value: "Indiranagar" },
          { key: "city", label: "City", type: "text", value: "Bengaluru" },
        ],
      },
    ],
    tasks: [{ id: "t-dd", name: "Fill DD checklist", stage: "legal", assignee: null, can_act: true }],
    history: [
      { task: "bd_send_legal", task_name: "Send to Legal", by: "u-bd2", by_name: "BD Manager", outcome: null, comment: null, at: AT },
    ],
    ...over,
  };
}

/** `GET /app/tasks/t-dd`: the DD checklist (real compiler output), showing the signed LOI. */
export const sampleTask: TaskDetail = {
  task: {
    id: "t-dd",
    name: "Fill DD checklist",
    flow: "store_launch",
    case_code: "MX-0001",
    stage: "legal",
    created: AT,
    assignee: null,
    form_key: "zm:legal_dd_checklist",
  },
  case: {
    id: "c-1",
    code: "MX-0001",
    flow: "store_launch",
    release_version: 1,
    process_instance_id: "pi-1",
    created_at: AT,
  },
  editable: true,
  form: { schema: sampleDdChecklist, layout: sampleDdChecklist["x-zm-layout"] ?? [] },
  show: { loi_file: { name: "loi-mx-0001.pdf" } },
  show_labels: { loi_file: "Signed LOI" },
};

// --- an API that answers from fixtures --------------------------------------------------------

type Handler = (request: Request) => unknown;

export const problem = (status: number, errors: { path: string; message: string }[] | null = null) =>
  Response.json({ type: "about:blank", title: "x", status, detail: "x", request_id: "r", errors }, { status });

/** A client whose requests are answered by `routes` ("METHOD /path" → a body, or a Response), never
 * the network; unknown routes are 404. `calls` keeps every request, e.g. to read its headers. */
export function fixtureApi(routes: Record<string, Handler>): { api: ApiClient; calls: Request[] } {
  const calls: Request[] = [];
  const api = createApiClient("http://fixtures.test");
  api.use({
    async onRequest({ request }) {
      calls.push(request.clone());
      const handler = routes[`${request.method} ${new URL(request.url).pathname}`];
      if (!handler) return problem(404);
      const out = await handler(request);
      return out instanceof Response ? out : Response.json(out);
    },
  });
  return { api, calls };
}
