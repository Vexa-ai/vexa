# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# --- Install Shared Library ---
# Copy the shared library source code
COPY ./libs/shared-models/ /app/libs/shared-models/
# Install the shared library so it's available to the app
RUN pip install --no-cache-dir /app/libs/shared-models/

# --- Install App Dependencies ---
# Copy the requirements file
COPY ./services/bot-manager/requirements.txt .
# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# --- Copy App Source ---
# Copy the entire application source code
COPY ./services/bot-manager/app/ ./app

# Expose the port the app runs on
EXPOSE 8080

# Run the application as a module
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"] 