FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY services/mcp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy MCP service code
COPY services/mcp/ .

# Expose the port
EXPOSE 18888

# Run the MCP service
CMD ["python", "main.py"]
