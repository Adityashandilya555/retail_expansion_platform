import { ACCENTS, PRESETS } from "@zm/ui";
import { expect, test } from "vitest";
import { blocks, layouts } from "./registry";

// catalogue.json (#37) is generated here, from the registry: every layout with its region rules and
// every registered version of every block with its props schema, plus the theme presets and accents
// of @zm/ui (#34). zm_schema.validate() checks pages and the look against it, and the API reads it
// for the agent's tools (#39). A region takes the blocks with a version whose manifest lists it.
// When this fails, the file is stale: rewrite it with
// `pnpm vitest run -u packages/zm-blocks/src/catalogue.test.ts` and commit it.
const sorted = <T>(record: Record<string, T>) => Object.entries(record).sort(([a], [b]) => a.localeCompare(b));

test("catalogue.json is the registry's layouts and blocks, and the presets and accents", async () => {
  const versions = sorted(blocks).map(([name, vs]) => [name, Object.values(vs).map((e) => e.manifest)] as const);
  const fits = (region: string) => versions.filter(([, ms]) => ms.some((m) => m.regions.includes(region))).map(([name]) => name);
  const catalogue = {
    layouts: Object.fromEntries(
      sorted(layouts).map(([name, { manifest }]) => [
        name,
        { ...manifest, regions: manifest.regions.map((r) => ({ ...r, allowed_blocks: fits(r.key) })) },
      ]),
    ),
    blocks: Object.fromEntries(versions.map(([name, ms]) => [name, Object.fromEntries(ms.map((m) => [m.version, m]))])),
    presets: Object.keys(PRESETS),
    accents: ACCENTS,
  };
  await expect(`${JSON.stringify(catalogue, null, 2)}\n`).toMatchFileSnapshot("../catalogue.json");
});
