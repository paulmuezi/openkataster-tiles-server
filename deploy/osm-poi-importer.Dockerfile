FROM debian:bookworm-slim

LABEL org.opencontainers.image.title="OpenKataster OSM POI importer" \
      org.opencontainers.image.description="Builds the read-only OpenKataster POI SQLite search index from a Geofabrik PBF" \
      org.opencontainers.image.licenses="MIT"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        gdal-bin \
        python3 \
        python3-gdal \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -c "import sqlite3; from osgeo import gdal; c=sqlite3.connect(':memory:'); c.execute('CREATE VIRTUAL TABLE f USING fts5(v)'); c.execute('CREATE VIRTUAL TABLE r USING rtree(id,minx,maxx,miny,maxy)'); print(gdal.VersionInfo('--version'), sqlite3.sqlite_version)"

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin importer

WORKDIR /app
COPY scripts/build_osm_poi_index.py /app/build_osm_poi_index.py
COPY scripts/osmconf-poi.ini /app/osmconf-poi.ini
RUN chmod 0555 /app/build_osm_poi_index.py \
    && mkdir -p /work \
    && chown importer:importer /work

USER importer
WORKDIR /work

ENTRYPOINT ["python3", "/app/build_osm_poi_index.py"]
