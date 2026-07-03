#!/opt/openkataster-tiles/venv/bin/python
from __future__ import annotations

import argparse
import concurrent.futures
import math
import os
import sys
import time
from pathlib import Path

ENV_PATH = Path('/etc/openkataster-tiles.env')
APP_ROOT = Path('/opt/openkataster-tiles')
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def load_env(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def lonlat_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    lat = max(-85.05112878, min(85.05112878, lat))
    n = 2 ** z
    x = int(math.floor((lon + 180.0) / 360.0 * n))
    lat_rad = math.radians(lat)
    y = int(math.floor((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n))
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def tiles_for_bounds(bounds: tuple[float, float, float, float], z: int) -> list[tuple[int, int, int]]:
    west, south, east, north = bounds
    x_min, y_north = lonlat_tile(west, north, z)
    x_max, y_south = lonlat_tile(east, south, z)
    if x_max < x_min:
        x_min, x_max = x_max, x_min
    if y_south < y_north:
        y_north, y_south = y_south, y_north
    return [(z, x, y) for x in range(x_min, x_max + 1) for y in range(y_north, y_south + 1)]


def state_bounds(state: str, kind: str | None) -> tuple[float, float, float, float]:
    from openkataster_tiles.main import mosaic_entries

    selected = [
        entry.dataset.bounds
        for entry in mosaic_entries()
        if entry.name == state and (kind is None or entry.kind == kind)
    ]
    if not selected:
        raise SystemExit(f'no mosaic entries found for state={state!r} kind={kind!r}')
    return (
        min(item[0] for item in selected),
        min(item[1] for item in selected),
        max(item[2] for item in selected),
        max(item[3] for item in selected),
    )


def worker(tile: tuple[int, int, int]) -> tuple[int, int, int, int, float, str]:
    z, x, y = tile
    start = time.perf_counter()
    try:
        from openkataster_tiles.main import mosaic_tile

        data = mosaic_tile(z, x, y)
        size = len(data) if data else 0
        return z, x, y, size, time.perf_counter() - start, 'ok' if data else 'empty'
    except Exception as exc:  # noqa: BLE001 - this is an ops script; report and continue.
        return z, x, y, 0, time.perf_counter() - start, f'error:{type(exc).__name__}:{exc}'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Prewarm OpenKataster mosaic tile disk cache.')
    parser.add_argument('--state', default='niedersachsen')
    parser.add_argument('--kind', choices=['overview', 'detail', 'all'], default='detail')
    parser.add_argument('--zoom', action='append', type=int, required=True)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--log-every', type=int, default=100)
    return parser.parse_args()


def main() -> int:
    load_env()
    args = parse_args()
    kind = None if args.kind == 'all' else args.kind
    bounds = state_bounds(args.state, kind)
    tiles: list[tuple[int, int, int]] = []
    for z in args.zoom:
        tiles.extend(tiles_for_bounds(bounds, z))
    if args.limit > 0:
        tiles = tiles[: args.limit]

    total = len(tiles)
    print(
        f'prewarm-start state={args.state} kind={args.kind} zooms={args.zoom} '
        f'bounds={bounds} tiles={total} workers={args.workers}',
        flush=True,
    )
    if not tiles:
        return 0

    started = time.perf_counter()
    ok = empty = errors = bytes_total = 0
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
        for idx, (_z, _x, _y, size, elapsed, status) in enumerate(pool.map(worker, tiles), start=1):
            bytes_total += size
            if status == 'ok':
                ok += 1
            elif status == 'empty':
                empty += 1
            else:
                errors += 1
                print(f'prewarm-error tile={_z}/{_x}/{_y} status={status}', flush=True)
            if idx == 1 or idx % args.log_every == 0 or idx == total:
                rate = idx / max(time.perf_counter() - started, 0.001)
                print(
                    f'prewarm-progress {idx}/{total} ok={ok} empty={empty} errors={errors} '
                    f'last={elapsed:.3f}s rate={rate:.2f}/s bytes={bytes_total}',
                    flush=True,
                )
    print(
        f'prewarm-finished total={total} ok={ok} empty={empty} errors={errors} '
        f'bytes={bytes_total} elapsed={time.perf_counter() - started:.1f}s',
        flush=True,
    )
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
