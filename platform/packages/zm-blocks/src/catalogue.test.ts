import { expect, test } from "vitest";
import { blocks, layouts, newest } from "./registry";

// catalogue.json (#37) is generated here, from the registry: every layout with its region rules and
// the newest version of every block with its props schema. zm_schema.validate() checks pages
// against it, and the API reads it for the agent's tools (#39). A region takes the blocks whose
// manifest lists it. When this fails, the file is stale: rewrite it with
// `pnpm vitest run -u packages/zm-blocks/src/catalogue.test.ts` and commit it.
const sorted = <T>(record: Record<string, T>) => Object.entries(record).sort(([a], [b]) => a.localeCompare(b));

test("catalogue.json is the registry's layouts and blocks", async () => {
  const manifests = sorted(blocks).flatMap(([, versions]) => newest(versions)?.manifest ?? []);
  const fits = (region: string) => manifests.filter((m) => m.regions.includes(region)).map((m) => m.name);
  const catalogue = {
    layouts: Object.fromEntries(
      sorted(layouts).map(([name, { manifest }]) => [
        name,
        { ...manifest, regions: manifest.regions.map((r) => ({ ...r, allowed_blocks: fits(r.key) })) },
      ]),
    ),
    blocks: Object.fromEntries(manifests.map((m) => [m.name, m])),
  };
  await expect(`${JSON.stringify(catalogue, null, 2)}\n`).toMatchFileSnapshot("../catalogue.json");
});
