FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    FLASK_ENV=production \
    PORT=5000

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN pip install --no-cache-dir Flask[async] pymysql cryptography httpx hypercorn

EXPOSE 5000

CMD ["hypercorn", "main:app", "--bind", "0.0.0.0:5000"]