import { expect, test } from "vitest";

// Every package source file as text (Vite's raw glob). Colours live only in theme.ts.
const sources = import.meta.glob<string>("../../*/{src,layouts,blocks}/**/*.{ts,tsx}", { query: "?raw", import: "default", eager: true });
const COLOUR = /#[0-9a-fA-F]{3,8}\b|\brgba?\(/;

test("no hex or rgb colours in packages/*/src outside theme.ts", () => {
  expect(Object.keys(sources).length).toBeGreaterThan(5);
  const offenders = Object.entries(sources)
    .filter(([path]) => !path.endsWith("/theme.ts") && !path.endsWith("/no-hex.test.ts"))
    .flatMap(([path, text]) =>
      text.split("\n").flatMap((line, i) => (COLOUR.test(line) ? [`${path}:${i + 1}: ${line.trim()}`] : [])),
    );
  expect(offenders).toEqual([]);
});
