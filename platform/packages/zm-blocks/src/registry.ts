// What the shell can draw: layouts by name, blocks by name and version (#27). The agent picks and
// configures them per client by their manifests; it never writes code (AGENTS.md, step 3).
import type { components } from "@zm/api-client";
import type { ComponentType, ReactNode } from "react";
import { TaskInbox } from "../blocks/task_inbox/Block";
import taskInboxManifest from "../blocks/task_inbox/manifest.json";
import { CaseLayout } from "../layouts/case/Layout";
import caseManifest from "../layouts/case/manifest.json";
import { StandardLayout } from "../layouts/standard/Layout";
import standardManifest from "../layouts/standard/manifest.json";

export type AppUi = components["schemas"]["AppUi"];
export type UiPage = components["schemas"]["Page"];
export type BlockRef = components["schemas"]["BlockRef"];

/** A block's `manifest.json`: where it may go and which props it takes (JSON Schema, no styling props). */
export type BlockManifest = {
  name: string;
  version: number;
  description: string;
  regions: string[];
  props: Record<string, unknown>;
};
/** `props` have passed the manifest's schema. */
export type BlockProps = { props: Record<string, unknown>; ui: AppUi; page: UiPage };
export type BlockEntry = { manifest: BlockManifest; Block: ComponentType<BlockProps> };
/** Block name → manifest version → block. The shell keeps every version a live release uses (D14). */
export type BlockRegistry = Record<string, Record<number, BlockEntry>>;

/** A layout's `manifest.json`: its named regions, in drawing order. */
export type LayoutManifest = {
  name: string;
  version: number;
  description: string;
  /** Page properties the layout needs, e.g. `flow` for `case`. */
  requires?: string[];
  regions: { key: string; description: string; allowed_blocks?: string[]; max?: number }[];
};
/** Only non-empty regions are passed. */
export type LayoutProps = { regions: Partial<Record<string, ReactNode>> };
export type LayoutEntry = { manifest: LayoutManifest; Layout: ComponentType<LayoutProps> };

/** The case blocks (S1-25, #92) register here too. */
export const blocks: BlockRegistry = {
  task_inbox: { 1: { manifest: taskInboxManifest, Block: TaskInbox } },
};

export const layouts: Record<string, LayoutEntry> = {
  standard: { manifest: standardManifest, Layout: StandardLayout },
  case: { manifest: caseManifest, Layout: CaseLayout },
};
