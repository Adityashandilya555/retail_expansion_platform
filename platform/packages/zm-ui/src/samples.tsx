// Sample data for /dev/ui and the accent × scheme render test (accents.test.tsx), so the preview
// and the test always show the same extras.
import { Text } from "@mantine/core";
import { ClipboardCheck, Inbox, MapPin, Scale } from "lucide-react";
import type { NavItem } from "./AppFrame";
import type { Column } from "./DataTable";
import { Num } from "./Num";
import type { Stage } from "./StageTracker";
import { StatusPill } from "./StatusPill";

export const sampleNav: NavItem[] = [
  { key: "home", label: "My work", icon: Inbox, badge: 3 },
  { key: "sites", label: "Sites", icon: MapPin },
  { key: "legal", label: "Legal", icon: Scale, badge: 1 },
  { key: "design", label: "Design", icon: ClipboardCheck },
];

export const sampleStages: Stage[] = [
  { key: "bd", label: "BD", state: "done" },
  { key: "legal", label: "Legal", state: "open" },
  { key: "finance", label: "Finance", state: "open" },
  { key: "design", label: "Design", state: "queued" },
  { key: "launch", label: "Launch", state: "queued" },
];

export type SampleSite = {
  code: string;
  name: string;
  city: string;
  days: number;
  capex: number;
  state: Stage["state"];
};

export const sampleSites: SampleSite[] = [
  { code: "MX-0001", name: "Koramangala 5th Block", city: "Bengaluru", days: 4, capex: 4250000, state: "open" },
  { code: "MX-0002", name: "Baner Road", city: "Pune", days: 11, capex: 3180000, state: "queued" },
  { code: "MX-0003", name: "Anna Nagar", city: "Chennai", days: 2, capex: 5120000, state: "done" },
];

export const sampleColumns: Column<SampleSite>[] = [
  { key: "code", header: "Code", sortValue: (s) => s.code, render: (s) => <Text ff="monospace" size="sm">{s.code}</Text> },
  { key: "name", header: "Site", sortValue: (s) => s.name },
  { key: "city", header: "City" },
  { key: "state", header: "Status", render: (s) => <StatusPill tone={s.state} /> },
  { key: "days", header: "Days in stage", align: "right", sortValue: (s) => s.days, render: (s) => <Num value={s.days} /> },
  { key: "capex", header: "Capex", align: "right", sortValue: (s) => s.capex, render: (s) => <Num value={s.capex} currency /> },
];
