/**
 * Shared Recharts hover sync for every history chart on a page.
 *
 * Charts that share {@link CHART_SYNC_ID} broadcast their active tooltip
 * position. Player/tick samples and container samples are polled on different
 * clocks, so we match by nearest timestamp rather than by index or exact label.
 *
 * Sync is gated by each receiving chart's *actual sample span* (first…last
 * loaded point for the selected range), not the nominal range tab (24h / 7d).
 * Hovering 6h ago on a full series must not light up a chart that only has
 * the last 2h of data.
 */

/** One sync group for the whole SPA page — every mounted chart joins it. */
export const CHART_SYNC_ID = "ssm-history";

type TooltipTick = { value?: unknown };

/**
 * Recharts `syncMethod`: map the hovered chart's activeLabel onto the nearest
 * point of the receiving chart. Returns -1 when the hover is outside that
 * chart's loaded data span, or when nothing can be matched.
 */
export function syncChartsByNearestTime(
  tooltipTicks: TooltipTick[],
  data: { activeLabel?: string | number },
): number {
  if (!tooltipTicks?.length || data?.activeLabel == null || data.activeLabel === "") {
    return -1;
  }

  const label = data.activeLabel;
  // Prefer exact match first (player + tick charts share the same series).
  for (let i = 0; i < tooltipTicks.length; i++) {
    if (tooltipTicks[i]?.value === label) return i;
  }

  const target = Date.parse(String(label));
  if (Number.isNaN(target)) return -1;

  // Extent of samples actually drawn on this chart for the current selection.
  let minT = Infinity;
  let maxT = -Infinity;
  for (let i = 0; i < tooltipTicks.length; i++) {
    const t = Date.parse(String(tooltipTicks[i]?.value ?? ""));
    if (Number.isNaN(t)) continue;
    if (t < minT) minT = t;
    if (t > maxT) maxT = t;
  }
  if (!Number.isFinite(minT) || target < minT || target > maxT) {
    return -1;
  }

  let best = -1;
  let bestDist = Infinity;
  for (let i = 0; i < tooltipTicks.length; i++) {
    const t = Date.parse(String(tooltipTicks[i]?.value ?? ""));
    if (Number.isNaN(t)) continue;
    const dist = Math.abs(t - target);
    if (dist < bestDist) {
      bestDist = dist;
      best = i;
    }
  }
  return best;
}
