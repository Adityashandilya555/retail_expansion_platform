import { Paper, SimpleGrid, Text, UnstyledButton } from "@mantine/core";
import { Num } from "@zm/ui";
import { useSearchParams } from "react-router";
import { Failed, flowOf, inTableScope, inTile, tableStages, tileKey, useCases, type Tile } from "../../src/cases";
import type { BlockProps } from "../../src/registry";

/** `props` as manifest.json allows them (the renderer has already validated them). */
type Props = { flow?: string; tiles: Tile[] };

/** kpi_strip v1 (S2-29, #94): counts over a flow's cases (within the `stages` of the page's
 * case_table over that flow); a tile picks (or clears) `?kpi=`, which filters that table to the
 * cases the tile counts. */
export function KpiStrip({ props, ui, page }: BlockProps) {
  const { flow: flowKey, tiles } = props as Props;
  const flow = flowOf(ui, flowKey);
  // ponytail: counted in the browser from GET /app/cases, the request case_table makes anyway. It
  // sends the newest 500, so past that a tile shows "N+" (at least N); exact counts with #55
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
  // counted within the page's case_table's `stages`, so a picked tile's count is the rows it shows
  const scope = tableStages(page, ui, flow);
  const scoped = cases.data?.rows.filter((c) => inTableScope(c, scope));
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
                <Num value={scoped?.filter((c) => inTile(t, c, flow)).length} />
                {cases.data?.truncated && "+"}
              </Text>
            </Paper>
          </UnstyledButton>
        );
      })}
    </SimpleGrid>
  );
}
