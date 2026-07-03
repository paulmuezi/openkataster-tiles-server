#!/opt/openkataster-tiles/venv/bin/python

#!/opt/openkataster-tiles/venv/bin/python
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config

ENV_PATH = Path('/etc/openkataster-tiles.env')
VOLUME_ROOT = Path(os.environ.get('OPENKATASTER_VOLUME_TILE_ROOT', '/mnt/HC_Volume_105964091/openkataster-active'))
DATA_DIR = Path(os.environ.get('OPENKATASTER_TILE_DATA_DIR', '/srv/openkataster-tiles/data'))
DETAIL_SHARD_RE = re.compile(r'^alkis_detail_(\d{3})\.pmtiles$')


def load_env() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def log(*parts: object) -> None:
    print(time.strftime('%Y-%m-%d %H:%M:%S'), *parts, flush=True)


def s3_client():
    bucket = (os.environ.get('OPENKATASTER_TILE_BUCKET') or os.environ.get('EXPORT_BUCKET') or '').strip().rstrip('/').split('/')[-1]
    if not bucket:
        raise RuntimeError('OPENKATASTER_TILE_BUCKET is not configured')
    endpoint = os.environ.get('OPENKATASTER_TILE_BUCKET_ENDPOINT') or os.environ.get('EXPORT_BUCKET_ENDPOINT')
    region = os.environ.get('OPENKATASTER_TILE_BUCKET_REGION') or os.environ.get('EXPORT_BUCKET_REGION') or 'nbg1'
    client = boto3.client(
        's3',
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=os.environ.get('OPENKATASTER_TILE_BUCKET_ACCESS_KEY_ID') or os.environ.get('HETZNER_BUCKET_ACCESS_KEY_ID'),
        aws_secret_access_key=os.environ.get('OPENKATASTER_TILE_BUCKET_SECRET_ACCESS_KEY') or os.environ.get('HETZNER_BUCKET_SECRET_ACCESS_KEY'),
        config=Config(retries={'max_attempts': 12, 'mode': 'adaptive'}, connect_timeout=30, read_timeout=300),
    )
    return bucket, client


def active_states(bucket: str, client) -> list[str]:
    prefix = (os.environ.get('OPENKATASTER_TILE_BUCKET_PREFIX') or 'tiles').strip('/')
    states = []
    # Keep this explicit to avoid listing bucket-wide secrets and unrelated prefixes.
    candidates = ['bremen', 'hamburg', 'niedersachsen']
    for state in candidates:
        try:
            client.head_object(Bucket=bucket, Key=f'{prefix}/{state}/active.json')
        except Exception:
            continue
        states.append(state)
    return states


def active_key(state: str) -> str:
    prefix = (os.environ.get('OPENKATASTER_TILE_BUCKET_PREFIX') or 'tiles').strip('/')
    return f'{prefix}/{state}/active.json'


def read_json(bucket: str, client, key: str) -> dict:
    return json.loads(client.get_object(Bucket=bucket, Key=key)['Body'].read().decode('utf-8'))


def put_json(bucket: str, client, key: str, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
    client.put_object(Bucket=bucket, Key=key, Body=body, ACL='private', ContentType='application/json')


def link_names(state: str, filename: str) -> list[str]:
    if filename == 'alkis.pmtiles':
        return [f'{state}_detail.pmtiles']
    if filename == 'overview.pmtiles':
        return [f'{state}_overview.pmtiles']
    match = DETAIL_SHARD_RE.match(filename)
    if match:
        return [f'{state}_detail_{match.group(1)}.pmtiles']
    if filename == 'features.sqlite':
        return [f'{state}.features.sqlite']
    if filename == 'alkis_overview_boundaries.json':
        return [f'{state}_overview_boundaries.json']
    if filename == 'alkis_overview_labels.json':
        return [f'{state}_overview_labels.json']
    if filename == 'style.json':
        return [f'{state}_detail.style.json', f'{state}.style.json']
    return []


def reusable_local_candidates(state: str, filename: str) -> list[Path]:
    candidates = []
    for name in link_names(state, filename):
        candidates.append(DATA_DIR / name)
    # Bremen pilot path from the first benchmark.
    candidates.append(Path('/mnt/HC_Volume_105964091/openkataster-test') / state / filename)
    return candidates


class Progress:
    def __init__(self, label: str, total: int) -> None:
        self.label = label
        self.total = total
        self.seen = 0
        self.last = 0
        self.started = time.time()

    def __call__(self, amount: int) -> None:
        self.seen += amount
        step = 1024 * 1024 * 1024 if self.total >= 8 * 1024**3 else 256 * 1024 * 1024
        if self.seen - self.last < step and self.seen < self.total:
            return
        self.last = self.seen
        elapsed = max(time.time() - self.started, 0.1)
        mib_s = self.seen / 1024 / 1024 / elapsed
        pct = self.seen / self.total * 100 if self.total else 0
        log('progress', self.label, f'{pct:.1f}%', f'{mib_s:.1f} MiB/s')


def copy_candidate(target: Path, expected: int, state: str, filename: str) -> bool:
    for candidate in reusable_local_candidates(state, filename):
        try:
            if candidate.resolve() == target.resolve():
                continue
        except OSError:
            pass
        if not candidate.exists() or not candidate.is_file():
            continue
        if expected and candidate.stat().st_size != expected:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f'.{target.name}.copying')
        tmp.unlink(missing_ok=True)
        log('copy-local', candidate, '->', target)
        shutil.copy2(candidate, tmp)
        tmp.replace(target)
        return True
    return False


def ensure_file(bucket: str, client, state: str, version: str, item: dict, transfer: TransferConfig) -> Path:
    filename = str(item['filename'])
    object_key = str(item['object_key'])
    expected = int(item.get('content_length') or item.get('size_bytes') or item.get('size') or 0)
    target = VOLUME_ROOT / 'versions' / state / version / filename
    if target.exists() and (expected == 0 or target.stat().st_size == expected):
        log('reuse', state, filename, target.stat().st_size)
        return target
    if expected and copy_candidate(target, expected, state, filename):
        if target.stat().st_size == expected:
            return target
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f'.{target.name}.download')
    tmp.unlink(missing_ok=True)
    log('download', state, filename, expected, object_key)
    with tmp.open('wb') as handle:
        client.download_fileobj(bucket, object_key, handle, Config=transfer, Callback=Progress(f'{state}/{filename}', expected))
    got = tmp.stat().st_size
    if expected and got != expected:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f'size mismatch for {state}/{filename}: got={got} expected={expected}')
    tmp.replace(target)
    return target


def symlink_atomic(source: Path, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    tmp = link.with_name(f'.{link.name}.next')
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(source)
    os.replace(tmp, link)


def sync_state(bucket: str, client, state: str, transfer: TransferConfig) -> None:
    key = active_key(state)
    manifest = read_json(bucket, client, key)
    version = str(manifest.get('version_name') or 'active')
    files = manifest.get('files')
    if not isinstance(files, list):
        raise RuntimeError(f'{state}: active manifest has no files')
    log('state-start', state, version, 'files', len(files))

    changed_manifest = False
    linked: dict[str, Path] = {}
    local_manifest_files = []
    for item in files:
        if not isinstance(item, dict) or not item.get('filename') or not item.get('object_key'):
            continue
        filename = str(item['filename'])
        names = link_names(state, filename)
        if not names:
            continue
        head = client.head_object(Bucket=bucket, Key=str(item['object_key']))
        actual_size = int(head['ContentLength'])
        actual_etag = str(head.get('ETag') or '').strip('"')
        for field in ('size_bytes', 'content_length'):
            if item.get(field) != actual_size:
                item[field] = actual_size
                changed_manifest = True
        if actual_etag and item.get('etag') != actual_etag:
            item['etag'] = actual_etag
            changed_manifest = True
        path = ensure_file(bucket, client, state, version, item, transfer)
        if path.stat().st_size != actual_size:
            raise RuntimeError(f'{state}/{filename}: local size mismatch after download')
        local_manifest_files.append({**item, 'local_path': str(path)})
        for name in names:
            linked[name] = path

    local_manifest = {
        'state_slug': state,
        'version_name': version,
        'source_active_key': key,
        'synced_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'files': local_manifest_files,
    }
    manifest_path = VOLUME_ROOT / 'versions' / state / version / 'manifest.local.json'
    manifest_path.write_text(json.dumps(local_manifest, ensure_ascii=False, indent=2), encoding='utf-8')

    if changed_manifest:
        log('update-active-json', state, key)
        put_json(bucket, client, key, manifest)

    for name, path in sorted(linked.items()):
        symlink_atomic(path, DATA_DIR / name)
        log('link', DATA_DIR / name, '->', path)

    active_link = VOLUME_ROOT / 'active' / state
    active_link.parent.mkdir(parents=True, exist_ok=True)
    symlink_atomic(VOLUME_ROOT / 'versions' / state / version, active_link)
    log('state-complete', state, version, 'links', len(linked))


def main() -> int:
    load_env()
    bucket, client = s3_client()
    states = sys.argv[1:] or active_states(bucket, client)
    if not states:
        raise RuntimeError('no active states found')
    VOLUME_ROOT.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    transfer = TransferConfig(
        multipart_threshold=64 * 1024 * 1024,
        multipart_chunksize=64 * 1024 * 1024,
        max_concurrency=int(os.environ.get('OPENKATASTER_VOLUME_SYNC_CONCURRENCY', '3')),
        use_threads=True,
    )
    log('sync-start', 'states', ','.join(states), 'volume', VOLUME_ROOT)
    for state in states:
        sync_state(bucket, client, state, transfer)
    log('sync-finished')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
