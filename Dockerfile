FROM python:3.12-slim-bookworm

# LibreOffice recalculates each workbook so it opens with values and the check rows can be verified.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libreoffice-calc-nogui fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY web web
COPY examples examples

ENV DATA_DIR=/data HOME=/tmp PYTHONUNBUFFERED=1
EXPOSE 8000
# One worker: model sessions run in background threads inside this process.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
