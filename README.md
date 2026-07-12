# OpenKataster Tiles Server

FastAPI/Caddy service for OpenKataster vector tiles, feature lookup, search, aerial image proxying, and the embeddable Germany viewer.

This repository contains only source code and static viewer assets. Runtime data is intentionally not tracked:

- `alkis.pmtiles`
- `features.sqlite`
- `search.sqlite`
- generated caches
- local `.env` files and credentials

## Server Layout

Production layout on the tiles server:

```text
/opt/openkataster-tiles                 # Git checkout and Docker Compose
/srv/openkataster-tiles/data            # active PMTiles and SQLite feature/search files
/srv/openkataster-tiles/active          # version manifests and active state
/srv/openkataster-tiles/cache           # PMTiles, mosaic, raster and runtime caches
/srv/openkataster-tiles/geocoder        # optional central geocoder databases
/srv/openkataster-tiles/plz             # optional postcode/OpenPLZ databases
/srv/openkataster-tiles/live-viewer     # static viewer assets served by the API
/srv/openkataster-tiles/logs            # runtime logs
```

## Services

The Docker stack runs:

- `tiles-api`: FastAPI app from `openkataster_tiles.main`
- `caddy`: TLS/reverse proxy for `tiles.openkataster.de`

Start or update:

```bash
docker compose up -d --build
```

Check:

```bash
curl -fsS http://127.0.0.1:8081/health
curl -fsS http://127.0.0.1:8081/api/v1
```

## Configuration

Copy `.env.example` to `.env` on the server and fill secrets there:

```bash
cp .env.example .env
```

Never commit `.env`.

Important variables:

- `OPENKATASTER_TILE_PUBLIC_BASE_URL`
- `OPENKATASTER_TILE_DATA_DIR`
- `OPENKATASTER_TILE_ACTIVE_VOLUME_ROOT`
- `OPENKATASTER_VIEWER_ROOT`
- `OPENKATASTER_TILE_ADMIN_KEYS`
- `OPENKATASTER_TILE_PRO_TOKENS`

## Viewer

There is one canonical viewer application:

```text
live-viewer/viewer-app
```

It is deployed read-only to:

```text
/srv/openkataster-tiles/live-viewer/viewer-app
```

The only public runtime route is:

```text
/embed/deutschland
```

`/planer` is a website shell that embeds this route. Free, Pro and future
partner integrations use the same application and differ only through signed,
short-lived session scopes. Partner-specific HTML copies are not created.

Compatibility routes may redirect to the canonical embed during migration,
but they must not serve an independent viewer build.

## Developer API and embeds

The public REST contract is generated from FastAPI:

```text
/docs
/openapi.json
```

The iframe integration guide is served separately at:

```text
/docs/embed
```

Long-lived project keys are accepted only as `Authorization: Bearer <key>` for
developer requests. A project backend exchanges the key at
`POST /api/v1/embed/sessions` for a short-lived token tied to one configured
origin. Never place a project key in browser JavaScript or an iframe URL.

Internal admin, upload and legacy routes are deliberately excluded from the
public OpenAPI document.

## Deployment boundary

The production responsibilities are intentionally split:

- the website service renders `/planer`, account and billing pages;
- this service owns map/search/feature APIs and `/embed/deutschland`;
- PMTiles and SQLite data stay outside Git under `/srv/openkataster-tiles`;
- the website reverse proxy exposes the canonical routes and contains no API
  secrets.

## Data Contract

Each active state version is expected to provide:

- `alkis.pmtiles`
- `features.sqlite`
- `search.sqlite`

Uploads and activation should treat those three files as one versioned unit.

## Git Hygiene

Before committing, run:

```bash
git status --short
rg -n "BEGIN .*PRIVATE|PRIVATE KEY|PASSWORD|SECRET|TOKEN|OPENKATASTER_TILE_ADMIN_KEYS|OPENKATASTER_TILE_PRO_TOKENS|OPENKATASTER_TILE_KEYS" .
```

The `rg` command will also match placeholder names in `.env.example`; real values must not appear.
