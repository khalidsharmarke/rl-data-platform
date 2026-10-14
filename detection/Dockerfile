FROM python:3.12-slim
RUN pip install --no-cache-dir httpx==0.28.1 pytest==8.3.4
WORKDIR /detection
COPY . .
ENV PYTHONUNBUFFERED=1
CMD ["python", "service.py"]
