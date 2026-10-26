# syntax=docker/dockerfile:1
# The ufc-data service (docs/SERVICE.md): nginx serving /v1/ on :8080 and the refresh scheduler, in one container,
# as nginx-unprivileged's uid 101. The dependencies are installed here, at build time, never on the box.
ARG NGINX=nginxinc/nginx-unprivileged:1.28-trixie@sha256:0b31859f91f223bd3f741a25946839012740b31dcb4b7f29a79f229ed4e0c5d6

FROM ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv

# Built on the runtime image, so the managed CPython and the wheels match its glibc.
FROM ${NGINX} AS build
USER root
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON=3.13 UV_PYTHON_PREFERENCE=only-managed UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PROJECT_ENVIRONMENT=/opt/venv UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /src
# Dependencies first, so a code-only change reuses this layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM ${NGINX}
COPY --from=build /opt/python /opt/python
COPY --from=build /opt/venv /opt/venv
COPY deploy/nginx/fightcard-engine.conf /etc/nginx/conf.d/default.conf
COPY model/ /app/model/
COPY scripts/should_run.py scripts/gh_output.py /app/scripts/
# The volume's mount point: an empty named volume takes this ownership on first mount.
USER root
RUN install -d -o 101 -g 101 /srv/fightcard
USER 101
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 \
    FIGHTCARD_ROOT=/srv/fightcard FIGHTCARD_MODEL_DIR=/app/model FIGHTCARD_GATE_SCRIPT=/app/scripts/should_run.py \
    XDG_CACHE_HOME=/srv/fightcard/state/cache
WORKDIR /app
EXPOSE 8080
# infra's sites.yml says `healthcheck: image`. The supervisor's check: nginx answers /healthz, the data it serves is
# under 26 h old where this env scrapes, and no run is stuck (docs/SERVICE.md). nginx's /healthz stays a plain 200.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["fightcard-service", "health"]
# The supervisor handles SIGTERM (the nginx base image stops with SIGQUIT) and passes nginx its own graceful stop.
STOPSIGNAL SIGTERM
ENTRYPOINT ["fightcard-service"]
CMD ["serve"]
