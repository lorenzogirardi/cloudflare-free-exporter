FROM python:3.13.1-alpine

ARG UID=10001
ARG GID=10001
ARG USER=cfexporter

# The exporter is stdlib-only on purpose: no wheels, no build stage, minimal CVE surface.
RUN apk add --no-cache curl && \
    addgroup -g $GID -S $USER && \
    adduser -u $UID -G $USER -S -H -s /sbin/nologin $USER

WORKDIR /srv

COPY app/ /srv/app/
COPY pyproject.toml /srv/

USER $USER

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fsS http://localhost:8080/healthz || exit 1

CMD ["python3", "-m", "app.exporter"]
