FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY meteo meteo
COPY tools tools
COPY data/raw/meteo_hack_dataset.zip data/raw/
ENV PYTHONUNBUFFERED=1 METEO_STORAGE=/data
RUN useradd --system --uid 10001 meteo && install -d -o meteo -g meteo /data
USER meteo
EXPOSE 8000
CMD ["uvicorn", "meteo.api:app", "--host", "0.0.0.0", "--port", "8000"]
