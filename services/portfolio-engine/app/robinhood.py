"""Robinhood integration via robin_stocks.

Pulls real-time buying_power and enforces the capital overspend guardrail:
if optimal trade > 5% of liquid Robinhood balance, cancel the signal.
"""

import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

_logged_in = False


def _login() -> bool:
    global _logged_in
    if _logged_in:
        return True
    if not settings.ROBINHOOD_USERNAME or not settings.ROBINHOOD_PASSWORD:
        logger.info("Robinhood credentials not configured, skipping")
        return False
    try:
        import robin_stocks.robinhood as rh
        login_args = {
            "username": settings.ROBINHOOD_USERNAME,
            "password": settings.ROBINHOOD_PASSWORD,
        }
        if settings.ROBINHOOD_TOTP:
            import pyotp
            totp = pyotp.TOTP(settings.ROBINHOOD_TOTP)
            login_args["mfa_code"] = totp.now()
        rh.login(**login_args)
        _logged_in = True
        logger.info("Robinhood login successful")
        return True
    except Exception as e:
        logger.error(f"Robinhood login failed: {e}")
        return False


def get_buying_power() -> Optional[float]:
    if not _login():
        return None
    try:
        import robin_stocks.robinhood as rh
        profile = rh.profiles.load_account_profile()
        buying_power = float(profile.get("buying_power", 0))
        return buying_power
    except Exception as e:
        logger.error(f"Failed to fetch buying power: {e}")
        return None


def execute_robinhood_order(
    ticker: str, direction: str, quantity: float, price: float,
) -> dict:
    """Execute a trade through Robinhood.

    All trades go through Robinhood when credentials are configured.
    Returns order result or error info.
    """
    if not _login():
        return {
            "executed": False,
            "reason": "Robinhood not connected — paper trade only",
        }
    try:
        import robin_stocks.robinhood as rh

        if direction == "BUY":
            order = rh.orders.order_buy_limit(
                symbol=ticker,
                quantity=quantity,
                limitPrice=round(price, 2),
                timeInForce="gfd",
            )
        elif direction == "SELL":
            order = rh.orders.order_sell_limit(
                symbol=ticker,
                quantity=quantity,
                limitPrice=round(price, 2),
                timeInForce="gfd",
            )
        else:
            return {"executed": False, "reason": f"Unknown direction: {direction}"}

        order_id = order.get("id", "unknown")
        logger.info(f"Robinhood order placed: {direction} {quantity} {ticker} @ ${price} (order: {order_id})")
        return {
            "executed": True,
            "order_id": order_id,
            "direction": direction,
            "ticker": ticker,
            "quantity": quantity,
            "price": price,
        }
    except Exception as e:
        logger.error(f"Robinhood order failed: {e}")
        return {"executed": False, "reason": str(e)}


def check_capital_overspend(optimal_trade_usd: float) -> dict:
    """Check if the optimal trade exceeds 5% of Robinhood liquid balance.

    Returns:
        {"allowed": bool, "buying_power": float|None,
         "limit_usd": float|None, "overspend": bool}
    """
    buying_power = get_buying_power()
    if buying_power is None:
        return {
            "allowed": True,
            "buying_power": None,
            "limit_usd": None,
            "overspend": False,
            "reason": "Robinhood not connected — skipping guardrail",
        }

    limit = buying_power * settings.ROBINHOOD_CAPITAL_LIMIT_PCT
    overspend = optimal_trade_usd > limit

    return {
        "allowed": not overspend,
        "buying_power": round(buying_power, 2),
        "limit_usd": round(limit, 2),
        "overspend": overspend,
        "reason": (
            f"CAPITAL OVERSPEND WARNING: Trade ${optimal_trade_usd:.2f} "
            f"exceeds 5% limit (${limit:.2f}) of buying power ${buying_power:.2f}"
            if overspend else "Within capital limits"
        ),
    }
