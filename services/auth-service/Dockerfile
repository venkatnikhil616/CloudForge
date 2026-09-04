FROM python:3.11-slim as builder

WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential gcc && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.11-slim as runner

WORKDIR /app
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONPATH=/app

# Non-root user for security best practices
RUN groupadd -r cloudtask && useradd -r -g cloudtask cloudtask

COPY pkg/ /app/pkg/
COPY services/auth-service/ /app/services/auth-service/

RUN chown -R cloudtask:cloudtask /app
USER cloudtask

EXPOSE 8001

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health/live')" || exit 1

CMD ["python", "services/auth-service/main.py"]
