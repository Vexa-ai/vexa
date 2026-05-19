FROM python:3.10-slim

WORKDIR /app

# Run as non-root. Create the user before copying service code so COPY can
# normalize ownership even when the local checkout has restrictive file modes.
RUN useradd -r -s /bin/false appuser

# Install schema-sync library
COPY ./libs/schema-sync /app/libs/schema-sync
RUN pip install --no-cache-dir /app/libs/schema-sync

# Install admin models (User, APIToken)
COPY ./libs/admin-models /app/libs/admin-models
RUN pip install --no-cache-dir /app/libs/admin-models

# Install meeting-api models (CalendarEvent, database)
COPY ./services/meeting-api /app/services/meeting-api
RUN pip install --no-cache-dir /app/services/meeting-api

COPY --chown=appuser:appuser ./services/calendar-service/ /app/

RUN pip install --no-cache-dir -r requirements.txt

RUN chmod -R a+rX /app
USER appuser

EXPOSE 8050

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8050"]
