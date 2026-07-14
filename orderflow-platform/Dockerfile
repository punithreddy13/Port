FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY frontend/ frontend/

ENV PYTHONPATH=/app/backend \
    PYTHONUNBUFFERED=1 \
    ORDERFLOW_HOST=0.0.0.0 \
    ORDERFLOW_PORT=8720 \
    ORDERFLOW_DATA_DIR=/data

VOLUME ["/data"]
EXPOSE 8720

CMD ["python", "-m", "orderflow"]
