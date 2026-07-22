import assert from 'node:assert/strict';
import { access, readFile } from 'node:fs/promises';
import test from 'node:test';

const root = new URL('../', import.meta.url);
const main = await readFile(new URL('openkataster_tiles/main.py', root), 'utf8');
const frontendProxy = await readFile(
    new URL('deploy/nginx/frontend-api-proxy.conf', root),
    'utf8',
);

async function exists(path) {
    return access(new URL(path, root)).then(() => true, () => false);
}

test('only the canonical viewer implementation remains in the tile service', async () => {
    assert.equal(await exists('app.py'), false);
    assert.equal(await exists('bin/deploy_viewer.sh'), false);

    assert.doesNotMatch(main, /\bdef viewer_key\(/);
    assert.doesNotMatch(main, /\bdef viewer_html\(/);
    assert.doesNotMatch(main, /["']\/embed-contract-v1\.js["']/);
    assert.doesNotMatch(main, /["']\/embed-runtime\/\{dataset\}["']/);
});

test('the deployment proxy no longer advertises removed viewer runtimes', () => {
    assert.doesNotMatch(frontendProxy, /\/embed-contract-v1\.js/);
    assert.doesNotMatch(frontendProxy, /\/embed-runtime\//);
    assert.match(frontendProxy, /location \^~ \/embed\//);
});
