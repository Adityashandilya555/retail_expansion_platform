import { Text } from "@mantine/core";

/** Indian grouping (12,34,567); `currency` adds ₹. Empty values render as an em dash. */
export function formatNum(value: number | null | undefined, { currency = false, decimals = 0 } = {}): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-IN", {
    style: currency ? "currency" : "decimal",
    currency: "INR",
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value);
}

/** Every number and code in mono with tabular figures (Z-Matrix kit). */
export function Num({ value, currency, decimals }: { value: number | null | undefined; currency?: boolean; decimals?: number }) {
  return (
    <Text span ff="monospace" data-num="" style={{ fontVariantNumeric: "tabular-nums" }}>
      {formatNum(value, { currency, decimals })}
    </Text>
  );
}
