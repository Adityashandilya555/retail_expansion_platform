import { ajv } from "@zm/forms";
import { expect, test } from "vitest";
import { blocks, layouts, type BlockManifest } from "./registry";

// Manifest rules for every block (#30): a version (D14), props as valid JSON Schema that rejects
// unknown props, no styling props, and only regions some layout has.
const STYLING = ["color", "style", "className", "css"];
const REGIONS = new Set(Object.values(layouts).flatMap((l) => l.manifest.regions.map((r) => r.key)));

/** What's wrong with a block manifest; empty when nothing is. */
function problems(manifest: BlockManifest): string[] {
  const out: string[] = [];
  if (!Number.isInteger(manifest.version) || manifest.version < 1) out.push("version must be a positive integer");
  if (!manifest.description) out.push("description is empty");
  for (const region of manifest.regions) if (!REGIONS.has(region)) out.push(`no layout has region "${region}"`);
  try {
    ajv.compile(manifest.props);
  } catch (e) {
    out.push(`props is not valid JSON Schema: ${e instanceof Error ? e.message : String(e)}`);
  }
  const props = manifest.props as { type?: unknown; additionalProperties?: unknown; properties?: object };
  if (props.type !== "object" || props.additionalProperties !== false)
    out.push("props must be an object schema with additionalProperties: false");
  for (const name of Object.keys(props.properties ?? {})) if (STYLING.includes(name)) out.push(`styling prop "${name}"`);
  return out;
}

test("every registered block's manifest follows the rules", () => {
  const found = Object.entries(blocks).flatMap(([name, versions]) =>
    Object.entries(versions).map(([version, { manifest }]) => ({ name, version: Number(version), manifest })),
  );
  expect(found.map((f) => f.name)).toContain("task_inbox");
  for (const { name, version, manifest } of found) {
    expect([manifest.name, manifest.version], name).toEqual([name, version]);
    expect(problems(manifest), name).toEqual([]);
  }
});

test("a manifest with a styling prop, an invalid schema, no version or an unknown region fails", () => {
  const bad: BlockManifest = {
    name: "bad",
    version: 0,
    description: "",
    regions: ["sidebar"],
    props: { type: "object", properties: { color: { type: "string" }, size: { type: "huge" } } },
  };
  expect(problems(bad)).toEqual([
    "version must be a positive integer",
    "description is empty",
    'no layout has region "sidebar"',
    expect.stringContaining("props is not valid JSON Schema"),
    "props must be an object schema with additionalProperties: false",
    'styling prop "color"',
  ]);
});
