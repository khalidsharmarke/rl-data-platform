# The sandbox runner: the platform's own code plus the Docker CLI, which it uses to start a
# fresh, locked-down container for every check (build decision B5). It talks to the host's
# Docker daemon, which is host-level power: fine on a laptop, nowhere else.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /bin/uv
ADD https://download.docker.com/linux/static/stable/x86_64/docker-27.3.1.tgz /tmp/docker.tgz
RUN tar -xzf /tmp/docker.tgz -C /tmp && mv /tmp/docker/docker /usr/local/bin/docker && rm -rf /tmp/docker /tmp/docker.tgz
WORKDIR /app
COPY pyproject.toml .
RUN uv pip install --system -r pyproject.toml --extra dev
COPY . .
ENV PYTHONUNBUFFERED=1
