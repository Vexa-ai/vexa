# Base Python image
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Install shared library first
COPY ./libs/shared-models /app/libs/shared-models
# Change to a standard pip install
RUN pip install --no-cache-dir /app/libs/shared-models

# Copy application code and requirements
COPY ./services/bot-manager/requirements.txt /app/
# Copy all python files and the app subdirectory
COPY ./services/bot-manager/*.py /app/
COPY ./services/bot-manager/app /app/app

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --upgrade pip && pip install httpx

# Expose port (internal only, but good practice)
EXPOSE 8080

# Command to run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"] 