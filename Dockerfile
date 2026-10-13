# SimCloud images.
#   docker build --target simcloud -t fullstack-bench/simcloud:dev .   # the platform (control plane + load balancer)
#   docker build --target client   -t fullstack-bench/client:dev .     # sc + simcloud-mcp for an agent image
#   docker build --target k8s      -t fullstack-bench/k8s:dev .        # a managed-Kubernetes node (k3s)
# Pin by digest in task environments.

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.8.15 /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock README.md ./
COPY simcloud ./simcloud
COPY simsaas ./simsaas
RUN uv build --wheel --out-dir /dist

# Cluster tools (kubectl, helm) and the registry's base images, fetched at build time and checked.
FROM debian:bookworm-slim AS tools
ARG TARGETARCH
ARG KUBECTL_VERSION=v1.34.1
ARG HELM_VERSION=v3.19.0
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates skopeo python3 \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSLo /usr/local/bin/kubectl https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl \
    && echo "$(curl -fsSL https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl.sha256)  /usr/local/bin/kubectl" \
       | sha256sum -c - && chmod 755 /usr/local/bin/kubectl
RUN curl -fsSLo /tmp/helm.tgz https://get.helm.sh/helm-${HELM_VERSION}-linux-${TARGETARCH}.tar.gz \
    && echo "$(curl -fsSL https://get.helm.sh/helm-${HELM_VERSION}-linux-${TARGETARCH}.tar.gz.sha256sum | cut -d' ' -f1)  /tmp/helm.tgz" \
       | sha256sum -c - && tar -xzf /tmp/helm.tgz -C /tmp && mv /tmp/linux-${TARGETARCH}/helm /usr/local/bin/helm
# Language runtimes for services and agents: Go and Node, checked against their published sums.
ARG GO_VERSION=1.23.12
ARG NODE_VERSION=22.23.3
RUN curl -fsSLo /tmp/go.tgz https://go.dev/dl/go${GO_VERSION}.linux-${TARGETARCH}.tar.gz \
    && curl -fsSL "https://go.dev/dl/?mode=json&include=all" | python3 -c "import json,sys; v='go'+sys.argv[1]; a=sys.argv[2]; print(next(f['sha256'] for r in json.load(sys.stdin) if r['version']==v for f in r['files'] if f['filename']==f'{v}.linux-{a}.tar.gz') + '  /tmp/go.tgz')" ${GO_VERSION} ${TARGETARCH} \
       | sha256sum -c - && tar -xzf /tmp/go.tgz -C /usr/local && rm /tmp/go.tgz
RUN NODE_ARCH=$([ "${TARGETARCH}" = amd64 ] && echo x64 || echo ${TARGETARCH}) \
    && curl -fsSLo /tmp/node.tar.gz https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.gz \
    && curl -fsSL https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt | grep " node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.gz$" \
       | sed "s| node-v.*| /tmp/node.tar.gz|" | sha256sum -c - \
    && mkdir -p /usr/local/node && tar -xzf /tmp/node.tar.gz -C /usr/local/node --strip-components=1 && rm /tmp/node.tar.gz
ARG JDK_RELEASE=21.0.12.1_1
RUN JDK_ARCH=$([ "${TARGETARCH}" = amd64 ] && echo x64 || echo aarch64) \
    && JDK_TAG=$(echo "jdk-${JDK_RELEASE}" | sed 's/_/%2B/') \
    && URL="https://github.com/adoptium/temurin21-binaries/releases/download/${JDK_TAG}/OpenJDK21U-jdk_${JDK_ARCH}_linux_hotspot_${JDK_RELEASE}.tar.gz" \
    && curl -fsSLo /tmp/jdk.tgz "$URL" \
    && echo "$(curl -fsSL "$URL.sha256.txt" | cut -d' ' -f1)  /tmp/jdk.tgz" | sha256sum -c - \
    && mkdir -p /usr/local/jdk && tar -xzf /tmp/jdk.tgz -C /usr/local/jdk --strip-components=1 && rm /tmp/jdk.tgz \
    && rm -rf /usr/local/jdk/lib/src.zip /usr/local/jdk/jmods
RUN mkdir -p /base-images/python && skopeo copy --override-arch ${TARGETARCH} docker://docker.io/library/python:3.13-slim \
    oci:/base-images/python/3.13-slim

# python-web:3.13 = python:3.13-slim + the web stack most services use (one extra layer).
FROM python:3.13-slim AS pyweb
RUN pip install --no-cache-dir --root /python-web-layer fastapi==0.142.2 uvicorn==0.54.0 httpx==0.28.1 \
    "psycopg[binary]==3.3.6" pyyaml==6.0.3

FROM python:3.12-slim AS simcloud
# Service instances run as processes inside this container, so it carries the runtimes they need.
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates git \
    postgresql postgresql-client && rm -rf /var/lib/apt/lists/*
COPY --from=tools /usr/local/go /usr/local/go
COPY --from=tools /usr/local/node /usr/local/node
COPY --from=tools /usr/local/jdk /usr/local/jdk
ENV PATH=/usr/local/node/bin:/usr/local/go/bin:/usr/local/jdk/bin:$PATH GOTOOLCHAIN=local JAVA_HOME=/usr/local/jdk
COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl
COPY --from=tools /usr/local/bin/kubectl /usr/local/bin/kubectl
COPY --from=tools /base-images /opt/simcloud/base-images
COPY --from=pyweb /python-web-layer /opt/simcloud/base-images/python-web-layer
RUN echo '{"name": "python-web", "tag": "3.13", "base": "python:3.13-slim", "layer_dir": "python-web-layer"}' \
    > /opt/simcloud/base-images/python-web.overlay.json
# Three identities: root is the operator (the control plane, the guard, the collectors and the
# evidence), `simcloud` runs Postgres, which refuses root, and `workload` runs every submitted
# service, build and job through setpriv (simcloud/privsep.py). Operator material is root-only.
RUN useradd --create-home --uid 10001 simcloud && useradd --no-create-home --uid 10100 --shell /usr/sbin/nologin workload \
    && mkdir -p /var/lib/simcloud /shared && chmod 700 /var/lib/simcloud && chmod 755 /shared
ENV SIMCLOUD_HOST=0.0.0.0 SIMCLOUD_PORT=7400 SIMCLOUD_ROUTER_PORT=7480 SIMCLOUD_DB=/var/lib/simcloud/state.db \
    SIMCLOUD_PG_LISTEN=0.0.0.0
EXPOSE 7400 7480 7500 5433
HEALTHCHECK --interval=5s --timeout=2s --retries=12 CMD curl -sf http://127.0.0.1:7400/v1/health || exit 1
ENTRYPOINT ["simcloud"]

FROM python:3.12-slim AS client
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client && rm -rf /var/lib/apt/lists/*
COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl
COPY --from=tools /usr/local/bin/kubectl /usr/local/bin/helm /usr/local/bin/
COPY --from=tools /usr/local/go /usr/local/go
COPY --from=tools /usr/local/node /usr/local/node
COPY --from=tools /usr/local/jdk /usr/local/jdk
ENV PATH=/usr/local/node/bin:/usr/local/go/bin:/usr/local/jdk/bin:$PATH GOTOOLCHAIN=local JAVA_HOME=/usr/local/jdk
COPY skills /skills
# Context compaction for long runs (mini-swe-agent agent_class context_compaction.CompactingAgent).
COPY agent/context_compaction.py /opt/agent-ext/context_compaction.py
ENV PYTHONPATH=/opt/agent-ext
ENTRYPOINT ["sc"]

# k3s's own images (pause, coredns, local-path-provisioner, metrics-server, klipper-lb), pinned by
# the release's published digest, so a cluster comes up with no path to Docker Hub. The SimCloud
# registry mirrors only the task images; without this preload every pod sandbox fails to create.
# amd64 only: an arm64 build needs that tarball's digest from the same release.
FROM debian:bookworm-slim AS k3s-airgap
ARG K3S_RELEASE=v1.34.1%2Bk3s1
ARG K3S_AIRGAP_SHA256_AMD64=27355d2838c727180f0a8ded8aec0ac14e2186bd50487a3c3f29500884121aa7
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/* \
    && curl -fsSLo /k3s-airgap-images-amd64.tar.zst \
       "https://github.com/k3s-io/k3s/releases/download/${K3S_RELEASE}/k3s-airgap-images-amd64.tar.zst" \
    && echo "${K3S_AIRGAP_SHA256_AMD64}  /k3s-airgap-images-amd64.tar.zst" | sha256sum -c -

# A managed-Kubernetes node: k3s with SimCloud identity (token webhook), audit forwarding and the
# SimCloud registry as its image source. Run privileged; see docker/k8s/entrypoint.sh.
FROM rancher/k3s:v1.34.1-k3s1 AS k8s
COPY --from=k3s-airgap /k3s-airgap-images-amd64.tar.zst /var/lib/rancher/k3s/agent/images/
COPY docker/k8s/ /etc/simcloud-k8s/
COPY docker/k8s/registries.yaml /etc/rancher/k3s/registries.yaml
ENV KUBECONFIG=/k8s-state/kubeconfig.yaml
HEALTHCHECK --interval=5s --timeout=4s --retries=60 CMD ["/bin/kubectl", "get", "--raw", "/readyz"]
ENTRYPOINT ["/bin/sh", "/etc/simcloud-k8s/entrypoint.sh"]

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
