FROM python:3.12-slim
RUN pip install --no-cache-dir httpx==0.28.1
WORKDIR /sim
COPY scenario.py .
CMD ["python", "scenario.py"]
