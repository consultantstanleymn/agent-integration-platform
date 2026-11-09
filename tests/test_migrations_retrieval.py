"""Migration 0006: retrieval columns. SQLite always, PostgreSQL when AIP_TEST_PG_URL is set."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[1]


def _cfg(monkeypatch, url: str) -> Config:
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def _cols(url: str, table: str) -> set[str]:
    engine = create_engine(url)
    try:
        return {c["name"] for c in inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


def test_upgrade_downgrade_sqlite(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'r.db'}"
    cfg = _cfg(monkeypatch, url)
    command.upgrade(cfg, "head")
    assert {"access_label", "source_uri"} <= _cols(url, "documents")
    assert "access_label" in _cols(url, "chunks")
    command.downgrade(cfg, "0005")
    assert "access_label" not in _cols(url, "documents") and "access_label" not in _cols(url, "chunks")
    command.upgrade(cfg, "head")
    assert "access_label" in _cols(url, "chunks")


@pytest.mark.pg
def test_upgrade_downgrade_postgres(pg_url, monkeypatch):
    engine = create_engine(pg_url)
    with engine.begin() as conn:  # start from an empty schema
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    cfg = _cfg(monkeypatch, pg_url)
    command.upgrade(cfg, "head")
    chunk_cols = _cols(pg_url, "chunks")
    assert {"access_label", "embedding_vec", "tsv"} <= chunk_cols
    with engine.connect() as conn:
        ext = conn.execute(text("SELECT extname FROM pg_extension WHERE extname = 'vector'")).all()
        indexes = {
            r[0] for r in conn.execute(text("SELECT indexname FROM pg_indexes WHERE tablename = 'chunks'"))
        }
    assert ext and {"ix_chunks_tsv", "ix_chunks_embedding_vec"} <= indexes
    command.downgrade(cfg, "0005")
    after = _cols(pg_url, "chunks")
    assert not ({"access_label", "embedding_vec", "tsv"} & after)
    command.upgrade(cfg, "head")
    assert "embedding_vec" in _cols(pg_url, "chunks")
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()
