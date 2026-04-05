FROM python:3.12-slim

WORKDIR /app

COPY services/telegram-bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/telegram-bot/bot.py .

EXPOSE 8200

CMD ["python", "bot.py"]
