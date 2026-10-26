// #20 acceptance, run after `pnpm --filter @zm/web build` (CI `web` job): the production build
// leaves out /dev/ui, loads no Google Fonts and keeps the initial JS within 300 kB gzip.
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const dist = join(dirname(fileURLToPath(import.meta.url)), "..", "dist");
const BUDGET_GZIP = 300 * 1024;

const walk = (dir) =>
  readdirSync(dir, { withFileTypes: true }).flatMap((d) =>
    d.isDirectory() ? walk(join(dir, d.name)) : [join(dir, d.name)],
  );
const files = walk(dist).filter((f) => /\.(js|css|html)$/.test(f));
const problems = [];
if (files.length === 0) problems.push(`no build output in ${dist}`);

let jsGzip = 0;
for (const file of files) {
  const bytes = readFileSync(file);
  const text = bytes.toString("utf8");
  const name = relative(dist, file);
  if (text.includes("Every @zm/ui extra") || /DevUi/.test(text)) problems.push(`${name}: contains the /dev/ui preview`);
  if (text.includes("/dev/layouts") || /DevLayouts/.test(text)) problems.push(`${name}: contains the /dev/layouts preview`);
  if (text.includes("fonts.googleapis.com")) problems.push(`${name}: references fonts.googleapis.com`);
  if (file.endsWith(".js")) jsGzip += gzipSync(bytes).length;
}
if (jsGzip > BUDGET_GZIP) problems.push(`initial JS is ${(jsGzip / 1024).toFixed(1)} kB gzip (budget 300 kB)`);

if (problems.length) {
  for (const p of problems) console.error(`check-build: ${p}`);
  process.exit(1);
}
console.log(`check-build: ok (no /dev/ui or /dev/layouts, no Google Fonts, initial JS ${(jsGzip / 1024).toFixed(1)} kB gzip)`);
