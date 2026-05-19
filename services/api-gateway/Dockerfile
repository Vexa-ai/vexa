# Base Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install meeting-api (schemas, security headers)
COPY ./services/meeting-api /app/services/meeting-api
RUN pip install --no-cache-dir /app/services/meeting-api

# Copy application code and requirements
COPY ./services/api-gateway/requirements.txt /app/
COPY ./services/api-gateway/main.py /app/

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Run as non-root
RUN useradd -r -s /bin/false appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]