// Test fixtures for the renderer and the shell: a `GET /app/ui` answer and stand-in blocks.
// The real blocks arrive with S1-14 (task_inbox, #30) and S1-25 (case_*, #92).
import type { AppUi, BlockEntry, BlockManifest, BlockRegistry } from "../registry";

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

/** Stand-ins with the real manifests' regions and props (task_inbox as specified in #30). */
export const testBlocks: BlockRegistry = {
  task_inbox: {
    1: stub("task_inbox", ["main", "aside"], {
      scope: { enum: ["mine", "team"], default: "mine" },
      flow: { type: "string" },
      group_by: { enum: ["case", "none"], default: "case" },
    }),
  },
  case_table: { 1: stub("case_table", ["main"], { flow: { type: "string" }, stages: { type: "array" } }) },
  case_header: { 1: stub("case_header", ["header"], { title_field: { type: "string" } }) },
  case_fields: { 1: stub("case_fields", ["overview"]) },
  case_tasks: { 1: stub("case_tasks", ["tasks"]) },
  kpi_tile: { 1: stub("kpi_tile", ["hero"]) },
};
