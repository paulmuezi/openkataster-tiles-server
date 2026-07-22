import assert from 'node:assert/strict';
import test from 'node:test';

import {
  exportLocationFilenameSegment,
  resolveExportLocationLabel
} from '../live-viewer/viewer-app/export.js';


test('empty center hit falls back to the selected Bayern building address', async () => {
  const label = await resolveExportLocationLabel({
    value: { lng: 11.492354, lat: 48.980616 },
    featureAt: async () => ({ count: 0, buildings: [], parcels: [] }),
    selection: {
      buildings: [{ address: 'Ortsstraße 19, 92339 Beilngries' }],
      parcels: []
    }
  });

  assert.equal(label, 'Ortsstraße 19, 92339 Beilngries');
  assert.equal(
    exportLocationFilenameSegment(label),
    'Ortsstrasse_19_92339_Beilngries'
  );
});


test('feature at the export center has priority over the current selection', async () => {
  const label = await resolveExportLocationLabel({
    value: { lng: 11.5, lat: 49.0 },
    featureAt: async () => ({
      buildings: [{ address: 'Mittelpunktweg 1, Beispielstadt' }],
      parcels: []
    }),
    selection: {
      buildings: [{ address: 'Auswahlstraße 2, Beispielstadt' }],
      parcels: []
    }
  });

  assert.equal(label, 'Mittelpunktweg 1, Beispielstadt');
});


test('selected parcel provides a useful cadastral fallback', async () => {
  const label = await resolveExportLocationLabel({
    value: { lng: 11.5, lat: 49.0 },
    featureAt: async () => ({ buildings: [], parcels: [] }),
    selection: {
      buildings: [],
      parcels: [{ gemarkung: 'Bemerode', flur: '4', flurstueck: '12/3' }]
    }
  });

  assert.equal(label, 'Gemarkung Bemerode, Flur 4, Flurstück 12/3');
});


test('raw coordinates are not used as a visible filename segment', async () => {
  const label = await resolveExportLocationLabel({
    value: { lng: 11.492354, lat: 48.980616 },
    featureAt: async () => ({ buildings: [], parcels: [] }),
    selection: { buildings: [], parcels: [] }
  });

  assert.equal(label, '48.980616, 11.492354');
  assert.equal(exportLocationFilenameSegment(label), 'Kartenausschnitt');
});
