#!/bin/sh
# Apply pending migrations, then start whatever the image was told to run.
#
# Why here and not "run it by hand before deploying": the operator's laptop cannot reach
# the database. The corporate network drops outbound 5432 and 6543 (443 to the same host
# is fine), so `python scripts/init_db.py` times out from a developer machine every time.
# The host that runs this container is the one that can reach the DB, so it is the one
# that should migrate.
#
# Safe to do on boot for this deployment:
#   * run_migrations() takes a Postgres advisory lock for the whole run, so two containers
#     starting together queue instead of applying the same DDL twice.
#   * It applies only what is missing and records each name, so a restart is a no-op.
#   * A failure exits non-zero HERE, before uvicorn binds — the release fails to come up
#     rather than serving traffic against a schema it does not match.
#
# MIGRATE_ON_START=false skips it, for the case where a human wants to inspect the
# database before the schema moves.
set -e

if [ "${MIGRATE_ON_START:-true}" = "true" ]; then
    echo "entrypoint: applying pending migrations"
    python scripts/init_db.py
else
    echo "entrypoint: MIGRATE_ON_START=false, skipping migrations"
fi

exec "$@"
