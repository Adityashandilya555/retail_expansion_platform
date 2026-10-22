import { Alert, Code, Text } from "@mantine/core";
import { ajv } from "@zm/forms";
import { Component, useEffect, type ReactNode } from "react";
import { blocks, layouts, type AppUi, type BlockRef, type BlockRegistry, type LayoutManifest, type UiPage } from "./registry";

type Region = LayoutManifest["regions"][number];

/** What `/app/cases/:code` draws when the release has no `case` page for the case's flow. */
export function defaultCasePage(flow: string): UiPage {
  return {
    key: "case",
    layout: "case",
    flow,
    regions: {
      header: [{ block: "case_header", props: {} }],
      overview: [{ block: "case_fields", props: {} }],
      tasks: [{ block: "case_tasks", props: {} }],
    },
  };
}

/** Visible stand-in for anything the shell can't draw; the rest of the page keeps working. */
function Placeholder({ name, problem }: { name: string; problem: string }) {
  return (
    <Alert variant="outline" color="gray" title="This part of the page can't be shown">
      <Text size="sm">
        <Code>{name}</Code>: {problem}
      </Text>
    </Alert>
  );
}

/** Turns a block that throws while rendering into a placeholder (React logs the error). */
class Boundary extends Component<{ name: string; children: ReactNode }, { problem: string | null }> {
  override state = { problem: null as string | null };

  static getDerivedStateFromError(error: unknown) {
    return { problem: error instanceof Error ? error.message : String(error) };
  }

  override render() {
    return this.state.problem === null ? this.props.children : <Placeholder name={this.props.name} problem={this.state.problem} />;
  }
}

function Slot({ at, region, index, page, ui, registry }: { at: BlockRef; region: Region; index: number; page: UiPage; ui: AppUi; registry: BlockRegistry }) {
  const versions = registry[at.block] ?? {};
  // ponytail: the newest version until releases record which one they placed (blocks_used, D14)
  const entry = versions[Math.max(...Object.keys(versions).map(Number))];
  let problem: string | null = null;
  if (!entry) problem = "unknown block";
  else if (!entry.manifest.regions.includes(region.key) || (region.allowed_blocks && !region.allowed_blocks.includes(at.block)))
    problem = `not allowed in region "${region.key}"`;
  else if (region.max !== undefined && index >= region.max) problem = `region "${region.key}" takes at most ${region.max}`;
  else {
    const validate = ajv.compile(entry.manifest.props); // Ajv caches by schema object
    if (!validate(at.props)) problem = ajv.errorsText(validate.errors, { dataVar: "props" });
  }

  useEffect(() => {
    if (problem) console.error(`[zm-blocks] page "${page.key}", region "${region.key}", ${at.block}: ${problem}`);
  }, [problem, page.key, region.key, at.block]);

  if (!entry || problem) return <Placeholder name={at.block} problem={problem ?? ""} />;
  return <entry.Block props={at.props} ui={ui} page={page} />;
}

/** Draws a release's page: its layout, and each region's blocks from the registry. */
export function PageRenderer({ page, ui, registry = blocks }: { page: UiPage; ui: AppUi; registry?: BlockRegistry }) {
  const layout = layouts[page.layout];
  useEffect(() => {
    if (!layout) console.error(`[zm-blocks] page "${page.key}": unknown layout "${page.layout}"`);
  }, [layout, page.key, page.layout]);
  if (!layout) return <Placeholder name={page.key} problem={`unknown layout "${page.layout}"`} />;

  const regions = Object.fromEntries(
    layout.manifest.regions.flatMap((region) => {
      const refs = page.regions[region.key] ?? [];
      if (refs.length === 0) return [];
      const slots = refs.map((at, i) => (
        <Boundary key={`${i}:${at.block}`} name={at.block}>
          <Slot at={at} region={region} index={i} page={page} ui={ui} registry={registry} />
        </Boundary>
      ));
      return [[region.key, slots]];
    }),
  );
  return <layout.Layout regions={regions} />;
}
