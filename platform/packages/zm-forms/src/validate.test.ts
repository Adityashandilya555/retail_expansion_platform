import { expect, test } from "vitest";
import { ajv, formErrors, type FormSchema } from "./validate";

// Shared with the API: packages/schema/tests/test_form_fixtures.py runs the same cases (#28).
type Case = { name: string; values: Record<string, unknown>; paths: string[] };
const DIR = "../../schema/tests/fixtures/";
const schemas = import.meta.glob<FormSchema>("../../schema/tests/fixtures/*.schema.json", { eager: true, import: "default" });
const caseFiles = import.meta.glob<Case[]>("../../schema/tests/fixtures/*.cases.json", { eager: true, import: "default" });

const cases = Object.entries(caseFiles).flatMap(([path, list]) => {
  const form = path.slice(DIR.length, -".cases.json".length);
  const schema = schemas[`${DIR}${form}.schema.json`];
  if (!schema) throw new Error(`missing ${form}.schema.json`);
  return list.map((c) => ({ ...c, schema, label: `${form}: ${c.name}` }));
});

test("every fixture has cases", () => {
  expect(cases.length).toBeGreaterThanOrEqual(15);
});

test.each(cases)("$label", ({ schema, values, paths }) => {
  const errors = formErrors(schema, values);
  expect(errors.map((e) => e.path)).toEqual(paths);
  expect(errors.every((e) => e.message)).toBe(true);
});

test("the exported Ajv knows the x-zm vocabulary and checks formats", () => {
  const date = { type: "string", format: "date", "x-zm-type": "date", "x-zm-options": [] };
  expect(ajv.validate(date, "2026-02-28")).toBe(true);
  expect(ajv.validate(date, "2026-02-30")).toBe(false);
});

test("pointers escape ~ and /", () => {
  const schema: FormSchema = { type: "object", properties: {}, additionalProperties: false };
  expect(formErrors(schema, { "a/b~c": 1 }).map((e) => e.path)).toEqual(["/a~1b~0c"]);
});
