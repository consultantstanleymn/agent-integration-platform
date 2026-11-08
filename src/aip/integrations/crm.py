"""Generic REST connector example. Talks to the mock CRM with an OAuth2 bearer token."""

from typing import Any

import httpx

from aip.integrations.oauth import ClientCredentialsTokenProvider


class CrmClient:
    def __init__(self, base_url: str, tokens: ClientCredentialsTokenProvider, client: httpx.Client) -> None:
        self.base_url = base_url.rstrip("/")
        self.tokens = tokens
        self._client = client

    def _get(self, path: str) -> httpx.Response:
        r = self._client.get(
            f"{self.base_url}{path}", headers={"Authorization": f"Bearer {self.tokens.get_token()}"}
        )
        if r.status_code == 401:  # token may have been revoked: refresh once and retry
            self.tokens.invalidate()
            r = self._client.get(
                f"{self.base_url}{path}",
                headers={"Authorization": f"Bearer {self.tokens.get_token()}"},
            )
        return r

    def get_customer(self, customer_id: str) -> dict[str, Any] | None:
        r = self._get(f"/customers/{customer_id}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return data


def customer_to_text(c: dict[str, Any]) -> str:
    """Turn a CRM record into a markdown document the RAG pipeline can index."""
    return (
        f"# Customer {c['name']}\n\n"
        f"Plan: {c['plan']}. Account status: {c['status']}. Account manager: {c['account_manager']}.\n\n"
        f"Notes: {c.get('notes', '')}"
    )
