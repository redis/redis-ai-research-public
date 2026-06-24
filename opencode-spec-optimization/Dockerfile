FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    APP_HOME=/opt/opencode-spec-optimization \
    HOST_WORKSPACE=/workspace \
    MERGED_WORKSPACE=/tmp/opencode-workspace \
    VIRTUAL_ENV=/opt/opencode-spec-optimization/.venv \
    PATH=/root/.opencode/bin:/opt/opencode-spec-optimization/.venv/bin:$PATH

WORKDIR /opt/opencode-spec-optimization

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv
RUN curl -fsSL https://opencode.ai/install | bash -s -- --no-modify-path

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY main.py ./main.py
COPY mcps ./mcps
COPY diagnostics ./diagnostics
COPY opencode.jsonc ./opencode.jsonc
COPY start_container.sh ./start_container.sh
RUN chmod +x ./start_container.sh

WORKDIR /workspace

CMD ["/opt/opencode-spec-optimization/start_container.sh"]
