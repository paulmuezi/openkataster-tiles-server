# DOP20 aerial pilot

This is a deliberately small, reproducible pilot for one official 1 km DOP20
tile. It validates the source response, records acquisition and licence data,
and creates an atomically published Cloud Optimized GeoTIFF (COG).

The filename convention uses the tile's western and northern kilometre grid
lines. For example, `E624N5306` covers:

```text
EPSG:25832
Easting:  624000 .. 625000
Northing: 5305000 .. 5306000
```

Run the Bavaria pilot with GDAL 3.1 or newer and Pillow installed. The output
path must not exist; every run publishes one new, immutable bundle:

```bash
python -m producer.aerial.dop20_pilot \
  --tile E624N5306 \
  --output-dir /path/out/bayern-pilot
```

The default request is 5000 × 5000 pixels, exactly 20 cm per pixel. Every run
writes a JSON manifest with request dimensions, bounds, the acquisition date
sampled at the tile centre, attribution, file size, SHA-256 hashes, the GDAL
version and all COG creation options. Source image, validated COG and manifest
are built in a temporary directory; the complete bundle is published by one
atomic directory rename. `--dry-run` performs no network or file write.

This command is not a nationwide downloader. For a production state build,
prefer the official bulk-download channel in `sources.json`, download one state
version at a time, validate complete coverage, and publish the finished
version atomically. WMS is retained for this bounded pilot and as a potential
runtime fallback.

The current Bavaria licence requires attribution. Because COG creation and web
delivery resample or recompress the source, the generated derivative should use:

```text
Bayerische Vermessungsverwaltung – www.geodaten.bayern.de (Daten verändert)
```

The bundle manifest records that derivative attribution explicitly. Before a
self-hosted bundle is activated, the viewer must read or mirror that value; the
current live WMS attribution remains unchanged until such an activation exists.
