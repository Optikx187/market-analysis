FROM python:3.12-slim

WORKDIR /app

COPY services/notification-gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/notification-gateway/main.py .
COPY services/notification-gateway/bot.py .
COPY services/notification-gateway/credentials.py .
COPY services/notification-gateway/event_contracts.py .
COPY schemas/events/v1 /app/schemas/events/v1

EXPOSE 8003

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"]
