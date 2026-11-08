import hashlib
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aip.api.app import create_app
from aip.config import Settings
from aip.db import make_engine
from aip.integrations.webhooks import sign
from aip.models import Base
from aip.providers.fake import FakeProvider

KB = """# Refund policy

Customers can request a full refund within 30 days of purchase.

# Shipping

Standard shipping takes 3 to 5 business days.
"""


class Env:
    def __init__(self) -> None:
        self.settings = Settings(
            admin_token="admin-secret",
            database_url="sqlite://",  # type: ignore[arg-type]
            rate_limit_per_minute=1000,
            allow_private_webhook_targets=True,
        )
        self.engine = make_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.requests: list[httpx.Request] = []
        self.http_handler = lambda req: httpx.Response(200, json={"ok": True})
        self.http = httpx.Client(transport=httpx.MockTransport(self._handle))
        self.app = create_app(
            self.settings,
            engine=self.engine,
            chat=FakeProvider(),
            embedder=FakeProvider(),
            http=self.http,
        )
        self.client = TestClient(self.app)

    def _handle(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        return self.http_handler(req)

    def tenant(
        self, name: str = "acme", tools: list[str] | None = None, webhook_url: str | None = None
    ) -> dict:
        body: dict = {"name": name}
        if tools is not None:
            body["allowed_tools"] = tools
        if webhook_url:
            body["webhook_url"] = webhook_url
        r = self.client.post("/admin/tenants", json=body, headers={"X-Admin-Token": "admin-secret"})
        assert r.status_code == 201, r.text
        d = r.json()
        d["headers"] = {"X-API-Key": d["api_key"]}
        return d

    def add_kb(self, t: dict, name: str = "kb.md", text: str = KB) -> None:
        r = self.client.post("/v1/documents", json={"name": name, "text": text}, headers=t["headers"])
        assert r.status_code == 201, r.text

    def signed(self, t: dict, payload: dict, ts: float | None = None) -> tuple[bytes, dict]:
        body = json.dumps(payload).encode()
        stamp = str(int(ts if ts is not None else time.time()))
        return body, {
            "X-Timestamp": stamp,
            "X-Signature": sign(t["webhook_secret"], stamp, body),
            "Content-Type": "application/json",
        }


@pytest.fixture
def env():
    e = Env()
    yield e
    e.client.close()
    e.engine.dispose()


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as s:
        yield s
    engine.dispose()


def digest(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()
