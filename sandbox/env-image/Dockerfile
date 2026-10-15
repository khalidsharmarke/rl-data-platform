# The base image every environment version runs on (build decision B4): Python with pytest,
# and an unprivileged user. Checks add the version's files at run time; nothing else is baked in.
FROM python:3.12-slim
RUN pip install --no-cache-dir pytest==8.3.4 && useradd --uid 1000 --create-home runner
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER runner
WORKDIR /work
