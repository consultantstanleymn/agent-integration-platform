import os

from alembic import context
from sqlalchemy import create_engine

from aip.models import Base

url = os.environ.get("DATABASE_URL", "sqlite:///./aip.db")
target_metadata = Base.metadata


def run_migrations_online() -> None:
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
