import { ajv } from "@zm/forms";
import { expect, test } from "vitest";
import meta from "../manifest.schema.json";
import { blocks, layouts, type BlockManifest, type BlockRegistry } from "./registry";
import { testBlocks } from "./test";

// Manifest rules (#30, #37): every manifest passes the meta-schema (a version, a description, region
// rules, props as an object schema that rejects unknown props, no styling props), its props compile
// as JSON Schema, a block names only regions some layout has, no two layouts share a region key, and
// the registry and the manifest files match one to one.
const validBlock = ajv.compile({ ...meta, $ref: "#/$defs/block" });
const validLayout = ajv.compile({ ...meta, $ref: "#/$defs/layout" });
const REGION_KEYS = Object.values(layouts).flatMap((l) => l.manifest.regions.map((r) => r.key));
const REGIONS = new Set(REGION_KEYS);

type Named = { name: string; version: number };
const blockFiles = import.meta.glob<Named>("../blocks/*/manifest.json", { eager: true, import: "default" });
const layoutFiles = import.meta.glob<Named>("../layouts/*/manifest.json", { eager: true, import: "default" });

/** [folder, name, version] of each manifest file, sorted. */
const files = (found: Record<string, Named>) =>
  Object.entries(found)
    .map(([path, m]) => [path.split("/").at(-2), m.name, m.version])
    .sort();
/** [key, manifest name, version] of each registry entry, sorted. */
const registeredBlocks = (registry: BlockRegistry) =>
  Object.entries(registry)
    .flatMap(([name, versions]) => Object.entries(versions).map(([v, { manifest }]) => [name, manifest.name, Number(v)]))
    .sort();

/** What's wrong with a block manifest; empty when nothing is. */
function problems(manifest: BlockManifest): string[] {
  const out = validBlock(manifest) ? [] : (validBlock.errors ?? []).map((e) => `manifest${e.instancePath} ${e.message}`);
  for (const region of manifest.regions) if (!REGIONS.has(region)) out.push(`no layout has region "${region}"`);
  try {
    ajv.compile(manifest.props);
  } catch (e) {
    out.push(`props is not valid JSON Schema: ${e instanceof Error ? e.message : String(e)}`);
  }
  return out;
}

test("the registry and the manifest files match one to one", () => {
  expect(registeredBlocks(blocks)).toEqual(files(blockFiles));
  expect(Object.entries(layouts).map(([name, { manifest }]) => [name, manifest.name, manifest.version]).sort()).toEqual(files(layoutFiles));
  // a block registered without its own manifest file (the stand-in kpi_tile) fails it
  expect(registeredBlocks(testBlocks)).not.toEqual(files(blockFiles));
});

test("no two layouts share a region key", () => {
  // A block lists the region keys it fits, so a key two layouts shared would take the same blocks in
  // both. A new layout picks keys of its own, or block manifests move to `<layout>.<region>`.
  expect(REGION_KEYS).toEqual([...REGIONS]);
});

test("every registered manifest follows the rules", () => {
  for (const { manifest } of Object.values(layouts)) expect(validLayout(manifest), ajv.errorsText(validLayout.errors)).toBe(true);
  for (const versions of Object.values(blocks))
    for (const { manifest } of Object.values(versions)) expect(problems(manifest), manifest.name).toEqual([]);
});

test("a manifest with a styling prop at any depth, an invalid schema, no version or an unknown region fails", () => {
  const bad: BlockManifest = {
    name: "bad",
    version: 0,
    description: "",
    regions: ["sidebar"],
    props: { type: "object", properties: { color: { type: "string" }, size: { type: "huge" } } },
  };
  expect(problems(bad)).toEqual([
    "manifest/version must be >= 1",
    "manifest/description must NOT have fewer than 1 characters",
    "manifest/props/properties/color boolean schema is false",
    "manifest/props must have required property 'additionalProperties'",
    'no layout has region "sidebar"',
    expect.stringContaining("props is not valid JSON Schema"),
  ]);
  const deep: BlockManifest = {
    name: "deep",
    version: 1,
    description: "x",
    regions: ["main"],
    props: {
      type: "object",
      additionalProperties: false,
      properties: {
        textColor: { type: "string" },
        filter: { type: "object", properties: { className: { type: "string" }, header_style: { type: "string" }, label: { type: "string" } } },
        rows: { type: "array", items: { anyOf: [{ type: "object", properties: { bg_colour: { type: "string" }, sx: {} } }] } },
        darkTheme: { type: "boolean" },
      },
    },
  };
  // styling props at any depth, in any spelling; `label` is fine
  expect(problems(deep)).toEqual([
    "manifest/props/properties/filter/properties/className boolean schema is false",
    "manifest/props/properties/filter/properties/header_style boolean schema is false",
    "manifest/props/properties/rows/items/anyOf/0/properties/bg_colour boolean schema is false",
    "manifest/props/properties/rows/items/anyOf/0/properties/sx boolean schema is false",
    "manifest/props/properties/textColor boolean schema is false",
    "manifest/props/properties/darkTheme boolean schema is false",
  ]);
  const layout = { name: "x", version: 1, description: "x", regions: [{ key: "main", description: "x", allowed_blocks: [] }] };
  expect(validLayout(layout)).toBe(false); // allowed_blocks is derived in catalogue.json, never written
});
