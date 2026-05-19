FROM python:3.12-slim

WORKDIR /app

COPY services/telegram-bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/telegram-bot/bot.py .

EXPOSE 8200

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

CMD ["python", "bot.py"]
