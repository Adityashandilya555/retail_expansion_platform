import { Stack, Tabs } from "@mantine/core";
import { useSearchParams } from "react-router";
import type { LayoutProps } from "../../src/registry";
import manifest from "./manifest.json";

const TABS = manifest.regions.map((r) => r.key).filter((key) => key !== "header");
const label = (key: string) => key.charAt(0).toUpperCase() + key.slice(1);

/** `case`: the header, then one tab per non-empty region; the open tab lives in `?tab=`. */
export function CaseLayout({ regions }: LayoutProps) {
  const [params, setParams] = useSearchParams();
  const tabs = TABS.filter((key) => regions[key]);
  const asked = params.get("tab");
  const open = asked && tabs.includes(asked) ? asked : (tabs[0] ?? null);

  return (
    <Stack gap="lg">
      {regions.header}
      {tabs.length > 0 && (
        <Tabs
          value={open}
          keepMounted={false}
          onChange={(tab) =>
            tab &&
            setParams(
              (p) => {
                p.set("tab", tab);
                return p;
              },
              { replace: true },
            )
          }
        >
          <Tabs.List>
            {tabs.map((key) => (
              <Tabs.Tab key={key} value={key}>
                {label(key)}
              </Tabs.Tab>
            ))}
          </Tabs.List>
          {tabs.map((key) => (
            <Tabs.Panel key={key} value={key} pt="lg">
              <Stack gap="lg">{regions[key]}</Stack>
            </Tabs.Panel>
          ))}
        </Tabs>
      )}
    </Stack>
  );
}
