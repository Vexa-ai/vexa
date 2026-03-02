FROM python:3.11-slim

WORKDIR /app

COPY services/recall-backup/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/recall-backup/main.py .

EXPOSE 8090

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8090"]
