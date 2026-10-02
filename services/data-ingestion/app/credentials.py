import logging
import time

import httpx

from app.config import settings


logger = logging.getLogger(__name__)
_CACHE_SECONDS = 30.0
_cache: dict[str, tuple[float, dict[str, str]]] = {}


async def get_provider_credentials(provider: str) -> dict[str, str]:
    provider = provider.strip().lower()
    cached = _cache.get(provider)
    now = time.monotonic()
    if cached and cached[0] > now:
        return cached[1].copy()
    if not settings.INTERNAL_SERVICE_TOKEN.strip():
        return {}

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                f"{settings.PORTFOLIO_ENGINE_URL}/internal/credentials/{provider}",
                headers={"X-Internal-Service-Token": settings.INTERNAL_SERVICE_TOKEN},
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
