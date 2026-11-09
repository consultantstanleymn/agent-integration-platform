from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        kwargs: dict[str, object] = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url or url in ("sqlite://", "sqlite:///"):
            kwargs["poolclass"] = StaticPool  # one shared connection for in-memory tests
        return create_engine(url, **kwargs)
    return create_engine(url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    db = factory()
    try:
        yield db
    finally:
        db.close()


def install_audit_triggers(engine: Engine) -> None:
    """Make ``audit_log`` append-only on SQLite. Postgres gets its trigger from migration 0003."""
    if engine.dialect.name != "sqlite" or not inspect(engine).has_table("audit_log"):
        return
    with engine.begin() as conn:
        for event in ("UPDATE", "DELETE"):
            conn.exec_driver_sql(
                f"CREATE TRIGGER IF NOT EXISTS audit_log_no_{event.lower()} BEFORE {event} ON audit_log "
                "BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;"
            )
