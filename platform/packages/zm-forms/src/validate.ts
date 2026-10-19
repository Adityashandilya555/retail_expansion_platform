// Ajv 2020 for compiled forms (#19). Paths match the API's (zm_schema.submission_errors) by the shared fixtures.
import { Ajv2020, type ErrorObject } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

export type FieldSchema = {
  type?: string;
  title?: string;
  format?: string;
  enum?: string[];
  readOnly?: boolean;
  "x-zm-type"?: string;
  "x-zm-options"?: { id: string; label: string }[];
  [keyword: string]: unknown;
};

/** A `Section` of workflow.json as compiled into `x-zm-layout` (the API may drop sections, so `rows` can be empty). */
export type FormSection = { key: string; title: string; rows: { field: string; span?: number }[][] };

/** A compiled form: `FormSpec.schema` from the compiler. */
export type FormSchema = {
  type: "object";
  properties: Record<string, FieldSchema>;
  required?: string[];
  additionalProperties?: boolean;
  "x-zm-layout"?: FormSection[];
  "x-zm-show"?: string[];
  [keyword: string]: unknown;
};

/** One error at a JSON Pointer: `/field`, `/field/sub`, or `""` for the whole form. A 422 from the API has the same shape. */
export type FieldError = { path: string; message: string };

/** Shared with @zm/blocks (manifest props). `strictTypes` off: compiled `then` branches carry `minLength` without `type`. */
export const ajv = new Ajv2020({ allErrors: true, strictTypes: false });
addFormats(ajv);
ajv.addVocabulary(["x-zm-type", "x-zm-options", "x-zm-layout", "x-zm-show", "x-zm-document"]);

const escape = (key: string) => key.replaceAll("~", "~0").replaceAll("/", "~1");

function toFieldError(e: ErrorObject): FieldError | null {
  const p = e.params as { missingProperty?: string; additionalProperty?: string; limit?: number };
  if (e.keyword === "if") return null; // the failing `then` branch is reported on its own
  if (e.keyword === "required") return { path: `${e.instancePath}/${escape(p.missingProperty ?? "")}`, message: "Required" };
  if (e.keyword === "additionalProperties")
    return { path: `${e.instancePath}/${escape(p.additionalProperty ?? "")}`, message: "Not a field of this form" };
  if (e.keyword === "minLength" && p.limit === 1) return { path: e.instancePath, message: "Required" };
  if (e.keyword === "format" && e.params.format === "date") return { path: e.instancePath, message: "Enter a valid date" };
  if (e.keyword === "enum") return { path: e.instancePath, message: "Choose one of the options" };
  return { path: e.instancePath, message: e.message ?? "Invalid" };
}

/** Errors sorted by path (code points, like Python's `sorted`), one per path. */
export function formErrors(schema: FormSchema, values: Record<string, unknown>): FieldError[] {
  const validate = ajv.compile(schema); // cached per schema object
  if (validate(values)) return [];
  const byPath = new Map<string, FieldError>();
  for (const e of validate.errors ?? []) {
    const f = toFieldError(e);
    if (f && !byPath.has(f.path)) byPath.set(f.path, f);
  }
  return [...byPath.values()].sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
}
