FROM python:3.12-slim

ARG USER_NAME=fred-user
ARG USER_ID=1000
ARG GROUP_ID=1000

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV UV_PROJECT_ENVIRONMENT=/app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Install uv once at image build time.
RUN pip install --no-cache-dir uv

# Create non-root runtime user/group.
RUN groupadd -g ${GROUP_ID} ${USER_NAME} && \
    useradd -u ${USER_ID} -g ${GROUP_ID} -m ${USER_NAME} && \
    chown -R ${USER_ID}:${GROUP_ID} /app

# Copy dependency manifests first for Docker layer caching.
COPY --chown=${USER_ID}:${GROUP_ID} agents/pyproject.toml agents/uv.lock* /app/

USER ${USER_NAME}

# Install runtime dependencies only; project source is copied later.
RUN uv sync --frozen --no-dev --no-install-project

# Copy the agents pod source tree.
COPY --chown=${USER_ID}:${GROUP_ID} agents/ /app/

EXPOSE 8010

CMD ["uv", "run", "uvicorn", "fred_samples_agents.main:app", "--host", "0.0.0.0", "--port", "8010"]
