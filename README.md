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
/srv/openkataster-tiles/basemaps/europe # versioned Europe basemap and feature mode
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
- `OPENKATASTER_EUROPE_BASEMAP_ROOT`
- `OPENKATASTER_EUROPE_BASEMAP_MODE`
- `OPENKATASTER_EUROPE_BASEMAP_STYLE_URL`
- `OPENKATASTER_TILE_ADMIN_KEYS`
- `OPENKATASTER_TILE_PRO_TOKENS`

## Europe basemap runtime

The self-hosted Europe map is independent from the cadastral state runtimes.
Its production directory has this layout:

```text
basemaps/europe/
├── versions/
│   └── europe-YYYYMMDD-z15/
│       ├── basemap.pmtiles
│       └── manifest.json
├── active -> versions/europe-YYYYMMDD-z15
└── mode
```

`manifest.json` uses schema version 1 and contains `version`, `pmtiles`,
`sha256`, `size_bytes`, `minzoom`, `maxzoom`, `bounds`, `attribution`,
`source`, and a pinned source/license inventory. The current daily archive
contains OpenStreetMap data and an unrendered Daylight/Overture `landcover`
layer derived from ESA WorldCover 2020. Both attributions are retained in the
manifest, public configuration, style and source panel even though OpenKataster
does not render that layer. The service accepts only an `active` symlink whose target and both
runtime files remain below the configured `versions` directory. Verify the
archive hash before activation, create a replacement symlink, and rename it
over `active`; never modify an active version in place.
Restart every tiles API worker after the switch and verify with `lsof +L1`
before deleting the previous archive. The runtime cache is bounded to two
readers, keyed by resolved path, inode, modification time, and size, and all
readers are closed during graceful shutdown.

The feature mode is `off`, `preview`, or `on`. A root-level `mode` file
overrides the environment and is also changed through write-and-rename. An
invalid mode, pointer, manifest, file size, or PMTiles header fails closed and
leaves the national maps as fallback. Runtime state is public at
`GET /api/v1/basemap/config`; versioned tiles are served from
`GET /api/v1/basemap/europe/{z}/{x}/{y}.mvt?v=VERSION`.

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
