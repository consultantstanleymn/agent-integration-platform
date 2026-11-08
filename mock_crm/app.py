"""A tiny mock CRM for demos and tests. OAuth2 client credentials, a REST resource, a webhook receiver.

Credentials come from env: MOCK_CRM_CLIENT_ID / MOCK_CRM_CLIENT_SECRET (demo defaults below).
"""

import os
import secrets
import time
from typing import Annotated, Any

from fastapi import FastAPI, Form, Header, HTTPException, Request

from aip.integrations.webhooks import verify

CLIENT_ID = os.environ.get("MOCK_CRM_CLIENT_ID", "demo-client")
CLIENT_SECRET = os.environ.get("MOCK_CRM_CLIENT_SECRET", "demo-secret")
WEBHOOK_SECRET = os.environ.get("MOCK_CRM_WEBHOOK_SECRET", "")  # empty = accept unsigned (demo)

CUSTOMERS: dict[str, dict[str, Any]] = {
    "c-100": {
        "id": "c-100",
        "name": "Acme Dental",
        "plan": "premium",
        "status": "active",
        "account_manager": "Priya Shah",
        "notes": "Uses the printer integration. Renewal in March.",
    },
    "c-200": {
        "id": "c-200",
        "name": "Blue Harbor Clinic",
        "plan": "standard",
        "status": "past_due",
        "account_manager": "Marcus Lee",
        "notes": "Invoice is 20 days overdue.",
    },
}

app = FastAPI(title="Mock CRM")
_tokens: dict[str, float] = {}
received: list[dict[str, Any]] = []


@app.post("/oauth/token")
async def token(request: Request, grant_type: Annotated[str, Form()]) -> dict[str, Any]:
    import base64

    auth = request.headers.get("authorization", "")
    try:
        cid, _, secret = base64.b64decode(auth.split(" ", 1)[1]).decode().partition(":")
    except (IndexError, ValueError):
        raise HTTPException(401, "basic auth required") from None
    ok = secrets.compare_digest(cid, CLIENT_ID) and secrets.compare_digest(secret, CLIENT_SECRET)
    if grant_type != "client_credentials" or not ok:
        raise HTTPException(401, "invalid_client")
    tok = secrets.token_urlsafe(24)
    _tokens[tok] = time.time() + 300
    return {"access_token": tok, "token_type": "Bearer", "expires_in": 300}


def _check(authorization: str | None) -> None:
    tok = (authorization or "").removeprefix("Bearer ").strip()
    if _tokens.get(tok, 0) < time.time():
        raise HTTPException(401, "invalid or expired token")


@app.get("/customers/{customer_id}")
def customer(customer_id: str, authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    _check(authorization)
    if customer_id not in CUSTOMERS:
        raise HTTPException(404, "not found")
    return CUSTOMERS[customer_id]


@app.post("/hooks/receive")
async def receive(request: Request) -> dict[str, bool]:
    body = await request.body()
    if WEBHOOK_SECRET and not verify(
        WEBHOOK_SECRET, request.headers.get("x-timestamp"), request.headers.get("x-signature"), body
    ):
        raise HTTPException(401, "bad signature")
    received.append({"body": body.decode(), "idempotency_key": request.headers.get("idempotency-key")})
    return {"ok": True}


@app.get("/hooks/received")
def list_received() -> list[dict[str, Any]]:
    return received
