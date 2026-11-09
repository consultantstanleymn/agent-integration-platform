"""The Redis rate limiter, tested with fakeredis."""

import fakeredis

from aip.api.app import build_rate_limiter, create_app
from aip.config import Settings
from aip.db import make_engine
from aip.models import Base
from aip.providers.fake import FakeProvider
from aip.security import RateLimiter, RedisRateLimiter


def test_redis_limiter_allows_under_limit():
    limiter = RedisRateLimiter(fakeredis.FakeRedis(), limit=3)
    assert [limiter.check("t1") for _ in range(3)] == [0.0, 0.0, 0.0]


def test_redis_limiter_blocks_over_limit_with_wait():
    client = fakeredis.FakeRedis()
    limiter = RedisRateLimiter(client, limit=2, window=60)
    limiter.check("t1")
    limiter.check("t1")
    wait = limiter.check("t1")
    assert 0 < wait <= 60
    assert limiter.check("other-key") == 0.0  # keys are separate


def test_two_app_instances_share_the_limit():
    server = fakeredis.FakeServer()
    limiters = [
        RedisRateLimiter(fakeredis.FakeRedis(server=server), limit=2),
        RedisRateLimiter(fakeredis.FakeRedis(server=server), limit=2),
    ]
    assert limiters[0].check("t1") == 0.0
    assert limiters[1].check("t1") == 0.0
    assert limiters[0].check("t1") > 0  # the second instance already used the quota


def test_memory_limiter_still_default():
    assert isinstance(build_rate_limiter(Settings()), RateLimiter)
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    app = create_app(
        Settings(database_url="sqlite://"),  # type: ignore[arg-type]
        engine=engine,
        chat=FakeProvider(),
        embedder=FakeProvider(),
    )
    assert isinstance(app.state.rate_limiter, RateLimiter)
