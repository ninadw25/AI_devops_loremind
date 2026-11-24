# PostgreSQL 16 with PostGIS 3.5 + pgvector 0.7
# postgis/postgis already ships pg16 + PostGIS; we add pgvector on top.
FROM postgis/postgis:16-3.5

RUN apt-get update && apt-get install -y --no-install-recommends \
        postgresql-16-pgvector \
    && rm -rf /var/lib/apt/lists/*

COPY init.sql /docker-entrypoint-initdb.d/01_init.sql
