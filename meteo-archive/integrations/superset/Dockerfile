FROM apache/superset:6.1.0
USER root
RUN uv pip install 'duckdb-engine==0.17.0' 'duckdb==1.5.6' 'rich>=13,<16'
COPY superset_config.py provision.py entrypoint.sh /app/meteo-superset/
USER superset
ENTRYPOINT ["sh", "/app/meteo-superset/entrypoint.sh"]
