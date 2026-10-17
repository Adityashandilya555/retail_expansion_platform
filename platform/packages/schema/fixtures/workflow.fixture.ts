// Type-checks the generated types against a two-flow document (`pnpm typecheck`, #16).
import type { Task, WorkflowV1 } from "@zm/schema";

type Performer = Task["performer"];

const review: Task = {
  id: "lg_dd",
  name: "Due diligence",
  performer: { department: "legal", level: 1, or_above: false },
  show: ["monthly_rent"],
  outcomes: [{ id: "back", label: "Send back", to: "lg_dd", comment_required: true }],
  route: [],
};

const initiator: Performer = { initiator: true };

export const twoFlows: WorkflowV1 = {
  schema_version: 1,
  org: {
    departments: [
      { key: "bd", name: "Business development", levels: [{ n: 1, name: "BD Executive" }] },
      { key: "legal", name: "Legal", levels: [{ n: 1, name: "Legal Executive" }] },
    ],
  },
  flows: [
    {
      key: "site_launch",
      name: "Site launch",
      case_label: "Site",
      case_code: { pattern: "MX-{seq:4}" },
      start: { performers: [{ department: "bd", level: 1 }], form: "site_start" },
      stages: [
        { key: "legal_dd", name: "Legal", department: "legal", after: [], tasks: [review] },
      ],
    },
    {
      key: "store_closure",
      name: "Store closure",
      case_label: "Closure",
      case_code: { pattern: "CL-{seq:3}" },
      start: { performers: [initiator], form: "site_start" },
      stages: [
        {
          key: "closure",
          name: "Closure",
          department: "legal",
          tasks: [
            {
              parallel: [
                { id: "cl_a", name: "A", performer: initiator },
                { id: "cl_b", name: "B", performer: { from_field: "owner" } },
              ],
            },
          ],
        },
      ],
    },
  ],
  fields: {
    monthly_rent: { type: "currency", label: "Monthly rent" },
    owner: { type: "user", label: "Owner" },
  },
  forms: {
    site_start: { sections: [{ key: "site", title: "Site", rows: [[{ field: "monthly_rent", span: 6 }]] }] },
  },
  apps: { theme: "quartz", nav: [], pages: [] },
  kpis: [{ key: "loi_to_legal", label: "Days", type: "duration", from: "legal_dd" }],
};
