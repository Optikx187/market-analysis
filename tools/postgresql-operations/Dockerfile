FROM python:3.12-alpine

RUN apk add --no-cache postgresql16-client

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY operations.py .

ENTRYPOINT ["python", "/app/operations.py"]
