import { Paper, SimpleGrid, Text, UnstyledButton } from "@mantine/core";
import { Num } from "@zm/ui";
import { useSearchParams } from "react-router";
import { Failed, flowOf, inTile, tileKey, useCases, type Tile } from "../../src/cases";
import type { BlockProps } from "../../src/registry";

/** `props` as manifest.json allows them (the renderer has already validated them). */
type Props = { flow?: string; tiles: Tile[] };

/** kpi_strip v1 (S2-29, #94): counts over a flow's cases; a tile picks (or clears) `?kpi=`, which
 * filters the page's case_table to the cases the tile counts. */
export function KpiStrip({ props, ui }: BlockProps) {
  const { flow: flowKey, tiles } = props as Props;
  const flow = flowOf(ui, flowKey);
  // ponytail: counted in the browser from GET /app/cases, the request case_table makes anyway; fine
  // up to about 1–2k cases, then server-side counts with #55
  const cases = useCases(flow?.key);
  const [params, setParams] = useSearchParams();
  if (!flow) throw new Error("the release has no flow");
  if (cases.isError) return <Failed message="Couldn't load the counts." onRetry={() => void cases.refetch()} />;

  const label = (t: Tile) => {
    const at = t.stage && (flow.stages.find((s) => s.key === t.stage)?.name ?? t.stage);
    const named = {
      open_cases: at ? `In ${at}` : `Open ${flow.case_label.toLowerCase()}s`,
      in_stage: `In ${at}`,
      awaiting_me: at ? `Awaiting me in ${at}` : "Awaiting me",
      overdue: at ? `Overdue in ${at}` : "Overdue",
      done: at ? `Past ${at}` : "Completed",
    };
    return t.label ?? named[t.kind];
  };
  const picked = params.get("kpi");
  return (
    <SimpleGrid cols={{ base: 2, sm: tiles.length }}>
      {tiles.map((t, i) => {
        const key = tileKey(t);
        const on = key === picked;
        return (
          <UnstyledButton
            key={i}
            aria-pressed={on}
            onClick={() =>
              setParams(
                (p) => {
                  p.delete("stage"); // the tile takes over from the table's stage chips
                  if (on) p.delete("kpi");
                  else p.set("kpi", key);
                  return p;
                },
                { replace: true },
              )
            }
          >
            <Paper component="span" display="block" withBorder p="md" bg={on ? "var(--mantine-primary-color-light)" : undefined}>
              <Text span display="block" size="sm" c={on ? "var(--mantine-primary-color-light-color)" : "dimmed"}>
                {label(t)}
              </Text>
              <Text span display="block" fz="h2" fw={650}>
                <Num value={cases.data?.filter((c) => inTile(t, c, flow)).length} />
              </Text>
            </Paper>
          </UnstyledButton>
        );
      })}
    </SimpleGrid>
  );
}
