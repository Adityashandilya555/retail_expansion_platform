// @zm/blocks (#27): layouts and blocks, each with a manifest.json, and the renderer that draws a
// release's page from them.
export { BlocksApi, useBlocksApi } from "./api";
export { PageRenderer, defaultCasePage } from "./PageRenderer";
export {
  blocks,
  layouts,
  type AppUi,
  type BlockEntry,
  type BlockManifest,
  type BlockProps,
  type BlockRef,
  type BlockRegistry,
  type LayoutEntry,
  type LayoutManifest,
  type LayoutProps,
  type UiPage,
} from "./registry";
