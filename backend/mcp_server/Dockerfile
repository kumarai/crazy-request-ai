FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt /tmp/backend-requirements.txt
COPY backend/mcp_server/requirements.txt /tmp/mcp-requirements.txt

RUN pip install --no-cache-dir -U pip \
    && pip install --no-cache-dir -r /tmp/mcp-requirements.txt

COPY backend /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

EXPOSE 8765

CMD ["python", "-m", "mcp_server.main"]
