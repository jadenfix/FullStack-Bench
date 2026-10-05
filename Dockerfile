# SimCloud images.
#   docker build --target simcloud -t fullstack-bench/simcloud:dev .   # the platform (control plane + load balancer)
#   docker build --target client   -t fullstack-bench/client:dev .     # sc + simcloud-mcp for an agent image
# Pin by digest in task environments.

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.8.15 /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock README.md ./
COPY simcloud ./simcloud
COPY fsbench ./fsbench
RUN uv build --wheel --out-dir /dist

FROM python:3.12-slim AS simcloud
# Service instances run as processes inside this container, so it carries the runtimes they need.
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm curl ca-certificates \
    postgresql postgresql-client && rm -rf /var/lib/apt/lists/*
COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl
RUN useradd --create-home --uid 10001 simcloud && mkdir -p /var/lib/simcloud && chown simcloud /var/lib/simcloud
USER simcloud
ENV SIMCLOUD_HOST=0.0.0.0 SIMCLOUD_PORT=7400 SIMCLOUD_ROUTER_PORT=7480 SIMCLOUD_DB=/var/lib/simcloud/state.db \
    SIMCLOUD_PG_LISTEN=0.0.0.0
EXPOSE 7400 7480 5433
HEALTHCHECK --interval=5s --timeout=2s --retries=12 CMD curl -sf http://127.0.0.1:7400/v1/health || exit 1
ENTRYPOINT ["simcloud"]

FROM python:3.12-slim AS client
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client && rm -rf /var/lib/apt/lists/*
COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl
COPY skills /skills
ENTRYPOINT ["sc"]

# Test image: the full suite with real Postgres (initdb refuses to run as root).
FROM python:3.12-slim AS test
RUN apt-get update && apt-get install -y --no-install-recommends postgresql curl && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8.15 /uv /usr/local/bin/uv
RUN useradd --create-home --uid 10002 tester && mkdir -p /src && chown tester /src
WORKDIR /src
COPY --chown=tester . .
USER tester
RUN uv sync --frozen
CMD ["uv", "run", "pytest", "-q", "-p", "no:cacheprovider"]
