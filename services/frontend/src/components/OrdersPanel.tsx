import { useCallback, useEffect, useMemo, useState } from "react";
import {
  PAPER_ORDER_STATUSES,
  PAPER_ORDER_TYPES,
  apiErrorMessage,
  cancelPaperOrder,
  createPaperOrder,
  fetchPaperMode,
  fetchPaperOrder,
  fetchPaperOrders,
  fetchPaperReconciliation,
  processPaperOrders,
  type PaperMode,
  type PaperOrder,
  type PaperOrderFilters,
  type PaperOrderType,
  type PaperReconciliation,
} from "@/lib/api";
import { notifyTradesChanged } from "@/lib/tradeEvents";

const TERMINAL_STATUSES = ["filled", "canceled", "rejected", "expired"];

const STATUS_STYLES: Record<string, string> = {
  pending: "bg-gray-800 text-gray-300",
  submitted: "bg-blue-900 text-blue-300",
  partially_filled: "bg-amber-900 text-amber-300",
  filled: "bg-green-900 text-green-300",
  canceled: "bg-gray-800 text-gray-400",
  rejected: "bg-red-900 text-red-300",
  expired: "bg-purple-900 text-purple-300",
};

const TYPE_LABELS: Record<PaperOrderType, string> = {
  market: "Market",
  limit: "Limit",
  stop: "Stop",
  stop_limit: "Stop-Limit",
  bracket: "Bracket (OCO exits)",
  trailing_stop: "Trailing Stop",
};

const emptyOrderForm = {
  idempotency_key: "",
  ticker: "",
  asset_type: "stock",
  side: "BUY",
  order_type: "market" as PaperOrderType,
  quantity: "",
  limit_price: "",
  stop_price: "",
  trail_percent: "",
  trail_amount: "",
  reference_price: "",
  take_profit_price: "",
  stop_loss_price: "",
  time_in_force: "gtc",
};

const emptyCandleForm = {
  ticker: "",
  timestamp: "",
  open: "",
  high: "",
  low: "",
  close: "",
  volume: "",
};

const money = (value: number | null | undefined, digits = 2): string =>
  value == null ? "—" : `$${value.toFixed(digits)}`;

const quantity = (value: number | null | undefined): string =>
  value == null ? "—" : String(Number(value.toFixed(8)));

const formatDateTime = (value: string | null): string => {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("en-US", {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true,
  });
};

const statusLabel = (status: string): string => status.replace("_", " ");

function numberOrUndefined(raw: string): number | undefined {
  if (!raw.trim()) return undefined;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : undefined;
}

/** Which optional price inputs a given order type actually uses. */
function typeFields(orderType: PaperOrderType): string[] {
  switch (orderType) {
    case "limit":
      return ["limit_price"];
    case "stop":
      return ["stop_price"];
    case "stop_limit":
      return ["stop_price", "limit_price"];
    case "bracket":
      return ["reference_price", "take_profit_price", "stop_loss_price"];
    case "trailing_stop":
      return ["trail_percent", "trail_amount"];
    default:
      return ["reference_price"];
  }
}

function OrderDetail({ order }: { order: PaperOrder }) {
  const parameters = [
    ["Limit price", money(order.limit_price)],
    ["Stop price", money(order.stop_price)],
    ["Effective stop", money(order.effective_stop_price)],
    ["Trail %", order.trail_percent == null ? "—" : `${order.trail_percent}%`],
    ["Trail amount", money(order.trail_amount)],
    ["Trail high-water", money(order.trail_reference_price)],
    ["Reference price", money(order.reference_price)],
    ["Triggered", order.triggered ? `Yes · ${formatDateTime(order.triggered_at)}` : "No"],
    ["Time in force", order.time_in_force.toUpperCase()],
    ["Expires", formatDateTime(order.expires_at)],
  ];
  const accounting = [
    ["Filled / remaining", `${quantity(order.filled_quantity)} / ${quantity(order.remaining_quantity)}`],
    ["Average fill", money(order.average_fill_price, 4)],
    ["Filled notional", money(order.filled_notional)],
    ["Fees", money(order.fees_total, 4)],
    ["Slippage", money(order.slippage_total, 4)],
    ["Costs total", money(order.costs_total, 4)],
    ["Reserved cash", money(order.reserved_cash)],
    ["Reservation price", money(order.reservation_price, 4)],
  ];

  return (
    <div className="space-y-3 rounded border border-[var(--border)] bg-[var(--background)] p-3 text-xs">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <div>
          <div className="mb-1 font-medium text-[var(--muted-foreground)]">Identity</div>
          <dl className="space-y-0.5">
            <div className="flex justify-between gap-3"><dt>Order ID</dt><dd className="font-mono">{order.id}</dd></div>
            <div className="flex justify-between gap-3"><dt>Idempotency key</dt><dd className="font-mono">{order.idempotency_key}</dd></div>
            <div className="flex justify-between gap-3"><dt>Role</dt><dd>{order.role}</dd></div>
            <div className="flex justify-between gap-3"><dt>Parent</dt><dd>{order.parent_id ?? "—"}</dd></div>
            <div className="flex justify-between gap-3"><dt>OCO group</dt><dd className="font-mono">{order.oco_group ?? "—"}</dd></div>
            <div className="flex justify-between gap-3"><dt>Trade</dt><dd>{order.trade_id ?? "—"}</dd></div>
            <div className="flex justify-between gap-3"><dt>Created</dt><dd>{formatDateTime(order.created_at)}</dd></div>
          </dl>
        </div>
        <div>
          <div className="mb-1 font-medium text-[var(--muted-foreground)]">Type parameters</div>
          <dl className="space-y-0.5">
            {parameters.map(([label, value]) => (
              <div key={label} className="flex justify-between gap-3"><dt>{label}</dt><dd>{value}</dd></div>
            ))}
          </dl>
        </div>
        <div>
          <div className="mb-1 font-medium text-[var(--muted-foreground)]">Fill accounting</div>
          <dl className="space-y-0.5">
            {accounting.map(([label, value]) => (
              <div key={label} className="flex justify-between gap-3"><dt>{label}</dt><dd>{value}</dd></div>
            ))}
          </dl>
        </div>
      </div>

      {(order.reject_reason || order.cancel_reason) && (
        <div className="rounded border border-red-600 bg-red-600/10 p-2 text-red-300">
          {order.reject_reason ?? order.cancel_reason}
        </div>
      )}

      {order.children && order.children.length > 0 && (
        <div>
          <div className="mb-1 font-medium text-[var(--muted-foreground)]">Child orders (OCO siblings)</div>
          <table className="w-full">
            <thead>
              <tr className="text-left text-[var(--muted-foreground)]">
                <th className="pb-1">ID</th><th className="pb-1">Role</th><th className="pb-1">Type</th>
                <th className="pb-1">Trigger</th><th className="pb-1">Status</th><th className="pb-1">Filled</th>
              </tr>
            </thead>
            <tbody>
              {order.children.map((child) => (
                <tr key={child.id} className="border-t border-[var(--border)]">
                  <td className="py-1 font-mono">{child.id}</td>
                  <td>{child.role}</td>
                  <td>{child.order_type}</td>
                  <td>{money(child.limit_price ?? child.stop_price)}</td>
                  <td>{statusLabel(child.status)}</td>
                  <td>{quantity(child.filled_quantity)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div>
        <div className="mb-1 font-medium text-[var(--muted-foreground)]">Fills</div>
        {order.fills && order.fills.length > 0 ? (
          <table className="w-full">
            <thead>
              <tr className="text-left text-[var(--muted-foreground)]">
                <th className="pb-1">Qty</th><th className="pb-1">Price</th><th className="pb-1">Notional</th>
                <th className="pb-1">Fees</th><th className="pb-1">Slippage</th><th className="pb-1">Candle</th>
              </tr>
            </thead>
            <tbody>
              {order.fills.map((fill) => (
                <tr key={fill.id} className="border-t border-[var(--border)]">
                  <td className="py-1">{quantity(fill.quantity)}</td>
                  <td>{money(fill.price, 4)}</td>
                  <td>{money(fill.notional)}</td>
                  <td>{money(fill.fees, 4)}</td>
                  <td>{money(fill.slippage, 4)}</td>
                  <td>{formatDateTime(fill.candle_timestamp)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-[var(--muted-foreground)]">No fills yet.</p>
        )}
      </div>

      <div>
        <div className="mb-1 font-medium text-[var(--muted-foreground)]">Audit trail (immutable, oldest first)</div>
        <ol className="space-y-1">
          {(order.events ?? []).map((event) => (
            <li key={event.id} className="border-t border-[var(--border)] pt-1">
              <span className="font-mono">{event.event_type}</span>
              {event.from_status !== event.to_status && (
                <span className="text-[var(--muted-foreground)]">
                  {" "}· {statusLabel(event.from_status ?? "—")} → {statusLabel(event.to_status ?? "—")}
                </span>
              )}
              <span className="text-[var(--muted-foreground)]"> · {formatDateTime(event.created_at)}</span>
              {event.message && <div className="text-[var(--muted-foreground)]">{event.message}</div>}
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

export default function OrdersPanel() {
  const [mode, setMode] = useState<PaperMode | null>(null);
  const [orders, setOrders] = useState<PaperOrder[]>([]);
  const [details, setDetails] = useState<Record<number, PaperOrder>>({});
  const [expanded, setExpanded] = useState<number | null>(null);
  const [filters, setFilters] = useState<PaperOrderFilters>({});
  const [reconciliation, setReconciliation] = useState<PaperReconciliation | null>(null);
  const [orderForm, setOrderForm] = useState(emptyOrderForm);
  const [candleForm, setCandleForm] = useState(emptyCandleForm);
  const [showOrderForm, setShowOrderForm] = useState(false);
  const [showProcessForm, setShowProcessForm] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const activeFilters = useMemo(() => {
    const cleaned: PaperOrderFilters = {};
    Object.entries(filters).forEach(([key, value]) => {
      if (value) cleaned[key as keyof PaperOrderFilters] = value;
    });
    return cleaned;
  }, [filters]);

  const loadOrders = useCallback(async () => {
    const response = await fetchPaperOrders(activeFilters);
    setOrders(response.orders);
  }, [activeFilters]);

  useEffect(() => {
    fetchPaperMode().then(setMode).catch(() => {});
  }, []);

  useEffect(() => {
    loadOrders().catch((error: unknown) => {
      setMessage({ type: "error", text: apiErrorMessage(error, "Failed to load paper orders.") });
    });
  }, [loadOrders]);

  const refresh = async (orderId?: number) => {
    await loadOrders();
    const target = orderId ?? expanded;
    if (target != null) {
      const detail = await fetchPaperOrder(target);
      setDetails((current) => ({ ...current, [target]: detail }));
    }
    if (reconciliation) setReconciliation(await fetchPaperReconciliation());
  };

  const toggleExpanded = async (orderId: number) => {
    if (expanded === orderId) {
      setExpanded(null);
      return;
    }
    setExpanded(orderId);
    try {
      const detail = await fetchPaperOrder(orderId);
      setDetails((current) => ({ ...current, [orderId]: detail }));
    } catch (error) {
      setMessage({ type: "error", text: apiErrorMessage(error, "Failed to load order detail.") });
    }
  };

  const submitOrder = async () => {
    const qty = Number(orderForm.quantity);
    if (!orderForm.idempotency_key.trim() || !orderForm.ticker.trim() || !Number.isFinite(qty) || qty <= 0) {
      setMessage({ type: "error", text: "Idempotency key, ticker and a positive quantity are required." });
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const created = await createPaperOrder({
        idempotency_key: orderForm.idempotency_key.trim(),
        ticker: orderForm.ticker.trim().toUpperCase(),
        asset_type: orderForm.asset_type,
        side: orderForm.side,
        order_type: orderForm.order_type,
        quantity: qty,
        time_in_force: orderForm.time_in_force,
        limit_price: numberOrUndefined(orderForm.limit_price),
        stop_price: numberOrUndefined(orderForm.stop_price),
        trail_percent: numberOrUndefined(orderForm.trail_percent),
        trail_amount: numberOrUndefined(orderForm.trail_amount),
        reference_price: numberOrUndefined(orderForm.reference_price),
        take_profit_price: numberOrUndefined(orderForm.take_profit_price),
        stop_loss_price: numberOrUndefined(orderForm.stop_loss_price),
      });
      setMessage({
        type: "success",
        text: `Paper order #${created.id} ${statusLabel(created.status)} — ${created.side} ${quantity(created.quantity)} ${created.ticker}. No broker order was submitted.`,
      });
      setOrderForm({ ...emptyOrderForm, ticker: orderForm.ticker, asset_type: orderForm.asset_type });
      setCandleForm((current) => ({ ...current, ticker: current.ticker || created.ticker }));
      await refresh(created.id);
    } catch (error) {
      setMessage({ type: "error", text: apiErrorMessage(error, "Failed to create the paper order.") });
    }
    setBusy(false);
  };

  const cancel = async (order: PaperOrder) => {
    setBusy(true);
    setMessage(null);
    try {
      await cancelPaperOrder(order.id, "Canceled from the Orders view");
      setMessage({ type: "success", text: `Paper order #${order.id} canceled and its reserved cash released.` });
      await refresh(order.id);
    } catch (error) {
      setMessage({ type: "error", text: apiErrorMessage(error, "Failed to cancel the order.") });
    }
    setBusy(false);
  };

  const process = async () => {
    const ticker = candleForm.ticker.trim().toUpperCase();
    if (!ticker) {
      setMessage({ type: "error", text: "A ticker is required to process orders." });
      return;
    }
    const numeric = ["open", "high", "low", "close", "volume"] as const;
    const values = numeric.map((field) => numberOrUndefined(candleForm[field]));
    const hasCandle = candleForm.timestamp.trim() !== "" || values.some((value) => value !== undefined);
    if (hasCandle && (!candleForm.timestamp.trim() || values.some((value) => value === undefined))) {
      setMessage({ type: "error", text: "A deterministic candle needs a timestamp plus open, high, low, close and volume." });
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const response = await processPaperOrders({
        ticker,
        candles: hasCandle
          ? [{
              timestamp: new Date(candleForm.timestamp).toISOString(),
              open: values[0] as number,
              high: values[1] as number,
              low: values[2] as number,
              close: values[3] as number,
              volume: values[4] as number,
            }]
          : undefined,
      });
      setMessage({
        type: "success",
        text: `Processed ${response.processed_candles} candle(s) from ${response.candle_source}; ${response.orders.length} order(s) advanced. Balance ${money(response.portfolio.balance)}.`,
      });
      await refresh();
      notifyTradesChanged();
    } catch (error) {
      setMessage({ type: "error", text: apiErrorMessage(error, "Failed to process candles.") });
    }
    setBusy(false);
  };

  const loadReconciliation = async () => {
    try {
      setReconciliation(await fetchPaperReconciliation());
    } catch (error) {
      setMessage({ type: "error", text: apiErrorMessage(error, "Failed to load reconciliation.") });
    }
  };

  const fields = typeFields(orderForm.order_type);
  const priceInput = (name: string, label: string, step = "0.01") => (
    <label key={name} className="flex flex-col gap-1 text-xs">
      <span className="text-[var(--muted-foreground)]">{label}</span>
      <input
        type="number"
        step={step}
        value={orderForm[name as keyof typeof orderForm] as string}
        onChange={(e) => setOrderForm({ ...orderForm, [name]: e.target.value })}
        className="rounded border bg-[var(--input)] px-2 py-1.5 text-xs"
      />
    </label>
  );

  return (
    <div className="rounded-lg border bg-[var(--card)] p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold">Orders</h2>
          <span className="rounded bg-amber-500 px-2 py-0.5 text-xs font-bold uppercase tracking-wide text-black">
            Paper
          </span>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => { setShowOrderForm(!showOrderForm); setMessage(null); }}
            className="rounded bg-[var(--secondary)] px-3 py-1.5 text-xs hover:bg-[var(--accent)]"
          >
            {showOrderForm ? "Close" : "New Paper Order"}
          </button>
          <button
            onClick={() => { setShowProcessForm(!showProcessForm); setMessage(null); }}
            className="rounded bg-[var(--secondary)] px-3 py-1.5 text-xs hover:bg-[var(--accent)]"
          >
            {showProcessForm ? "Close" : "Process Candles"}
          </button>
          <button
            onClick={loadReconciliation}
            className="rounded bg-[var(--secondary)] px-3 py-1.5 text-xs hover:bg-[var(--accent)]"
          >
            Reconcile
          </button>
        </div>
      </div>

      <div className="mb-3 rounded border border-amber-500 bg-amber-500/10 p-2 text-xs text-amber-300">
        <span className="font-semibold uppercase">Paper trading only.</span>{" "}
        {mode?.notice ?? "Simulated fills only — no broker order is ever submitted."}
        {mode && (
          <span className="text-[var(--muted-foreground)]">
            {" "}Spread {(mode.spread_pct * 100).toFixed(3)}% · slippage {(mode.slippage_pct * 100).toFixed(3)}%
            {" "}· volume participation cap {(mode.participation_pct * 100).toFixed(2)}% · candles {mode.candle_interval}
          </span>
        )}
      </div>

      {message && (
        <div className={`mb-3 rounded border p-2 text-xs ${
          message.type === "success"
            ? "border-green-600 bg-green-600/10 text-green-400"
            : "border-red-600 bg-red-600/10 text-red-400"
        }`}>
          {message.text}
        </div>
      )}

      {showOrderForm && (
        <div className="mb-4 space-y-2 rounded border border-[var(--border)] bg-[var(--background)] p-3">
          <div className="text-xs font-medium text-[var(--muted-foreground)]">
            Create a simulated order. The idempotency key makes retries safe — re-sending a key returns the original order.
          </div>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--muted-foreground)]">Idempotency key</span>
              <input
                value={orderForm.idempotency_key}
                onChange={(e) => setOrderForm({ ...orderForm, idempotency_key: e.target.value })}
                placeholder="aapl-entry-1"
                className="rounded border bg-[var(--input)] px-2 py-1.5 text-xs"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--muted-foreground)]">Ticker</span>
              <input
                value={orderForm.ticker}
                onChange={(e) => setOrderForm({ ...orderForm, ticker: e.target.value.toUpperCase() })}
                placeholder="AAPL"
                className="rounded border bg-[var(--input)] px-2 py-1.5 text-xs"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--muted-foreground)]">Asset type</span>
              <select
                value={orderForm.asset_type}
                onChange={(e) => setOrderForm({ ...orderForm, asset_type: e.target.value })}
                className="rounded border bg-[var(--input)] px-2 py-1.5 text-xs"
              >
                <option value="stock">Stock</option>
                <option value="crypto">Crypto</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--muted-foreground)]">Side</span>
              <select
                value={orderForm.side}
                onChange={(e) => setOrderForm({ ...orderForm, side: e.target.value })}
                className="rounded border bg-[var(--input)] px-2 py-1.5 text-xs"
              >
                <option value="BUY">BUY</option>
                <option value="SELL">SELL</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--muted-foreground)]">Order type</span>
              <select
                value={orderForm.order_type}
                onChange={(e) => setOrderForm({ ...orderForm, order_type: e.target.value as PaperOrderType })}
                className="rounded border bg-[var(--input)] px-2 py-1.5 text-xs"
              >
                {PAPER_ORDER_TYPES.map((type) => (
                  <option key={type} value={type}>{TYPE_LABELS[type]}</option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--muted-foreground)]">Quantity</span>
              <input
                type="number"
                step="0.0001"
                value={orderForm.quantity}
                onChange={(e) => setOrderForm({ ...orderForm, quantity: e.target.value })}
                className="rounded border bg-[var(--input)] px-2 py-1.5 text-xs"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--muted-foreground)]">Time in force</span>
              <select
                value={orderForm.time_in_force}
                onChange={(e) => setOrderForm({ ...orderForm, time_in_force: e.target.value })}
                className="rounded border bg-[var(--input)] px-2 py-1.5 text-xs"
              >
                <option value="gtc">GTC</option>
                <option value="day">DAY</option>
              </select>
            </label>
            {fields.includes("limit_price") && priceInput("limit_price", "Limit price")}
            {fields.includes("stop_price") && priceInput("stop_price", "Stop price")}
            {fields.includes("reference_price") && priceInput("reference_price", "Reference price")}
            {fields.includes("take_profit_price") && priceInput("take_profit_price", "Take profit")}
            {fields.includes("stop_loss_price") && priceInput("stop_loss_price", "Stop loss")}
            {fields.includes("trail_percent") && priceInput("trail_percent", "Trail %", "0.1")}
            {fields.includes("trail_amount") && priceInput("trail_amount", "Trail $")}
          </div>
          <div className="flex justify-end">
            <button
              onClick={submitOrder}
              disabled={busy}
              className="rounded bg-[var(--primary)] px-4 py-1.5 text-xs font-medium text-[var(--primary-foreground)] disabled:opacity-50"
            >
              {busy ? "Working..." : "Create Paper Order"}
            </button>
          </div>
        </div>
      )}

      {showProcessForm && (
        <div className="mb-4 space-y-2 rounded border border-[var(--border)] bg-[var(--background)] p-3">
          <div className="text-xs font-medium text-[var(--muted-foreground)]">
            Advance working orders through one deterministic candle. Leave the candle empty to replay
            stored {mode?.candle_interval ?? "daily"} candles from data-ingestion instead.
          </div>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-7">
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--muted-foreground)]">Ticker</span>
              <input
                value={candleForm.ticker}
                onChange={(e) => setCandleForm({ ...candleForm, ticker: e.target.value.toUpperCase() })}
                className="rounded border bg-[var(--input)] px-2 py-1.5 text-xs"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--muted-foreground)]">Candle time</span>
              <input
                type="datetime-local"
                value={candleForm.timestamp}
                onChange={(e) => setCandleForm({ ...candleForm, timestamp: e.target.value })}
                className="rounded border bg-[var(--input)] px-2 py-1.5 text-xs"
              />
            </label>
            {(["open", "high", "low", "close", "volume"] as const).map((field) => (
              <label key={field} className="flex flex-col gap-1 text-xs">
                <span className="capitalize text-[var(--muted-foreground)]">{field}</span>
                <input
                  type="number"
                  step={field === "volume" ? "1" : "0.01"}
                  value={candleForm[field]}
                  onChange={(e) => setCandleForm({ ...candleForm, [field]: e.target.value })}
                  className="rounded border bg-[var(--input)] px-2 py-1.5 text-xs"
                />
              </label>
            ))}
          </div>
          <div className="flex justify-end">
            <button
              onClick={process}
              disabled={busy}
              className="rounded bg-[var(--primary)] px-4 py-1.5 text-xs font-medium text-[var(--primary-foreground)] disabled:opacity-50"
            >
              {busy ? "Working..." : "Process"}
            </button>
          </div>
        </div>
      )}

      {reconciliation && (
        <div className="mb-4 grid grid-cols-2 gap-2 rounded border border-[var(--border)] bg-[var(--background)] p-3 text-xs md:grid-cols-4">
          <div><div className="text-[var(--muted-foreground)]">Balance</div>{money(reconciliation.balance)}</div>
          <div><div className="text-[var(--muted-foreground)]">Reserved cash</div>{money(reconciliation.reserved_cash)}</div>
          <div><div className="text-[var(--muted-foreground)]">Position capital</div>{money(reconciliation.position_capital)}</div>
          <div><div className="text-[var(--muted-foreground)]">Equity</div>{money(reconciliation.equity)}</div>
          <div><div className="text-[var(--muted-foreground)]">Fills</div>{reconciliation.fills}</div>
          <div><div className="text-[var(--muted-foreground)]">Filled quantity</div>{quantity(reconciliation.filled_quantity)}</div>
          <div>
            <div className="text-[var(--muted-foreground)]">Fills match orders</div>
            {reconciliation.fills_match_orders ? "Yes" : "No"}
          </div>
          <div>
            <div className="text-[var(--muted-foreground)]">Equity balanced</div>
            {reconciliation.equity_balanced ? "Yes" : "No"}
          </div>
        </div>
      )}

      <div className="mb-3 grid grid-cols-2 gap-2 md:grid-cols-5">
        <select
          value={filters.status ?? ""}
          onChange={(e) => setFilters({ ...filters, status: e.target.value })}
          className="rounded border bg-[var(--input)] px-2 py-1.5 text-xs"
        >
          <option value="">All statuses</option>
          {PAPER_ORDER_STATUSES.map((status) => (
            <option key={status} value={status}>{statusLabel(status)}</option>
          ))}
        </select>
        <input
          value={filters.ticker ?? ""}
          onChange={(e) => setFilters({ ...filters, ticker: e.target.value.toUpperCase() })}
          placeholder="Ticker"
          className="rounded border bg-[var(--input)] px-2 py-1.5 text-xs"
        />
        <select
          value={filters.asset_type ?? ""}
          onChange={(e) => setFilters({ ...filters, asset_type: e.target.value })}
          className="rounded border bg-[var(--input)] px-2 py-1.5 text-xs"
        >
          <option value="">All asset types</option>
          <option value="stock">Stock</option>
          <option value="crypto">Crypto</option>
        </select>
        <select
          value={filters.side ?? ""}
          onChange={(e) => setFilters({ ...filters, side: e.target.value })}
          className="rounded border bg-[var(--input)] px-2 py-1.5 text-xs"
        >
          <option value="">All sides</option>
          <option value="BUY">BUY</option>
          <option value="SELL">SELL</option>
        </select>
        <select
          value={filters.order_type ?? ""}
          onChange={(e) => setFilters({ ...filters, order_type: e.target.value })}
          className="rounded border bg-[var(--input)] px-2 py-1.5 text-xs"
        >
          <option value="">All order types</option>
          {PAPER_ORDER_TYPES.map((type) => (
            <option key={type} value={type}>{TYPE_LABELS[type]}</option>
          ))}
        </select>
      </div>

      {orders.length === 0 ? (
        <p className="text-sm text-[var(--muted-foreground)]">No paper orders match these filters.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs text-[var(--muted-foreground)]">
                <th className="pb-2">ID</th>
                <th className="pb-2">Ticker</th>
                <th className="pb-2">Side</th>
                <th className="pb-2">Type</th>
                <th className="pb-2">Qty</th>
                <th className="pb-2">Filled</th>
                <th className="pb-2">Avg fill</th>
                <th className="pb-2">Costs</th>
                <th className="pb-2">Status</th>
                <th className="pb-2">Created</th>
                <th className="pb-2"></th>
              </tr>
            </thead>
            <tbody>
              {orders.map((order) => (
                <tr key={order.id} className="border-b border-[var(--border)] align-top">
                  <td className="py-1.5 font-mono text-xs">{order.id}</td>
                  <td className="font-medium">
                    {order.ticker}
                    <span className="ml-1 text-xs text-[var(--muted-foreground)]">{order.asset_type}</span>
                  </td>
                  <td className={order.side === "BUY" ? "text-green-400" : "text-red-400"}>{order.side}</td>
                  <td className="text-xs">{order.order_type}{order.role !== "standalone" ? ` · ${order.role}` : ""}</td>
                  <td>{quantity(order.quantity)}</td>
                  <td>{quantity(order.filled_quantity)}</td>
                  <td>{money(order.average_fill_price, 4)}</td>
                  <td>{money(order.costs_total, 4)}</td>
                  <td>
                    <span className={`rounded px-1.5 py-0.5 text-xs ${STATUS_STYLES[order.status] ?? "bg-gray-800"}`}>
                      {statusLabel(order.status)}
                    </span>
                  </td>
                  <td className="text-xs text-[var(--muted-foreground)]">{formatDateTime(order.created_at)}</td>
                  <td className="whitespace-nowrap text-right">
                    <button
                      onClick={() => { void toggleExpanded(order.id); }}
                      className="rounded bg-[var(--secondary)] px-2 py-1 text-xs hover:bg-[var(--accent)]"
                    >
                      {expanded === order.id ? "Hide" : "Detail"}
                    </button>
                    {!TERMINAL_STATUSES.includes(order.status) && (
                      <button
                        onClick={() => { void cancel(order); }}
                        disabled={busy}
                        className="ml-1 rounded bg-[var(--secondary)] px-2 py-1 text-xs hover:bg-[var(--accent)] disabled:opacity-50"
                      >
                        Cancel
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {expanded != null && details[expanded] && (
            <div className="mt-3">
              <OrderDetail order={details[expanded]} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
