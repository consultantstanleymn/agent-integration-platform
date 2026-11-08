"""OAuth2 client-credentials helper (RFC 6749 section 4.4) with token caching."""

import time
from collections.abc import Callable

import httpx


class OAuthError(RuntimeError):
    pass


class ClientCredentialsTokenProvider:
    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        *,
        client: httpx.Client,
        scope: str | None = None,
        skew: float = 30,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.token_url = token_url
        self.client_id = client_id
        self._secret = client_secret
        self.scope = scope
        self.skew = skew
        self._client = client
        self._clock = clock
        self._token: str | None = None
        self._expires_at = 0.0

    def get_token(self) -> str:
        """Return a cached token, or fetch a new one when it is about to expire."""
        if self._token and self._clock() < self._expires_at - self.skew:
            return self._token
        form = {"grant_type": "client_credentials"}
        if self.scope:
            form["scope"] = self.scope
        try:
            r = self._client.post(self.token_url, data=form, auth=(self.client_id, self._secret))
            r.raise_for_status()
            data = r.json()
            self._token = str(data["access_token"])
            self._expires_at = self._clock() + float(data.get("expires_in", 300))
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise OAuthError(f"token request failed: {type(exc).__name__}") from exc
        return self._token

    def invalidate(self) -> None:
        self._token = None
