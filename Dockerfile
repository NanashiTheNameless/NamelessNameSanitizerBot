# This software is licensed under NNCL v1.3 see LICENSE.md for more info

FROM python:alpine

WORKDIR /app

COPY requirements.txt .

# Install runtime deps and temporary build toolchain for wheels; purge build tools after install
RUN apk add --no-cache tzdata ca-certificates \
    && update-ca-certificates \
    && apk add --no-cache --virtual .build-deps build-base git \
    && pip install --no-cache-dir pip==25.3 \
    && pip install --no-cache-dir -r requirements.txt \
    && apk del .build-deps

COPY bot ./bot

# Create application data directory (volume-mounted in docker-compose).
RUN mkdir -p /app/data

# Run the bot as a module in unbuffered mode for real-time logs.
CMD ["python", "-u", "-m", "bot.main"]
