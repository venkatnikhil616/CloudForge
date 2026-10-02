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

RUN groupadd -r cloudtask && useradd -r -g cloudtask cloudtask

COPY pkg/ /app/pkg/
COPY services/scheduler/ /app/services/scheduler/

RUN chown -R cloudtask:cloudtask /app
USER cloudtask

EXPOSE 9092

CMD ["python", "services/scheduler/main.py"]
