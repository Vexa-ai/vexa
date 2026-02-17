FROM python:3.10-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY services/ultravox-agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/ultravox-agent/*.py .

EXPOSE 9092 9093

CMD ["python", "main.py"]
