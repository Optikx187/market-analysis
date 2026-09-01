import { useCallback, useEffect, useState } from "react";
import {
  acknowledgeLiveTrading,
  apiErrorMessage,
  cancelAllLiveOrders,
  cancelLiveOrder,
  disableLiveTrading,
  enableLiveTrading,
  fetchLiveOrders,
  fetchLiveStatus,
  previewLiveOrder,
  reconcileLiveOrders,
  revokeLiveTrading,
  submitLiveOrder,
  verifyLiveAudit,
  type LiveModeStatus,
  type LiveOrder,
  type LiveOrderInput,
  type LivePreview,
} from "@/lib/api";

const OPEN_STATUSES = ["new", "submitted", "accepted", "partially_filled"];

const STATUS_STYLES: Record<string, string> = {
  new: "bg-gray-800 text-gray-300",
  submitted: "bg-blue-900 text-blue-300",
  accepted: "bg-blue-900 text-blue-300",
  partially_filled: "bg-amber-900 text-amber-300",
  filled: "bg-green-900 text-green-300",
  canceled: "bg-gray-800 text-gray-400",
  rejected: "bg-red-900 text-red-300",
  expired: "bg-purple-900 text-purple-300",
  unknown: "bg-orange-900 text-orange-300",
};

const emptyForm = {
  ticker: "",
  asset_type: "stock",
  side: "BUY",
  order_type: "limit",
  quantity: "",
  limit_price: "",
  stop_price: "",
  time_in_force: "day",
};

const money = (value: number | null | undefined, digits = 2): string =>
  value == null ? "—" : `$${value.toFixed(digits)}`;

const numberOrUndefined = (raw: string): number | undefined => {
  if (!raw.trim()) return undefined;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : undefined;
};

/** Live orders reach a real broker, so this view is deliberately loud and gated. */
export default function LiveTradingPanel() {
  const [status, setStatus] = useState<LiveModeStatus | null>(null);
  const [orders, setOrders] = useState<LiveOrder[]>([]);
  const [form, setForm] = useState({ ...emptyForm });
  const [preview, setPreview] = useState<LivePreview | null>(null);
  const [phrase, setPhrase] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [nextStatus, list] = await Promise.all([fetchLiveStatus(), fetchLiveOrders()]);
      setStatus(nextStatus);
      setOrders(list.orders);
    } catch (err) {
      setError(apiErrorMessage(err, "Could not load live trading state"));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const run = async (action: () => Promise<string>) => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      setMessage(await action());
      await load();
    } catch (err) {
      setError(apiErrorMessage(err, err instanceof Error ? err.message : "The action failed"));
    } finally {
      setBusy(false);
    }
  };

  const orderInput = (): LiveOrderInput => ({
    ticker: form.ticker.trim().toUpperCase(),
    asset_type: form.asset_type,
    side: form.side,
    order_type: form.order_type,
    quantity: Number(form.quantity),
    limit_price: numberOrUndefined(form.limit_price),
    stop_price: numberOrUndefined(form.stop_price),
    time_in_force: form.time_in_force,
  });

  const onPreview = () =>
    run(async () => {
      const result = await previewLiveOrder(orderInput());
      setPreview(result);
      return result.submittable
        ? "Preview passed every safety gate. Review it, then approve to submit."
        : "Preview blocked. Resolve the blockers below before approving.";
    });

  const onApprove = () =>
    run(async () => {
      if (!preview) throw new Error("Preview the order before approving it");
      const submitted = await submitLiveOrder({
        ...orderInput(),
        idempotency_key: `live-${preview.approval_fingerprint.slice(0, 16)}`,
        approval_fingerprint: preview.approval_fingerprint,
      });
      setPreview(null);
      setForm({ ...emptyForm });
      return submitted.status === "rejected"
        ? `Order rejected before reaching the broker: ${submitted.reject_reason}`
        : `Live order ${submitted.id} is ${submitted.status} at ${submitted.broker}`;
    });

  const liveBadge = status?.armed ? (
    <span className="rounded bg-red-700 px-2 py-1 text-xs font-bold uppercase tracking-wide text-white">
      Live · real money
    </span>
  ) : (
    <span className="rounded bg-gray-700 px-2 py-1 text-xs font-bold uppercase tracking-wide text-gray-200">
      Live disarmed
    </span>
  );

  return (
    <div className="space-y-4">
      <section
        className={`rounded border p-4 ${
          status?.armed ? "border-red-600 bg-red-950/40" : "border-gray-700 bg-gray-900/40"
        }`}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-semibold">Live broker execution</h2>
          {liveBadge}
        </div>
        <p className="mt-2 text-sm text-[var(--muted-foreground)]">{status?.notice}</p>
        <dl className="mt-3 grid grid-cols-2 gap-2 text-sm md:grid-cols-4">
          <div><dt className="text-[var(--muted-foreground)]">Configuration</dt><dd>{status?.config_enabled ? "Enabled" : "Disabled"}</dd></div>
          <div><dt className="text-[var(--muted-foreground)]">Acknowledged</dt><dd>{status?.acknowledged ? `Yes — ${status.acknowledged_by}` : "No"}</dd></div>
          <div><dt className="text-[var(--muted-foreground)]">Kill switch</dt><dd>{status?.trading_disabled ? `Engaged — ${status.disabled_reason}` : "Clear"}</dd></div>
          <div><dt className="text-[var(--muted-foreground)]">Broker</dt><dd>{status?.broker ?? "none"} {status?.sandbox ? "(sandbox)" : "(production)"}</dd></div>
        </dl>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <input
            aria-label="Acknowledgement phrase"
            className="rounded border border-gray-600 bg-transparent px-2 py-1 text-sm"
            placeholder={status?.acknowledgement_phrase ?? "acknowledgement phrase"}
            value={phrase}
            onChange={(event) => setPhrase(event.target.value)}
          />
          <button
            className="rounded bg-amber-700 px-3 py-1 text-sm text-white disabled:opacity-50"
            disabled={busy}
            onClick={() => run(async () => {
              await acknowledgeLiveTrading(phrase);
              setPhrase("");
              return "Live trading acknowledged";
            })}
          >
            Acknowledge
          </button>
          <button
            className="rounded border border-gray-600 px-3 py-1 text-sm disabled:opacity-50"
            disabled={busy}
            onClick={() => run(async () => {
              await revokeLiveTrading();
              return "Acknowledgement revoked";
            })}
          >
            Revoke
          </button>
          <button
            className="rounded bg-red-700 px-3 py-1 text-sm font-semibold text-white disabled:opacity-50"
            disabled={busy}
            onClick={() => run(async () => {
              await disableLiveTrading("Disabled from the live trading panel");
              return "Kill switch engaged; no new live order can be submitted";
            })}
          >
            Disable trading
          </button>
          <button
            className="rounded border border-gray-600 px-3 py-1 text-sm disabled:opacity-50"
            disabled={busy}
            onClick={() => run(async () => {
              await enableLiveTrading();
              return "Kill switch cleared";
            })}
          >
            Re-enable
          </button>
          <button
            className="rounded bg-red-800 px-3 py-1 text-sm font-semibold text-white disabled:opacity-50"
            disabled={busy}
            onClick={() => run(async () => {
              const result = await cancelAllLiveOrders("Cancel-all from the live trading panel");
              return `Cancel-all: ${result.canceled} canceled, ${result.failed} failed`;
            })}
          >
            Cancel all orders
          </button>
          <button
            className="rounded border border-gray-600 px-3 py-1 text-sm disabled:opacity-50"
            disabled={busy}
            onClick={() => run(async () => {
              const result = await reconcileLiveOrders();
              return `Reconciled ${result.checked} order(s); ${result.out_of_sync} corrected, ${result.errors} error(s)`;
            })}
          >
            Reconcile with broker
          </button>
          <button
            className="rounded border border-gray-600 px-3 py-1 text-sm disabled:opacity-50"
            disabled={busy}
            onClick={() => run(async () => {
              const result = await verifyLiveAudit();
              return result.intact
                ? `Audit chain intact across ${result.entries} entries`
                : `Audit chain broken at entry ${result.broken_entry_id}`;
            })}
          >
            Verify audit chain
          </button>
        </div>
      </section>

      {message && <p className="rounded border border-blue-800 bg-blue-950/40 p-2 text-sm">{message}</p>}
      {error && <p className="rounded border border-red-800 bg-red-950/40 p-2 text-sm text-red-300">{error}</p>}

      <section className="rounded border border-gray-700 p-4">
        <h3 className="font-semibold">Preview a live order</h3>
        <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-4">
          <label className="text-sm">Ticker
            <input className="mt-1 w-full rounded border border-gray-600 bg-transparent px-2 py-1"
              value={form.ticker} onChange={(e) => setForm({ ...form, ticker: e.target.value })} />
          </label>
          <label className="text-sm">Asset type
            <select className="mt-1 w-full rounded border border-gray-600 bg-transparent px-2 py-1"
              value={form.asset_type} onChange={(e) => setForm({ ...form, asset_type: e.target.value })}>
              <option value="stock">Stock</option>
              <option value="crypto">Crypto</option>
            </select>
          </label>
          <label className="text-sm">Side
            <select className="mt-1 w-full rounded border border-gray-600 bg-transparent px-2 py-1"
              value={form.side} onChange={(e) => setForm({ ...form, side: e.target.value })}>
              <option value="BUY">BUY</option>
              <option value="SELL">SELL</option>
            </select>
          </label>
          <label className="text-sm">Order type
            <select className="mt-1 w-full rounded border border-gray-600 bg-transparent px-2 py-1"
              value={form.order_type} onChange={(e) => setForm({ ...form, order_type: e.target.value })}>
              <option value="market">Market</option>
              <option value="limit">Limit</option>
              <option value="stop">Stop</option>
              <option value="stop_limit">Stop-Limit</option>
            </select>
          </label>
          <label className="text-sm">Quantity
            <input className="mt-1 w-full rounded border border-gray-600 bg-transparent px-2 py-1"
              value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} />
          </label>
          <label className="text-sm">Limit price
            <input className="mt-1 w-full rounded border border-gray-600 bg-transparent px-2 py-1"
              value={form.limit_price} onChange={(e) => setForm({ ...form, limit_price: e.target.value })} />
          </label>
          <label className="text-sm">Stop price
            <input className="mt-1 w-full rounded border border-gray-600 bg-transparent px-2 py-1"
              value={form.stop_price} onChange={(e) => setForm({ ...form, stop_price: e.target.value })} />
          </label>
          <label className="text-sm">Time in force
            <select className="mt-1 w-full rounded border border-gray-600 bg-transparent px-2 py-1"
              value={form.time_in_force} onChange={(e) => setForm({ ...form, time_in_force: e.target.value })}>
              <option value="day">Day</option>
              <option value="gtc">GTC</option>
            </select>
          </label>
        </div>
        <div className="mt-3 flex gap-2">
          <button className="rounded bg-blue-700 px-3 py-1 text-sm text-white disabled:opacity-50"
            disabled={busy} onClick={onPreview}>Preview</button>
          <button
            className="rounded bg-red-700 px-3 py-1 text-sm font-semibold text-white disabled:opacity-50"
            disabled={busy || !preview?.submittable}
            onClick={onApprove}
            title={preview?.submittable ? "Submit this order to the broker" : "Preview must pass every gate first"}
          >
            Approve &amp; submit live order
          </button>
        </div>

        {preview && (
          <div className="mt-4 space-y-2 text-sm">
            <p>
              Estimated notional {money(preview.estimated_notional)} · reference {money(preview.reference_price)} ·
              buying power {money(preview.buying_power)}
            </p>
            <ul className="space-y-1">
              {preview.checks.map((check) => (
                <li key={check.name} className={check.passed ? "text-green-400" : check.blocking ? "text-red-400" : "text-amber-400"}>
                  {check.passed ? "PASS" : check.blocking ? "BLOCK" : "WARN"} · {check.name} — {check.detail}
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <section className="rounded border border-gray-700 p-4">
        <h3 className="font-semibold">Live orders</h3>
        {orders.length === 0 ? (
          <p className="mt-2 text-sm text-[var(--muted-foreground)]">No live orders have been created.</p>
        ) : (
          <table className="mt-3 w-full text-left text-sm">
            <thead className="text-[var(--muted-foreground)]">
              <tr><th>ID</th><th>Ticker</th><th>Side</th><th>Type</th><th>Qty</th><th>Filled</th><th>Status</th><th>Broker order</th><th /></tr>
            </thead>
            <tbody>
              {orders.map((order) => (
                <tr key={order.id} className="border-t border-gray-800">
                  <td>{order.id}</td>
                  <td>{order.ticker}</td>
                  <td>{order.side}</td>
                  <td>{order.order_type}</td>
                  <td>{order.quantity}</td>
                  <td>{order.filled_quantity}</td>
                  <td>
                    <span className={`rounded px-2 py-0.5 text-xs ${STATUS_STYLES[order.status] ?? ""}`}>
                      {order.status.replace("_", " ")}
                    </span>
                    {order.reject_reason && (
                      <span className="ml-2 text-xs text-red-400">{order.reject_reason}</span>
                    )}
                  </td>
                  <td>{order.broker_order_id ?? "—"}</td>
                  <td>
                    {OPEN_STATUSES.includes(order.status) && (
                      <button
                        className="rounded border border-gray-600 px-2 py-0.5 text-xs disabled:opacity-50"
                        disabled={busy}
                        onClick={() => run(async () => {
                          await cancelLiveOrder(order.id, "Canceled from the live trading panel");
                          return `Order ${order.id} canceled`;
                        })}
                      >
                        Cancel
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
