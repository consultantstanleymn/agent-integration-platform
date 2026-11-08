#!/bin/sh
# Run database migrations, then start the command. Fails fast if migrations fail.
set -e
alembic upgrade head
exec "$@"
