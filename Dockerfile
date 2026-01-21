FROM ghcr.io/astral-sh/uv:bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Configure the Python directory so it is consistent
ENV UV_PYTHON_INSTALL_DIR=/python

# Only use the managed Python version
ENV UV_PYTHON_PREFERENCE=only-managed

# Install Python before the project for caching
RUN uv python install 3.11

WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

FROM debian:bookworm-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ca-certificates \
    openssh-client \
    ffmpeg && \
    update-ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Copy the Python version
COPY --from=builder /python /python

# Copy the application from the builder
COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app

CMD ["python", "-m", "bot.main"]