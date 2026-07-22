#!/usr/bin/env python3
"""Download and validate one versioned DOP20 pilot tile.

This deliberately handles one tile at a time. It proves resolution, metadata,
georeferencing and COG creation without turning a public WMS into an
uncontrolled bulk downloader. Production state builds should prefer the
official bulk-download channel recorded in ``sources.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from PIL import Image


SOURCE_CONFIG_PATH = Path(__file__).with_name("sources.json")
TILE_ID_RE = re.compile(r"^E(?P<easting>[0-9]{3})N(?P<northing>[0-9]{4})$")
ACQUISITION_DATE_RE = re.compile(r"\bua\s*=\s*['\"](?P<date>[0-9]{2}\.[0-9]{2}\.[0-9]{4})['\"]")
USER_AGENT = "OpenKataster-DOP20-Pilot/1.0"
MAX_DOWNLOAD_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class TileBounds:
    min_easting: int
    min_northing: int
    max_easting: int
    max_northing: int

    def wms_bbox(self) -> str:
        return ",".join(
            str(value)
            for value in (
                self.min_easting,
                self.min_northing,
                self.max_easting,
                self.max_northing,
            )
        )


def load_source(source_key: str, path: Path = SOURCE_CONFIG_PATH) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError(f"Unsupported aerial source schema: {payload.get('schema_version')!r}")
    try:
        return payload["sources"][source_key]
    except KeyError as exc:
        raise ValueError(f"Unknown aerial source: {source_key}") from exc


def parse_tile_id(tile_id: str, tile_size_m: int = 1000) -> TileBounds:
    """Decode ``E624N5306`` where N identifies the tile's north edge."""

    match = TILE_ID_RE.fullmatch(tile_id.strip())
    if not match:
        raise ValueError("Tile ID must use the form E624N5306")
    min_easting = int(match.group("easting")) * 1000
    max_northing = int(match.group("northing")) * 1000
    return TileBounds(
        min_easting=min_easting,
        min_northing=max_northing - tile_size_m,
        max_easting=min_easting + tile_size_m,
        max_northing=max_northing,
    )


def request_dimensions(source: dict, pixel_size_m: float) -> tuple[int, int]:
    if pixel_size_m <= 0:
        raise ValueError("Pixel size must be greater than zero")
    tile_size_m = int(source["tile_size_m"])
    pixels = round(tile_size_m / pixel_size_m)
    if not pixels or abs(tile_size_m / pixels - pixel_size_m) > 1e-9:
        raise ValueError("Pixel size must divide the configured tile size exactly")
    max_pixels = int(source["max_request_pixels"])
    if pixels > max_pixels:
        raise ValueError(
            f"Requested {pixels} pixels exceed the source limit of {max_pixels}"
        )
    return pixels, pixels


def build_getmap_params(
    source: dict,
    bounds: TileBounds,
    pixel_size_m: float,
    image_format: str,
) -> dict[str, str]:
    if image_format not in source["formats"]:
        raise ValueError(f"Unsupported source image format: {image_format}")
    width, height = request_dimensions(source, pixel_size_m)
    return {
        "SERVICE": "WMS",
        "VERSION": str(source["wms_version"]),
        "REQUEST": "GetMap",
        "LAYERS": str(source["layer"]),
        "STYLES": "",
        "CRS": str(source["crs"]),
        "BBOX": bounds.wms_bbox(),
        "WIDTH": str(width),
        "HEIGHT": str(height),
        "FORMAT": image_format,
        "TRANSPARENT": "FALSE",
        "EXCEPTIONS": "XML",
    }


def build_url(base_url: str, params: dict[str, str]) -> str:
    return f"{base_url}?{urllib.parse.urlencode(params)}"


def _copy_limited(response: BinaryIO, destination: BinaryIO) -> int:
    total = 0
    while True:
        chunk = response.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_DOWNLOAD_BYTES:
            raise RuntimeError("Upstream image exceeded the 128 MiB pilot limit")
        destination.write(chunk)
    return total


def download_atomic(url: str, destination: Path, attempts: int = 3) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.part")
        temporary.unlink(missing_ok=True)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as out:
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if not content_type.startswith("image/"):
                    raise RuntimeError(f"Upstream returned {content_type or 'no content type'}")
                _copy_limited(response, out)
            os.replace(temporary, destination)
            return content_type
        except Exception as exc:  # retry network and incomplete upstream responses
            last_error = exc
            temporary.unlink(missing_ok=True)
            if attempt < attempts:
                time.sleep(attempt * 2)
    raise RuntimeError(f"DOP20 download failed after {attempts} attempts: {last_error}")


def validate_image(path: Path, expected_size: tuple[int, int]) -> dict[str, object]:
    with Image.open(path) as image:
        image.load()
        actual_size = image.size
        if actual_size != expected_size:
            raise RuntimeError(f"Unexpected image size {actual_size}; expected {expected_size}")
        return {
            "width": actual_size[0],
            "height": actual_size[1],
            "mode": image.mode,
            "format": image.format,
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def acquisition_date_params(source: dict, bounds: TileBounds) -> dict[str, str]:
    return {
        "SERVICE": "WMS",
        "VERSION": str(source["wms_version"]),
        "REQUEST": "GetFeatureInfo",
        "LAYERS": str(source["metadata_layer"]),
        "QUERY_LAYERS": str(source["metadata_layer"]),
        "STYLES": "",
        "CRS": str(source["crs"]),
        "BBOX": bounds.wms_bbox(),
        "WIDTH": "1000",
        "HEIGHT": "1000",
        "I": "500",
        "J": "500",
        "INFO_FORMAT": "text/plain",
    }


def parse_acquisition_date(payload: str) -> str | None:
    match = ACQUISITION_DATE_RE.search(payload)
    if not match:
        return None
    parsed = datetime.strptime(match.group("date"), "%d.%m.%Y")
    return parsed.date().isoformat()


def fetch_acquisition_date(source: dict, bounds: TileBounds) -> dict[str, str | None]:
    url = build_url(str(source["wms_url"]), acquisition_date_params(source, bounds))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            value = parse_acquisition_date(
                response.read(64 * 1024).decode("utf-8", "replace")
            )
        return {
            "sample": "tile_center",
            "status": "ok" if value else "not_found",
            "date": value,
            "error_type": None,
        }
    except Exception as exc:
        return {
            "sample": "tile_center",
            "status": "unavailable",
            "date": None,
            "error_type": type(exc).__name__,
        }


def cog_creation_options(source: dict, bounds: TileBounds) -> list[str]:
    return [
        "-of",
        "COG",
        "-a_srs",
        str(source["crs"]),
        "-a_ullr",
        str(bounds.min_easting),
        str(bounds.max_northing),
        str(bounds.max_easting),
        str(bounds.min_northing),
        "-co",
        "COMPRESS=JPEG",
        "-co",
        "QUALITY=90",
        "-co",
        "BLOCKSIZE=512",
        "-co",
        "BIGTIFF=IF_SAFER",
        "-co",
        "OVERVIEWS=AUTO",
        "-co",
        "RESAMPLING=LANCZOS",
    ]


def validate_cog(
    path: Path,
    source: dict,
    bounds: TileBounds,
    expected_size: tuple[int, int],
) -> dict[str, object]:
    executable = shutil.which("gdalinfo")
    if not executable:
        raise RuntimeError("gdalinfo is required to validate the COG")
    try:
        completed = subprocess.run(
            [executable, "-json", str(path)],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "GDAL_PAM_ENABLED": "NO"},
        )
        payload = json.loads(completed.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"COG validation failed: {exc}") from exc

    if tuple(payload.get("size", ())) != expected_size:
        raise RuntimeError(f"COG has unexpected dimensions: {payload.get('size')}")
    pixel_width = (bounds.max_easting - bounds.min_easting) / expected_size[0]
    pixel_height = (bounds.max_northing - bounds.min_northing) / expected_size[1]
    expected_transform = [
        float(bounds.min_easting),
        pixel_width,
        0.0,
        float(bounds.max_northing),
        0.0,
        -pixel_height,
    ]
    actual_transform = payload.get("geoTransform")
    if not isinstance(actual_transform, list) or any(
        abs(float(actual) - expected) > 1e-9
        for actual, expected in zip(actual_transform, expected_transform, strict=True)
    ):
        raise RuntimeError(f"COG has unexpected geotransform: {actual_transform}")
    image_structure = payload.get("metadata", {}).get("IMAGE_STRUCTURE", {})
    if image_structure.get("LAYOUT") != "COG":
        raise RuntimeError("GDAL did not report a COG layout")
    epsg_code = str(source["crs"]).split(":", 1)[-1]
    crs_wkt = str(payload.get("coordinateSystem", {}).get("wkt", ""))
    if not re.search(rf'ID\["EPSG",\s*{re.escape(epsg_code)}\]', crs_wkt):
        raise RuntimeError(f"COG does not declare {source['crs']}")
    first_band = (payload.get("bands") or [{}])[0]
    overview_sizes = [overview.get("size") for overview in first_band.get("overviews", [])]
    if not overview_sizes:
        raise RuntimeError("COG has no overview levels")
    return {
        "layout": image_structure.get("LAYOUT"),
        "compression": image_structure.get("COMPRESSION"),
        "geo_transform": actual_transform,
        "overview_sizes": overview_sizes,
    }


def create_cog(
    source_image: Path,
    destination: Path,
    source: dict,
    bounds: TileBounds,
    expected_size: tuple[int, int],
) -> dict[str, object]:
    executable = shutil.which("gdal_translate")
    if not executable:
        raise RuntimeError("gdal_translate is required to create the COG")
    try:
        version = subprocess.run(
            [executable, "--version"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Could not determine the GDAL version") from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp.tif", dir=destination.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    temporary.unlink(missing_ok=True)
    options = cog_creation_options(source, bounds)
    command = [executable, str(source_image), str(temporary), *options]
    environment = {**os.environ, "GDAL_PAM_ENABLED": "NO"}
    try:
        subprocess.run(command, check=True, env=environment, capture_output=True, text=True)
        validation = validate_cog(temporary, source, bounds, expected_size)
        os.replace(temporary, destination)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"COG creation failed: {exc.stderr.strip()}") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "profile_version": 1,
        "gdal_version": version,
        "options": options,
        "validation": validation,
    }


def file_record(path: Path) -> dict[str, object]:
    return {
        "name": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def run(args: argparse.Namespace) -> dict[str, object]:
    source = load_source(args.source)
    bounds = parse_tile_id(args.tile, int(source["tile_size_m"]))
    pixel_size_m = float(args.pixel_size)
    expected_size = request_dimensions(source, pixel_size_m)
    image_format = args.format
    params = build_getmap_params(source, bounds, pixel_size_m, image_format)
    url = build_url(str(source["wms_url"]), params)

    output_dir = args.output_dir.resolve()
    suffix = str(source["formats"][image_format])
    format_name = image_format.split("/", 1)[-1].replace("jpeg", "jpg")
    stem = f"{args.source}_{args.tile}_{pixel_size_m:g}m_{format_name}"

    if args.dry_run:
        return {"request_url": url, "bounds": bounds.__dict__, "output": str(output_dir)}
    if output_dir.exists():
        raise RuntimeError(
            f"Output bundle already exists: {output_dir}. Use a new version directory."
        )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
    )
    image_path = staging_dir / f"{stem}.source{suffix}"
    cog_path = staging_dir / f"{stem}.cog.tif"
    manifest_path = staging_dir / f"{stem}.manifest.json"
    try:
        content_type = download_atomic(url, image_path)
        image = validate_image(image_path, expected_size)
        cog_build = None
        if not args.skip_cog:
            cog_build = create_cog(
                image_path, cog_path, source, bounds, expected_size
            )

        manifest: dict[str, object] = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": args.source,
            "source_title": source["title"],
            "tile_id": args.tile,
            "bounds": {**bounds.__dict__, "crs": source["crs"]},
            "pixel_size_m": pixel_size_m,
            "request": {
                "url": source["wms_url"],
                "version": source["wms_version"],
                "layer": source["layer"],
                "format": image_format,
                "content_type": content_type,
                "width": expected_size[0],
                "height": expected_size[1],
            },
            # This is a point sample, not an exhaustive date for every pixel.
            "acquisition_date_at_center": fetch_acquisition_date(source, bounds),
            "license": source["license"],
            "published_attribution": {
                "text": source["license"]["modified_attribution"],
                "license_id": source["license"]["id"],
                "license_url": source["license"]["url"],
            },
            "bulk_download": source["bulk_download"],
            "image": {**image, **file_record(image_path)},
            "cog": file_record(cog_path) if cog_path.exists() else None,
            "cog_build": cog_build,
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(staging_dir, output_dir)
        published_manifest = output_dir / manifest_path.name
        return {"manifest": str(published_manifest), **manifest}
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="bayern")
    parser.add_argument("--tile", required=True, help="1-km tile such as E624N5306")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pixel-size", type=float, default=0.2)
    parser.add_argument("--format", choices=("image/jpeg", "image/tiff"), default="image/jpeg")
    parser.add_argument("--skip-cog", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    result = run(parse_args(argv))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
