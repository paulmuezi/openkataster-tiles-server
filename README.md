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

The current viewer is tracked under:

```text
live-viewer/deutschland-v2
```

On the server it is mounted read-only at:

```text
/srv/openkataster-tiles/live-viewer/deutschland-v2
```

The API serves it at:

```text
/viewer/deutschland
/embed
/embed/onoffice
```

Free/Pro behavior is handled by the viewer session API and configured tokens.

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
