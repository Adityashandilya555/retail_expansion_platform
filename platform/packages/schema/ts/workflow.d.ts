/* Generated from workflow.schema.json by json-schema-to-typescript. Do not edit: run `uv run zm-schema export && pnpm --filter @zm/schema gen`. */

/**
 * One client's `workflow.json`, schema version 1.
 */
export interface WorkflowV1 {
  schema_version: 1;
  org: Org;
  /**
   * @minItems 1
   */
  flows: [Flow, ...Flow[]];
  fields?: {
    [k: string]: FieldDef;
  };
  forms?: {
    [k: string]: Form;
  };
  apps?: Apps;
  kpis?: Kpi[];
}
export interface Org {
  /**
   * @minItems 1
   */
  departments: [Department, ...Department[]];
}
export interface Department {
  /**
   * Department key; Operaton candidate groups are derived as `<key>__L<n>`.
   */
  key: string;
  name: string;
  /**
   * Contiguous from 1 (L1 = most junior).
   *
   * @minItems 1
   */
  levels: [Level, ...Level[]];
}
export interface Level {
  n: number;
  name: string;
}
export interface Flow {
  key: string;
  name: string;
  /**
   * What one case is called, e.g. "Site".
   */
  case_label: string;
  case_code: CaseCode;
  start: Start;
  /**
   * @minItems 1
   */
  stages: [Stage, ...Stage[]];
}
export interface CaseCode {
  /**
   * e.g. `MX-{seq:4}`; must contain `{seq:N}`.
   */
  pattern: string;
}
export interface Start {
  /**
   * @minItems 1
   */
  performers: [
    LevelPerformer | InitiatorPerformer | FieldPerformer,
    ...(LevelPerformer | InitiatorPerformer | FieldPerformer)[]
  ];
  form: string;
}
export interface LevelPerformer {
  department: string;
  level: number;
  /**
   * Higher levels of the same department may also do the task.
   */
  or_above?: boolean;
}
export interface InitiatorPerformer {
  /**
   * The person who started the case.
   */
  initiator: true;
}
export interface FieldPerformer {
  /**
   * A `user`-type field filled earlier in the case.
   */
  from_field: string;
}
export interface Stage {
  key: string;
  name: string;
  department: string;
  /**
   * Stage keys of the same flow that must finish first; several = parallel join.
   */
  after?: string[];
  /**
   * @minItems 1
   */
  tasks: [Task | ParallelGroup, ...(Task | ParallelGroup)[]];
}
export interface Task {
  id: string;
  name: string;
  performer: LevelPerformer | InitiatorPerformer | FieldPerformer;
  form?: string | null;
  /**
   * Read-only fields from earlier steps shown on this task.
   */
  show?: string[];
  outcomes?: Outcome[];
  approval?: Approval | null;
  skip_if?: string | InitiatorAtLeastRule | null;
  route?: Route[];
}
export interface Outcome {
  id: string;
  label: string;
  /**
   * A task id in the same stage, `@close` (close the case) or `@end`; default: next step.
   */
  to?: string | null;
  comment_required?: boolean;
}
export interface Approval {
  /**
   * Approvers in order; each can approve, reject (if allowed) or send back.
   *
   * @minItems 1
   */
  chain: [DeptLevel, ...DeptLevel[]];
  send_back_to?: string | null;
  allow_reject?: boolean;
}
export interface DeptLevel {
  department: string;
  level: number;
}
export interface InitiatorAtLeastRule {
  initiator_at_least: DeptLevel;
}
export interface Route {
  when: string | InitiatorAtLeastRule;
  to: string;
  label: string;
}
export interface ParallelGroup {
  /**
   * @minItems 2
   */
  parallel: [Task, Task, ...Task[]];
}
export interface FieldDef {
  type:
    | "text"
    | "textarea"
    | "number"
    | "currency"
    | "percent"
    | "date"
    | "date_range"
    | "select"
    | "multi_select"
    | "yes_no"
    | "rating"
    | "user"
    | "address"
    | "checklist"
    | "line_items"
    | "computed"
    | "document"
    | "info";
  label: string;
  required?: boolean;
  options?: Option[] | null;
  document?: DocumentRule | null;
  /**
   * Release version that added the field (additive versioning, S2-25).
   */
  since?: number | null;
  /**
   * Hidden from new forms; stored values are kept.
   */
  hidden?: boolean;
}
export interface Option {
  id: string;
  label: string;
}
export interface DocumentRule {
  /**
   * Allowed file extensions, e.g. `["pdf", "jpg"]`.
   *
   * @minItems 1
   */
  accept: [string, ...string[]];
  max_mb?: number;
  keep_versions?: boolean;
}
export interface Form {
  /**
   * @minItems 1
   */
  sections: [Section, ...Section[]];
}
export interface Section {
  key: string;
  title: string;
  hidden_below_level?: DeptLevel | null;
  /**
   * @minItems 1
   */
  rows: [Cell[], ...Cell[][]];
}
export interface Cell {
  field: string;
  span?: number;
}
export interface Apps {
  /**
   * Theme preset key (D19).
   */
  theme?: string;
  accent?: string | null;
  nav?: NavItem[];
  pages?: Page[];
}
export interface NavItem {
  page: string;
  label: string;
  /**
   * Department keys, or `"*"` for everyone.
   *
   * @minItems 1
   */
  visible_to: [string, ...string[]];
}
export interface Page {
  key: string;
  layout: string;
  department?: string | null;
  /**
   * Required for the `case` layout.
   */
  flow?: string | null;
  regions?: {
    [k: string]: BlockRef[];
  };
}
export interface BlockRef {
  block: string;
  props?: {
    [k: string]: unknown;
  };
}
export interface Kpi {
  key: string;
  label: string;
  type: "duration" | "count";
  /**
   * Stage key the measurement starts at.
   */
  from?: string | null;
  to?: string | null;
}
