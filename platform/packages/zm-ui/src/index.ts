// @zm/ui: the one Z-Matrix look on Mantine (D19 / ADR-001). Apps and blocks import Mantine directly
// for everything else; this package holds the theme, the provider and the extras Mantine lacks.
export { AppFrame, type NavItem } from "./AppFrame";
export { AppearanceMenu } from "./AppearanceMenu";
export { AuthCard, SignInForm, notifyError, notifyInfo } from "./SignIn";
export { DataTable, type Column } from "./DataTable";
export { Notice } from "./Notice";
export { Num, formatNum } from "./Num";
export { PageHeader } from "./PageHeader";
export { StageTracker, type Stage, type StageState } from "./StageTracker";
export { StatusPill, type StatusTone } from "./StatusPill";
export {
  ACCENTS,
  DEFAULT_PRESET,
  PRESETS,
  TONE_COLORS,
  createZmTheme,
  isAccent,
  resolveAccent,
  resolvePreset,
  type Accent,
  type Preset,
  type PresetOverrides,
} from "./theme";
export {
  APPEARANCE_USER_KEY,
  ZmProvider,
  accentStorageKey,
  appearanceScope,
  schemeStorageKey,
  useAppearance,
  type ZmClientLook,
} from "./ZmProvider";
