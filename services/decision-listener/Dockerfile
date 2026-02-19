FROM python:3.10-slim

WORKDIR /app

COPY ./services/decision-listener/requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY ./services/decision-listener/ /app/

EXPOSE 8765

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8765"]
