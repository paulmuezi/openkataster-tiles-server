const LEGACY_STYLE_URL = '/viewer-assets/viewer-app/bkg-style.json?v=20260724-europe1';
const CONFIG_URL = '/api/v1/basemap/config';
const EUROPE_SOURCE_ID = 'openkataster_europe';
const VERSION_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$/;

export function normalizeBasemapMode(value) {
  return ['off', 'preview', 'on'].includes(value) ? value : 'off';
}

export function selectBasemapProfile(config, search = '') {
  const mode = normalizeBasemapMode(config?.mode);
  const requested = new URLSearchParams(String(search || '').replace(/^\?/, '')).get('basemap');
  const available = config?.europe?.available === true
    && VERSION_PATTERN.test(String(config?.europe?.version || ''));
  if (!available || mode === 'off') return 'national';
  if (requested === 'national') return 'national';
  if (mode === 'preview') return requested === 'europe' ? 'europe' : 'national';
  return 'europe';
}

function sameOriginPath(value, locationObject) {
  const candidate = new URL(String(value || ''), locationObject.origin);
  if (candidate.origin !== locationObject.origin) throw new Error('Basemap asset must be same-origin.');
  return `${candidate.pathname}${candidate.search}`
    .replace(/%7B([zxy])%7D/gi, '{$1}');
}

function europeRuntime(config, style, locationObject) {
  const europe = config.europe;
  const version = String(europe.version);
  const tileTemplate = sameOriginPath(europe.tile_template, locationObject);
  if (!tileTemplate.includes('{z}') || !tileTemplate.includes('{x}') || !tileTemplate.includes('{y}')) {
    throw new Error('Europe basemap tile template is incomplete.');
  }
  if (!style || style.version !== 8 || !Array.isArray(style.layers)) {
    throw new Error('Europe basemap style is invalid.');
  }
  const source = style.sources?.[EUROPE_SOURCE_ID];
  if (!source || source.type !== 'vector') {
    throw new Error('Europe basemap style source is missing.');
  }
  const separator = tileTemplate.includes('?') ? '&' : '?';
  source.tiles = [`${tileTemplate}${separator}v=${encodeURIComponent(version)}`];
  source.minzoom = Number.isFinite(Number(europe.minzoom)) ? Number(europe.minzoom) : 0;
  source.maxzoom = Number.isFinite(Number(europe.maxzoom)) ? Number(europe.maxzoom) : 15;
  if (Array.isArray(europe.bounds) && europe.bounds.length === 4) {
    source.bounds = europe.bounds.map(Number);
  }
  style.metadata = {
    ...(style.metadata || {}),
    'openkataster:runtime-version': version
  };
  return {
    profile: 'europe',
    style,
    mode: normalizeBasemapMode(config.mode),
    version,
    sourceId: EUROPE_SOURCE_ID,
    attribution: europe.attribution || '© OpenStreetMap contributors',
    fallback: 'national'
  };
}

export async function resolvePlannerBasemap({
  locationObject = window.location,
  fetchImpl = window.fetch.bind(window),
  timeoutMs = 1800
} = {}) {
  const legacy = {
    profile: 'national',
    style: LEGACY_STYLE_URL,
    mode: 'off',
    version: 'national-20260723',
    sourceId: '',
    attribution: '',
    fallback: 'national'
  };
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), Math.max(250, Number(timeoutMs) || 1800));
  try {
    const configResponse = await fetchImpl(CONFIG_URL, {
      credentials: 'same-origin',
      cache: 'no-store',
      signal: controller.signal,
      headers: { Accept: 'application/json' }
    });
    if (!configResponse.ok) return legacy;
    const config = await configResponse.json();
    if (selectBasemapProfile(config, locationObject.search) !== 'europe') {
      return { ...legacy, mode: normalizeBasemapMode(config?.mode) };
    }
    const styleUrl = sameOriginPath(config.europe.style_url, locationObject);
    const styleResponse = await fetchImpl(styleUrl, {
      credentials: 'same-origin',
      cache: 'force-cache',
      signal: controller.signal,
      headers: { Accept: 'application/json' }
    });
    if (!styleResponse.ok) return legacy;
    return europeRuntime(config, await styleResponse.json(), locationObject);
  } catch (error) {
    if (error?.name !== 'AbortError') {
      console.warn('Europa-Grundkarte nicht verfügbar; nationale Grundkarten werden verwendet.', error);
    }
    return legacy;
  } finally {
    window.clearTimeout(timeout);
  }
}

export function installEuropeBasemapFailover(map, runtime, {
  locationObject = window.location,
  replace = (url) => window.location.replace(url),
  threshold = 4,
  windowMs = 12000
} = {}) {
  if (runtime?.profile !== 'europe' || !runtime.sourceId) return () => {};
  let failures = [];
  let finished = false;

  const removeOldFailures = (now) => {
    failures = failures.filter((timestamp) => now - timestamp <= windowMs);
  };
  const fallBack = () => {
    if (finished) return;
    finished = true;
    const target = new URL(locationObject.href);
    target.searchParams.set('basemap', 'national');
    replace(target.toString());
  };
  const onError = (event) => {
    const sourceId = event?.sourceId || event?.source?.id || event?.error?.sourceId;
    if (sourceId !== runtime.sourceId) return;
    const now = Date.now();
    failures.push(now);
    removeOldFailures(now);
    if (failures.length >= Math.max(2, Number(threshold) || 4)) fallBack();
  };
  const onSourceData = (event) => {
    if (event?.sourceId !== runtime.sourceId || event?.isSourceLoaded !== true) return;
    failures = [];
  };

  map.on('error', onError);
  map.on('sourcedata', onSourceData);
  return () => {
    map.off?.('error', onError);
    map.off?.('sourcedata', onSourceData);
  };
}

export const BASEMAP_RUNTIME_CONSTANTS = Object.freeze({
  configUrl: CONFIG_URL,
  legacyStyleUrl: LEGACY_STYLE_URL,
  europeSourceId: EUROPE_SOURCE_ID
});
