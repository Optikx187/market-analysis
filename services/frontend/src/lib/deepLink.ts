import type { ActionItemDeepLink } from "@/lib/api";

/** A deep-link request handed to a panel. `token` changes on every request so a
 * repeated link to the same context still re-focuses the panel. */
export interface DeepLinkFocus {
  token: number;
  ticker?: string | null;
  opportunityId?: string | null;
  tradeId?: number | null;
  orderId?: number | null;
  section?: string | null;
}

export type DeepLinkTab = "alerts" | "orders" | "trades" | "scanner" | "settings";

const TABS: DeepLinkTab[] = ["alerts", "orders", "trades", "scanner", "settings"];

export const deepLinkTab = (link: ActionItemDeepLink): DeepLinkTab =>
  (TABS as string[]).includes(link.tab) ? (link.tab as DeepLinkTab) : "alerts";

export const deepLinkFocus = (link: ActionItemDeepLink, token: number): DeepLinkFocus => ({
  token,
  ticker: link.ticker ?? null,
  opportunityId: link.opportunity_id ?? null,
  tradeId: link.trade_id ?? null,
  orderId: link.order_id ?? null,
  section: link.section ?? null,
});

export const deepLinkLabel = (link: ActionItemDeepLink): string => {
  const target = link.ticker || link.opportunity_id || (link.trade_id ? `trade ${link.trade_id}` : "")
    || (link.order_id ? `order ${link.order_id}` : "") || link.section || "";
  return target ? `Open ${link.tab}: ${target}` : `Open ${link.tab}`;
};
