"""Locust test of the case path: small cases, large cases and /v2/search.

Run: see load/README.md. The API key comes from LOAD_API_KEY. Text choices use a seeded random generator.
"""

import os
import random

from locust import HttpUser, between, task

RNG = random.Random(42)
SMALL = [
    "Please refund ${amount} for order ORD-{n}. The jacket did not fit.",
    "My zipper is broken, warranty claim for order ORD-{n}.",
    "The tent arrived damaged. Order ORD-{n}. Please send a replacement.",
]
LARGE = "Please refund ${amount} for order ORD-{n}. The whole set was wrong."
QUERIES = ["return window", "warranty zipper", "shipping damage photo", "store credit expiry", "refund limit"]


class CaseUser(HttpUser):
    wait_time = between(0.5, 1.5)

    def on_start(self) -> None:
        self.headers = {"X-API-Key": os.environ["LOAD_API_KEY"]}

    @task(3)
    def post_small_case(self) -> None:
        body = RNG.choice(SMALL).format(amount=RNG.randint(10, 90), n=RNG.randint(1000, 9999))
        self.client.post(
            "/v1/cases",
            json={"subject": "Support", "body": body},
            headers=self.headers,
            name="POST /v1/cases small",
        )

    @task(1)
    def post_large_case(self) -> None:
        body = LARGE.format(amount=RNG.randint(150, 450), n=RNG.randint(1000, 9999))
        self.client.post(
            "/v1/cases",
            json={"subject": "Support", "body": body},
            headers=self.headers,
            name="POST /v1/cases large",
        )

    @task(2)
    def search_v2(self) -> None:
        self.client.post(
            "/v2/search",
            json={"query": RNG.choice(QUERIES), "k": 5, "mode": "hybrid"},
            headers=self.headers,
            name="POST /v2/search",
        )
