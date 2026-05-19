# Base Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Run as non-root. Create the user before copying service code so COPY can
# normalize ownership even when the local checkout has restrictive file modes.
RUN useradd -r -s /bin/false appuser

# Install meeting-api (schemas, security headers)
COPY ./services/meeting-api /app/services/meeting-api
RUN pip install --no-cache-dir /app/services/meeting-api

# Copy application code and requirements
COPY --chown=appuser:appuser ./services/api-gateway/requirements.txt /app/
COPY --chown=appuser:appuser ./services/api-gateway/main.py /app/

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

RUN chmod -R a+rX /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
