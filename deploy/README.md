# Deployment Layout

`/opt/openkataster-tiles` is the Git-tracked source of truth.

`/srv/openkataster-tiles` is runtime/deployment storage only:
- active PMTiles and SQLite files
- version manifests
- live static viewer files copied from `live-viewer/deutschland-v2`
- PMTiles and SQLite data
- tile, raster and aerial image caches

The current production stack is Docker Compose plus Caddy:

```bash
cd /opt/openkataster-tiles
docker compose up -d --build
```

The container serves static viewer assets from:

```text
/srv/openkataster-tiles/live-viewer/deutschland-v2
```

To deploy tracked viewer files after checkout:

```bash
rsync -a --delete live-viewer/deutschland-v2/ /srv/openkataster-tiles/live-viewer/deutschland-v2/
```

Legacy system config snapshots live in:
- `deploy/nginx/openkataster-tiles.conf`
- `deploy/systemd/openkataster-tiles.service`

Large runtime data must not be committed.
