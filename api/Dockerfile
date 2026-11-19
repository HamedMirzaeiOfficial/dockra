FROM python:3.12-slim
ARG APT_MIRROR=
ARG PIP_MIRROR=
WORKDIR /app
RUN if [ -n "$APT_MIRROR" ]; then sed -i "s#http://deb.debian.org#$APT_MIRROR#g" /etc/apt/sources.list.d/debian.sources; fi \
    && apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker:27-cli /usr/local/libexec/docker/cli-plugins/docker-buildx /usr/local/libexec/docker/cli-plugins/docker-buildx
COPY requirements.txt .
# Try the configured mirror first, then fall back to pypi.org (same order as PIP_FALLBACK_MIRRORS in engine/source.py).
RUN for index in "$PIP_MIRROR" https://pypi.org/simple; do \
      [ -n "$index" ] || continue; \
      pip install --no-cache-dir --retries 5 --timeout 60 --index-url "$index" -r requirements.txt && exit 0; \
    done; exit 1
COPY . .
EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate && python manage.py seed && python manage.py collectstatic --noinput && exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --worker-class gthread --workers 3 --threads 8 --timeout 7200 --graceful-timeout 60"]
