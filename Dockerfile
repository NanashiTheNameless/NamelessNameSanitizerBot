# This software is licensed under NNCL v1.3 see LICENSE.md for more info

FROM python:alpine

ARG NNSB_IMAGE_VERSION=dev
ARG NNSB_IMAGE_DIGEST=
ARG NNSB_GIT_SHA=unknown
LABEL org.opencontainers.image.revision=$NNSB_IMAGE_VERSION
ENV NNSB_IMAGE_VERSION=$NNSB_IMAGE_VERSION
ENV NNSB_IMAGE_DIGEST=$NNSB_IMAGE_DIGEST
ENV NNSB_GIT_SHA=$NNSB_GIT_SHA

WORKDIR /app

COPY requirements.txt .

# Install runtime deps and temporary build toolchain for wheels; purge build tools after install
# hadolint ignore=DL3013
RUN apk add --no-cache tzdata ca-certificates \
    && update-ca-certificates \
    && apk add --no-cache --virtual .build-deps build-base git \
    && pip install --root-user-action ignore --no-cache-dir pip>=25.3 \
    && pip install --root-user-action ignore --no-cache-dir -r requirements.txt \
    && apk del .build-deps

COPY bot ./bot

# Create application data directory (volume-mounted in docker-compose).
RUN mkdir -p /app/data \
    && echo "$NNSB_IMAGE_VERSION" > /app/.image_version \
    && if [ -n "$NNSB_IMAGE_DIGEST" ]; then echo "$NNSB_IMAGE_DIGEST" > /app/.image_digest; fi \
    && echo "$NNSB_GIT_SHA" > /app/.git_sha

# Run the bot as a module in unbuffered mode for real-time logs.
CMD ["python", "-u", "-m", "bot.main"]
