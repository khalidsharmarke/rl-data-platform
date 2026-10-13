FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /bin/uv
WORKDIR /app
COPY pyproject.toml .
RUN uv pip install --system -r pyproject.toml --extra dev
COPY . .
ENV PYTHONUNBUFFERED=1
