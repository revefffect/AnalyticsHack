FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 METEO_DATA_DIR=/data METEO_STATIC_ROOT=/app/staticfiles
WORKDIR /app
COPY portal/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY manage.py /app/manage.py
COPY portal /app/portal
COPY catalog /app/catalog
COPY eda/core.py /app/eda/core.py
COPY scripts/install_dashboard_assets.py /app/install_dashboard_assets.py

RUN useradd --system --uid 10001 --no-create-home meteo \
    && mkdir -p /data \
    && touch /data/.meteo-volume \
    && chown -R meteo /data \
    && chmod 700 /data \
    && python /app/install_dashboard_assets.py \
    && METEO_DEBUG=1 python manage.py collectstatic --noinput

USER meteo
CMD ["sh", "-ec", "python manage.py migrate --noinput && python manage.py seed_catalog && exec gunicorn portal.wsgi:application --bind 0.0.0.0:8000 --workers 1 --threads 4 --access-logfile -"]
