"""Inbound HMAC verification and outbound delivery with retry and backoff."""

import hashlib
import hmac
import ipaddress
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

MAX_SKEW_SECONDS = 300


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """HMAC-SHA256 over '<timestamp>.<body>'. The timestamp is signed so a replay is detectable."""
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def verify(
    secret: str, timestamp: str | None, signature: str | None, body: bytes, now: float | None = None
) -> bool:
    if not timestamp or not signature:
        return False
    try:
        ts = float(timestamp)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - ts) > MAX_SKEW_SECONDS:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature)  # constant time


class UnsafeTargetError(ValueError):
    pass


def validate_target_url(url: str, allow_private: bool = False) -> None:
    """Basic SSRF guard for customer-supplied URLs. Literal IPs and 'localhost' only (see SECURITY.md)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UnsafeTargetError("webhook URL must be http(s)")
    if allow_private:
        return
    host = parsed.hostname
    if host == "localhost" or host.endswith(".internal"):
        raise UnsafeTargetError("private webhook target blocked")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return  # a DNS name; DNS rebinding is not handled here
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        raise UnsafeTargetError("private webhook target blocked")


@dataclass
class DeliveryResult:
    ok: bool
    status_code: int | None
    attempts: int


def send_webhook(
    url: str,
    secret: str,
    payload: dict[str, object],
    *,
    client: httpx.Client,
    max_attempts: int = 4,
    base_delay: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
    idempotency_key: str | None = None,
) -> DeliveryResult:
    """POST a signed JSON payload. Retry on network errors, 429 and 5xx with exponential backoff.

    4xx (except 429) is the receiver saying "your request is wrong", so retrying cannot help.
    The same Idempotency-Key is sent on every attempt so the receiver can drop duplicates.
    """
    body = json.dumps(payload, separators=(",", ":")).encode()
    status: int | None = None
    for attempt in range(1, max_attempts + 1):
        ts = str(int(time.time()))  # fresh timestamp per attempt, so retries stay inside the window
        headers = {
            "Content-Type": "application/json",
            "X-Timestamp": ts,
            "X-Signature": sign(secret, ts, body),
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            r = client.post(url, content=body, headers=headers)
            status = r.status_code
            if status < 400:
                return DeliveryResult(True, status, attempt)
            if status != 429 and status < 500:
                return DeliveryResult(False, status, attempt)
        except httpx.TransportError:
            status = None
        if attempt < max_attempts:
            sleep(base_delay * 2 ** (attempt - 1))  # 0.5s, 1s, 2s ...
    return DeliveryResult(False, status, max_attempts)
