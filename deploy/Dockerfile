FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin app \
    && mkdir -p /app/media /app/staticfiles /app/deploy \
    && command -v runuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY manage.py .
COPY yandex_64e4bd290b97df47.html googleaa830e61bb557a81.html .
COPY config ./config
COPY shop ./shop
COPY templates ./templates
COPY static ./static
COPY deploy/entrypoint.sh /entrypoint.sh
COPY deploy/healthcheck.py deploy/check_env.py deploy/envfile.py deploy/media_archive.py deploy/pack_media.py deploy/extract_media.py /app/deploy/

RUN chmod 755 /entrypoint.sh \
    && chown -R app:app /app

ENTRYPOINT ["/entrypoint.sh"]
CMD ["web"]
