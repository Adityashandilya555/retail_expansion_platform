import { Grid, Stack } from "@mantine/core";
import type { LayoutProps } from "../../src/registry";

/** `standard`: an optional hero, then `main` on 12 columns, or 8/4 when `aside` has content. */
export function StandardLayout({ regions: { hero, main, aside } }: LayoutProps) {
  const mainSpan = aside ? 8 : 12;
  return (
    <Stack gap="lg">
      {hero}
      <Grid gap="lg">
        <Grid.Col span={{ base: 12, md: mainSpan }} data-region="main" data-span={mainSpan}>
          <Stack gap="lg">{main}</Stack>
        </Grid.Col>
        {aside && (
          <Grid.Col span={{ base: 12, md: 4 }} data-region="aside" data-span={4}>
            <Stack gap="lg">{aside}</Stack>
          </Grid.Col>
        )}
      </Grid>
    </Stack>
  );
}
