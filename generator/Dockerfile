# The code task generator (D48): the platform's own client, with nothing but the standard library
# and the vendor CLI (tools/rlp.py, mounted). pytest is only for the families' tests.
FROM python:3.12-slim
RUN pip install --no-cache-dir pytest==8.3.4
WORKDIR /gen
COPY families.py generator.py test_families.py ./
CMD ["python", "generator.py"]
