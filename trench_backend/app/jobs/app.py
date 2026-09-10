"""The procrastinate App -- the background job queue, backed by the same
Postgres instance as everything else.

Its own schema/tables are managed by procrastinate's own migration
tooling (`procrastinate schema --apply`), not Alembic: procrastinate
versions its schema independently of ours, and mixing the two migration
systems is generally discouraged.
"""

from procrastinate import App, PsycopgConnector

from app.config import get_settings


def _build_connector() -> PsycopgConnector:
    db = get_settings().TRENCH_CONFIG.DB
    return PsycopgConnector(
        kwargs={
            "host": db.ip_address,
            "port": db.port,
            "user": db.username,
            "password": db.password,
            "dbname": db.database,
        }
    )


app = App(connector=_build_connector())

# Importing task modules registers their @app.task-decorated functions on
# this App instance. Anything that loads `app.jobs.app` -- the API process,
# the `procrastinate` CLI's --app flag, tests -- needs this side effect, so
# it lives here rather than relying on each entrypoint to import it itself.
from app.jobs import document_tasks  # noqa: E402,F401
