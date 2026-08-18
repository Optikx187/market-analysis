const TRADES_CHANGED = "trades:changed";

export const notifyTradesChanged = () => window.dispatchEvent(new Event(TRADES_CHANGED));

export const onTradesChanged = (handler: () => void) => {
  window.addEventListener(TRADES_CHANGED, handler);
  return () => window.removeEventListener(TRADES_CHANGED, handler);
};
