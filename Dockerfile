FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    wget gnupg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium --with-deps

COPY . .

RUN mkdir -p /app/data

EXPOSE 5000

ENV FLASK_ENV=production
ENV DATABASE_URL=sqlite:////app/data/seo_tracker.db

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--timeout", "300", "--workers", "1", "app:app"]
