# SPDX-License-Identifier: LicenseRef-OQL-1.2
# Minimal production image for the Discord Sanitizer Bot.
# Expects a requirements.txt at repository root and runs bot/main.py.

FROM python:3.12-slim

WORKDIR /app

# Install minimal OS dependencies (timezone data and CA certs) and clean cache.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Install Python dependencies.
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot

# Create application data directory (volume-mounted in docker-compose).
RUN mkdir -p /app/data

# Run the bot in unbuffered mode for real-time logs.
CMD ["python", "-u", "bot/main.py"]