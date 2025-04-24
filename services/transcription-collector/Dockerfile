# Base Python image
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Install shared library first
COPY ./libs/shared-models /app/libs/shared-models
# Explicitly install the shared library from its location within the container
RUN pip install --no-cache-dir /app/libs/shared-models

# Copy application code and requirements
COPY ./services/transcription-collector/requirements.txt /app/
# Copy all python files 
COPY ./services/transcription-collector/*.py /app/
# Copy README and config if needed by the app at runtime
# COPY ./services/transcription-collector/README.md /app/
# COPY ./services/transcription-collector/filter_config.py /app/ # Already copied by *.py

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose port (internal only, but good practice)
EXPOSE 8000

# Command to run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"] 