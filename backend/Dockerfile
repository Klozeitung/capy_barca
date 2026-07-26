# Pinned to the 3.12 line rather than a single patch release. Patch releases
# carry security fixes and no behaviour changes, so floating within the minor
# line keeps the image current without a repository edit for every CPython
# release. Data-bearing services (PostgreSQL, Collabora) are pinned exactly in
# docker-compose.yml, where a version jump can change behaviour.
FROM python:3.12-slim

# The container runs as the account that owns the repository on the host,
# never as root. setup.sh derives the values from id -u / id -g and passes
# them in through docker-compose.yml, so the bind-mounted uploads directory
# stays writable from the container and from restore.sh alike.
ARG APP_UID=1000
ARG APP_GID=1000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ./app ./app
COPY ./alembic ./alembic
COPY alembic.ini ./alembic.ini
# Carries the pytest configuration (asyncio_mode = "auto"). Without it the
# suite runs under different settings inside the container than in CI, where
# the full checkout is present.
COPY pyproject.toml ./pyproject.toml
COPY entrypoint.sh ./entrypoint.sh

# The work tree has to belong to the runtime user: the application creates
# static/ and static/uploads on import, and pytest writes .pytest_cache and
# __pycache__ into /app. The user is only created when the id is still free -
# a base image that already ships that id would otherwise fail the build.
RUN chmod +x ./entrypoint.sh \
 && mkdir -p /app/static/uploads \
 && if ! getent group "${APP_GID}" >/dev/null; then \
        groupadd -g "${APP_GID}" app; \
    fi \
 && if ! getent passwd "${APP_UID}" >/dev/null; then \
        useradd -u "${APP_UID}" -g "${APP_GID}" -M -d /app -s /usr/sbin/nologin app; \
    fi \
 && chown -R "${APP_UID}:${APP_GID}" /app

USER ${APP_UID}:${APP_GID}

ENTRYPOINT ["./entrypoint.sh"]
