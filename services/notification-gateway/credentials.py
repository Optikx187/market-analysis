import logging
import time

import httpx


logger = logging.getLogger(__name__)
_CACHE_SECONDS = 30.0
_cache: dict[str, tuple[float, dict[str, str]]] = {}


async def get_provider_credentials(
    provider: str,
    *,
    portfolio_engine_url: str,
    internal_service_token: str,
) -> dict[str, str]:
    provider = provider.strip().lower()
    cached = _cache.get(provider)
    now = time.monotonic()
    if cached and cached[0] > now:
        return cached[1].copy()
    if not internal_service_token.strip():
        return {}

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                f"{portfolio_engine_url}/internal/credentials/{provider}",
                headers={"X-Internal-Service-Token": internal_service_token},
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "Encrypted %s credentials are unavailable: %s",
            provider,
            type(exc).__name__,
        )
        return {}

    values = {
        str(key): str(value)
        for key, value in payload.items()
        if isinstance(key, str) and isinstance(value, str) and value
    }
    _cache[provider] = (now + _CACHE_SECONDS, values)
    return values.copy()
