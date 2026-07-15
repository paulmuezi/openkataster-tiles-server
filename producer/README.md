# ALKIS production and repair tools

These tools reproduce the cross-shard building-address repair used for the
Niedersachsen runtime index. They never modify PostgreSQL dumps and always
build outputs in a temporary file before an atomic replacement.

## Relation patch

The extractor needs PostgreSQL client tools (`pg_restore`) and reads only
`ax_gemeinde`, `ax_lagebezeichnungkatalogeintrag`,
`ax_lagebezeichnungmithausnummer`, and `ax_gebaeude` from each dump:

```bash
python producer/extract_alkis_building_relation_patch.py \
  --dumps-dir /path/to/dumps \
  --pattern 'alkis_niedersachsen_*.dump' \
  --output /path/to/building-relations.sqlite \
  --summary /path/to/building-relations.json
```

References are resolved only after all shards have been read. Historical
catalog entries are selected by the newest active `beginnt` timestamp; an
ambiguous newest value or conflicting GML references aborts the build.

Apply the validated relation patch to a copy of `features.sqlite`:

```bash
python producer/apply_alkis_building_relation_patch.py \
  --features /path/to/features.sqlite \
  --relations /path/to/building-relations.sqlite \
  --postcode-search /path/to/previous/search.sqlite \
  --report /path/to/apply-report.json
```

The postcode index only supplies unambiguous label context. It never decides
whether a building relation exists and there is no building-to-parcel address
fallback.

## Merge chunk indexes

```bash
python producer/merge_alkis_feature_indexes.py \
  --parts-dir /path/to/chunks \
  --out /path/to/features.sqlite
```

Feature geometry is deduplicated globally by `(kind, gml_id)`. Addresses found
in overlapping shards are rewritten to the canonical feature's `source_db`,
because runtime and search joins are source-aware. Inputs are quick-checked;
the merged database is published atomically.

Generated SQLite databases, compressed outputs, reports, and repair working
directories are runtime artifacts and must remain outside this repository.
