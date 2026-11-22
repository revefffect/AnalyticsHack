FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 WEATHER_HOME=/var/lib/weather
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && mkdir -p /var/lib/weather && chown 10001:10001 /var/lib/weather
COPY weather/ weather/
COPY tools/ tools/
COPY ops/ ops/
COPY data/raw/meteo_hack_dataset.zip data/raw/meteo_hack_dataset.zip
USER 10001:10001
CMD ["python","-m","uvicorn","weather.app:app","--host","0.0.0.0","--port","8000"]
