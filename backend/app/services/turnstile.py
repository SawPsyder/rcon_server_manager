"""Cloudflare Turnstile server-side verification.

Browser -> our backend -> Cloudflare. The token is never verified in the
browser, and the secret never leaves the server.

Turnstile is optional: with TURNSTILE_SITE_KEY or TURNSTILE_SECRET unset the
verifier is a no-op and the gated endpoints behave exactly as they did before.
"""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def verify_turnstile(token: str, remote_ip: str = "") -> bool:
    """Redeem a Turnstile token. Fails closed.

    A token is single-use: Cloudflare rejects a second redemption of the same
    value with ``timeout-or-duplicate``. Callers that keep the user on the page
    after a failure must reset the widget so the retry carries a fresh token.
    """
    settings = get_settings()
    if not settings.turnstile_enabled:
        return True
    if not token:
        return False

    payload = {
        # Read from the environment via pydantic-settings. Never logged.
        "secret": settings.turnstile_secret,
        "response": token,
    }
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        with httpx.Client(timeout=settings.turnstile_timeout) as client:
            response = client.post(SITEVERIFY_URL, data=payload)
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError):
        # Network failure, non-2xx, or a non-JSON body. Treat an unverifiable
        # token as a failed one - the alternative is that a Cloudflare outage
        # silently removes the protection.
        logger.warning("Turnstile siteverify unavailable; rejecting the request")
        return False

    if not isinstance(result, dict) or result.get("success") is not True:
        codes = result.get("error-codes") if isinstance(result, dict) else None
        logger.info("Turnstile rejected a token: %s", codes)
        return False

    return True
