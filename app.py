from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pmtiles.reader import MmapSource, Reader
from pmtiles.tile import Compression

DATA_ROOT = Path(os.environ.get("OPENKATASTER_PMTILES_ROOT", "/srv/openkataster/pmtiles"))
ADMIN_API_BASE_URL = os.environ.get("OPENKATASTER_ADMIN_API_BASE_URL", "https://api.openkataster.de").rstrip("/")
S3_BUCKET = os.environ.get("OPENKATASTER_TILES_BUCKET", "")
S3_ENDPOINT_URL = os.environ.get("OPENKATASTER_TILES_S3_ENDPOINT", "")
S3_REGION = os.environ.get("OPENKATASTER_TILES_S3_REGION", "nbg1")
S3_ACCESS_KEY_ID = os.environ.get("OPENKATASTER_TILES_S3_ACCESS_KEY_ID", "")
S3_SECRET_ACCESS_KEY = os.environ.get("OPENKATASTER_TILES_S3_SECRET_ACCESS_KEY", "")
DATASET_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

app = FastAPI(title="OpenKataster Tiles", version="0.1.0")


def _safe_dataset(dataset: str) -> str:
    if not DATASET_RE.match(dataset):
        raise HTTPException(status_code=400, detail="invalid dataset")
    return dataset


def _dataset_slug(value: str) -> str:
    replacements = {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "ß": "ss",
        "Ä": "ae",
        "Ö": "oe",
        "Ü": "ue",
    }
    normalized = "".join(replacements.get(char, char.lower()) for char in value.strip())
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    if not normalized:
        raise HTTPException(status_code=400, detail="invalid bundesland")
    return _safe_dataset(normalized)


def _require_admin_token(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing admin token")

    request = urllib.request.Request(
        f"{ADMIN_API_BASE_URL}/v1/admin/auth/me",
        headers={"Authorization": authorization, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            if response.status != 200:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin token")
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin token") from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail="admin api validation failed") from exc

    return authorization[7:].strip()


def _dataset_dir(dataset: str) -> Path:
    dataset = _safe_dataset(dataset)
    return DATA_ROOT / dataset


def _pmtiles_path(dataset: str) -> Path:
    base = _dataset_dir(dataset)
    candidates = [base / "alkis.pmtiles", DATA_ROOT / f"{dataset}.pmtiles"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise HTTPException(status_code=404, detail="dataset not found")


def _style_path(dataset: str) -> Path | None:
    base = _dataset_dir(dataset)
    candidates = [base / "style.json", DATA_ROOT / f"{dataset}.style.json", DATA_ROOT / "default-style.json"]
    return next((candidate for candidate in candidates if candidate.is_file()), None)


@lru_cache(maxsize=32)
def _local_reader(path_str: str) -> Reader:
    # Keep file and mmap alive for the process lifetime via closure defaults.
    handle = open(path_str, "rb")
    source = MmapSource(handle)
    reader = Reader(source)
    reader._openkataster_file_handle = handle  # type: ignore[attr-defined]
    return reader


def _s3_enabled() -> bool:
    return bool(S3_BUCKET and S3_ENDPOINT_URL and S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY)


@lru_cache(maxsize=1)
def _s3_client():
    if not _s3_enabled():
        raise HTTPException(status_code=404, detail="dataset not found")
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL,
        region_name=S3_REGION,
        aws_access_key_id=S3_ACCESS_KEY_ID,
        aws_secret_access_key=S3_SECRET_ACCESS_KEY,
    )


def _s3_pmtiles_key(dataset: str) -> str:
    return f"pmtiles/{_safe_dataset(dataset)}/alkis.pmtiles"


def _s3_dataset_metadata_key(dataset: str) -> str:
    return f"pmtiles/{_safe_dataset(dataset)}/dataset.json"


def _s3_object_exists(key: str) -> bool:
    if not _s3_enabled():
        return False
    try:
        _s3_client().head_object(Bucket=S3_BUCKET, Key=key)
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise


def _s3_source(key: str):
    client = _s3_client()

    def get_bytes(offset: int, length: int) -> bytes:
        end = offset + length - 1
        response = client.get_object(Bucket=S3_BUCKET, Key=key, Range=f"bytes={offset}-{end}")
        return response["Body"].read()

    return get_bytes


@lru_cache(maxsize=32)
def _s3_reader(dataset: str) -> Reader:
    return Reader(_s3_source(_s3_pmtiles_key(dataset)))


def _get_reader(dataset: str) -> Reader:
    dataset = _safe_dataset(dataset)
    try:
        return _local_reader(str(_pmtiles_path(dataset)))
    except HTTPException:
        if _s3_object_exists(_s3_pmtiles_key(dataset)):
            return _s3_reader(dataset)
        raise


def _local_dataset_names() -> set[str]:
    if not DATA_ROOT.exists():
        return set()
    names = {p.stem for p in DATA_ROOT.glob("*.pmtiles")}
    names.update(p.name for p in DATA_ROOT.iterdir() if p.is_dir() and (p / "alkis.pmtiles").is_file())
    return names


def _s3_dataset_names() -> set[str]:
    if not _s3_enabled():
        return set()
    names: set[str] = set()
    paginator = _s3_client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix="pmtiles/"):
        for item in page.get("Contents", []):
            key = item.get("Key", "")
            if not key.endswith("/alkis.pmtiles"):
                continue
            parts = key.split("/")
            if len(parts) == 3 and parts[1]:
                names.add(parts[1])
    return names


def _dataset_metadata(dataset: str) -> dict[str, Any]:
    metadata_path = _dataset_dir(dataset) / "dataset.json"
    if metadata_path.is_file():
        with metadata_path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    key = _s3_dataset_metadata_key(dataset)
    if _s3_object_exists(key):
        response = _s3_client().get_object(Bucket=S3_BUCKET, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))
    return {}


def _jsonable(value: Any) -> Any:
    if hasattr(value, "name") and hasattr(value, "value"):
        return value.name.lower()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _public_base_url(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}".rstrip("/")


def _tile_headers(reader: Reader) -> dict[str, str]:
    headers = {
        "Content-Type": "application/vnd.mapbox-vector-tile",
        "Cache-Control": "public, max-age=31536000, immutable",
        "Access-Control-Allow-Origin": "*",
    }
    compression = reader.header().get("tile_compression")
    if compression == Compression.GZIP:
        headers["Content-Encoding"] = "gzip"
    elif compression == Compression.BROTLI:
        headers["Content-Encoding"] = "br"
    return headers


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "data_root": str(DATA_ROOT)}


@app.get("/datasets")
def datasets(request: Request) -> dict[str, Any]:
    result: list[dict[str, Any]] = []
    names = _local_dataset_names() | _s3_dataset_names()
    for name in sorted(names):
        try:
            reader = _get_reader(name)
            header = reader.header()
            result.append(
                {
                    "id": name,
                    "tile_url": f"{_public_base_url(request)}/tiles/{name}/{{z}}/{{x}}/{{y}}.pbf",
                    "style_url": f"{_public_base_url(request)}/styles/{name}/style.json",
                    "minzoom": header.get("min_zoom"),
                    "maxzoom": header.get("max_zoom"),
                    "metadata": _dataset_metadata(name),
                }
            )
        except HTTPException:
            continue
    return {"datasets": result}


@app.get("/tiles/{dataset}/{z}/{x}/{y}.pbf")
def tile(dataset: str, z: int, x: int, y: int) -> Response:
    if z < 0 or x < 0 or y < 0:
        raise HTTPException(status_code=400, detail="invalid tile coordinate")
    reader = _get_reader(dataset)
    payload = reader.get(z, x, y)
    if payload is None:
        return Response(status_code=204, headers={"Access-Control-Allow-Origin": "*"})
    return Response(content=payload, headers=_tile_headers(reader))


@app.get("/metadata/{dataset}.json")
def metadata(dataset: str) -> JSONResponse:
    reader = _get_reader(dataset)
    return JSONResponse(
        content={"header": _jsonable(reader.header()), "metadata": _jsonable(reader.metadata())},
        headers={"Access-Control-Allow-Origin": "*"},
    )


@app.get("/styles/{dataset}/assets/{asset}")
def style_asset(dataset: str, asset: str) -> FileResponse:
    if "/" in asset or ".." in asset:
        raise HTTPException(status_code=400, detail="invalid asset")
    path = _style_path(dataset)
    if path is None:
        raise HTTPException(status_code=404, detail="style not found")
    asset_path = path.parent / asset
    if not asset_path.is_file():
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(asset_path, headers={"Access-Control-Allow-Origin": "*"})


@app.get("/styles/{dataset}/style.json")
def style(dataset: str, request: Request) -> JSONResponse:
    path = _style_path(dataset)
    if path is None:
        raise HTTPException(status_code=404, detail="style not found")
    with path.open("r", encoding="utf-8") as f:
        style_json = json.load(f)

    base = _public_base_url(request)
    tile_url = f"{base}/tiles/{_safe_dataset(dataset)}/{{z}}/{{x}}/{{y}}.pbf"
    for source_name, source in style_json.get("sources", {}).items():
        if not isinstance(source, dict):
            continue
        if source.get("type") == "vector":
            source_url = str(source.get("url", ""))
            source_tiles = source.get("tiles") or []
            is_alkis_source = source_name == "alkis"
            is_pmtiles_source = source_url.startswith("pmtiles://") or any(str(tile).startswith("pmtiles://") for tile in source_tiles)
            if is_alkis_source or is_pmtiles_source:
                source.pop("url", None)
                source["tiles"] = [tile_url]
        elif source.get("type") == "geojson" and isinstance(source.get("data"), str):
            data_ref = source["data"]
            if data_ref.startswith("./") and path is not None:
                asset_path = path.parent / data_ref[2:]
                if asset_path.is_file():
                    source["data"] = f"{base}/styles/{_safe_dataset(dataset)}/assets/{asset_path.name}"
                else:
                    source["data"] = {"type": "FeatureCollection", "features": []}

    style_json.setdefault("metadata", {})["openkataster:dataset"] = dataset
    return JSONResponse(content=style_json, headers={"Access-Control-Allow-Origin": "*"})


@app.post("/admin/upload-pmtiles/{bundesland}")
async def upload_pmtiles(
    bundesland: str,
    version_name: str = Query(default=""),
    pmtiles: UploadFile = File(...),
    style: UploadFile | None = File(default=None),
    admin_token: str = Depends(_require_admin_token),
) -> dict[str, Any]:
    del bundesland, version_name, pmtiles, style, admin_token
    raise HTTPException(status_code=410, detail="Upload PMTiles directly to the tiles bucket.")


@app.post("/admin/refresh/{dataset}")
async def refresh_dataset(dataset: str, admin_token: str = Depends(_require_admin_token)) -> dict[str, Any]:
    del admin_token
    _safe_dataset(dataset)
    _local_reader.cache_clear()
    _s3_reader.cache_clear()
    _s3_client.cache_clear()
    return {"status": "success", "dataset": dataset}


@app.get("/viewer/{dataset}")
def viewer(dataset: str, request: Request) -> HTMLResponse:
    reader = _get_reader(dataset)
    header = reader.header()
    lon = header.get("center_lon_e7", 0) / 10_000_000
    lat = header.get("center_lat_e7", 0) / 10_000_000
    zoom = min(max(int(header.get("center_zoom") or header.get("max_zoom") or 16), 12), 18)
    style_url = f"{_public_base_url(request)}/styles/{_safe_dataset(dataset)}/style.json"
    html = f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>OpenKataster Tiles - {dataset}</title>
  <link rel="stylesheet" href="https://unpkg.com/maplibre-gl@5.10.0/dist/maplibre-gl.css" />
  <style>
    html, body, #map {{ height: 100%; margin: 0; }}
    .maplibregl-ctrl-attrib {{ font: 11px/1.4 system-ui, sans-serif; }}
  </style>
</head>
<body>
  <div id="map"></div>
  <script src="https://unpkg.com/maplibre-gl@5.10.0/dist/maplibre-gl.js"></script>
  <script>
    const map = new maplibregl.Map({{
      container: "map",
      style: "{style_url}",
      center: [{lon:.7f}, {lat:.7f}],
      zoom: {zoom},
      hash: true
    }});
    map.addControl(new maplibregl.NavigationControl({{ visualizePitch: true }}), "top-right");
  </script>
</body>
</html>"""
    return HTMLResponse(html)


@app.get("/pmtiles/{dataset}.pmtiles")
def pmtiles_file(dataset: str) -> FileResponse:
    path = _pmtiles_path(dataset)
    return FileResponse(
        path,
        media_type="application/vnd.pmtiles",
        filename=f"{_safe_dataset(dataset)}.pmtiles",
        headers={"Access-Control-Allow-Origin": "*"},
    )
