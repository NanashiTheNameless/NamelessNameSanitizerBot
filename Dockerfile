# SPDX-License-Identifier: LicenseRef-OQL-1.2
# Minimal production image for the Discord Sanitizer Bot.
# Expects a requirements.txt at repository root and runs bot/main.py.

# Use explicit Debian release to safely pin APT package versions
FROM python:3.12-slim-bookworm

WORKDIR /app

# Install minimal OS dependencies (timezone data and CA certs) and clean cache.
## Pin APT packages to specific versions for reproducible builds (as of 2025-10-28)
## - tzdata: https://packages.debian.org/bookworm/tzdata (2025b-0+deb12u2)
## - ca-certificates: https://packages.debian.org/bookworm/ca-certificates (20230311+deb12u1)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata=2025b-0+deb12u2 \
    ca-certificates=20230311+deb12u1 && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Install Python dependencies.
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot

# Create application data directory (volume-mounted in docker-compose).
RUN mkdir -p /app/data

# Run the bot in unbuffered mode for real-time logs.
CMD ["python", "-u", "bot/main.py"]