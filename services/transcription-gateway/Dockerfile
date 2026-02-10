FROM python:3.11-slim

WORKDIR /app

COPY services/transcription-gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/transcription-gateway/gateway ./gateway
COPY services/transcription-gateway/main.py services/transcription-gateway/check_aws.py ./

EXPOSE 9090

CMD ["python", "main.py"]
