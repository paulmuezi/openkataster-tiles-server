# Europe-Basemap: reproduzierbarer Produktionsbetrieb

Diese Skripte bauen und betreiben die selbst gehostete, europaweit
einheitliche OpenKataster-Grundkarte. Sie verändern weder ALKIS-Runtimes noch
die nationalen Grundkarten. Der nationale Bestand bleibt in allen Modi der
technische Rückfall.

## Fest definierte Eingaben

| Eingabe | Wert |
|---|---|
| Datensatz | Protomaps Basemap v4, täglicher Build |
| Reproduzierter Stand | `20260723` |
| Quell-URL | `https://build.protomaps.com/20260723.pmtiles` |
| Ausschnitt | `-25,34,45,72` |
| Zoomstufen | `0` bis `15` |
| Werkzeug | `pmtiles 1.31.2`, Linux x86_64 |
| Releasearchiv SHA-256 | `3ed7dbf4ec2e6dfe5e25b6f70d1ffc932729f93c86db353bf514dd71010a312f` |
| Binary SHA-256 | `a7e9ae10184d109c83f456ccdf6df4f3e2a64ba6cf69d9ed0f9f1840305055c1` |
| Runtime-Schema | Manifest `schema_version: 1` |

Es gibt bewusst kein `latest`. Für ein späteres Update wird ein konkretes,
bereits veröffentlichtes Tagesdatum über `--build-date YYYYMMDD` angegeben.
URL, Versionsname und Manifest leiten sich deterministisch daraus ab. Der
resultierende Archiv-Hash wird im Manifest festgeschrieben.

Die Daten sind aus OpenStreetMap abgeleitet. In der Karte muss die sichtbare
Attribution `© OpenStreetMap contributors` erhalten bleiben. Die Basemap-Tiles
sind ein Produced Work unter den für Protomaps/OSM geltenden Bedingungen; die
mitgelieferten Style-/Asset-Lizenzen werden separat im Viewer-Release geführt.

## Produktionslayout

```text
/srv/openkataster-tiles/basemaps/europe/
├── .incoming/                         # niemals vom API-Prozess gelesen
├── versions/
│   ├── europe-20260723-z15/
│   │   ├── basemap.pmtiles
│   │   └── manifest.json
│   └── europe-YYYYMMDD-z15/
├── active   -> versions/europe-…-z15
├── previous -> versions/europe-…-z15
└── mode                                # off | preview | on
```

Eine Version wird vollständig in `.incoming` erzeugt. Erst nach
`pmtiles verify`, Header-, v4-Layer-Schema-, Größen- und SHA-Prüfung wird das
fertige Verzeichnis innerhalb desselben Dateisystems atomar nach `versions`
verschoben. Eine aktive Version wird niemals in-place geändert.

Es dürfen höchstens zwei Versionen auf diesem Runtime-Dateisystem liegen. Die
Skripte löschen nie automatisch Daten. Vor dem dritten Build wird eine nicht
aktive alte Version mit `archive-release.sh` bewusst auf ein **anderes
Dateisystem** kopiert, dort vollständig geprüft und erst danach aus `versions`
entfernt. Ein Archiv unterhalb des Runtime-Roots ist verboten, weil es die
Kapazitätsgrenze nur verstecken würde.

## Vorbedingungen

- Linux x86_64, Bash 4+, Python 3
- `curl`, `flock`, `ionice`, `lsof`, `sha256sum`, `tar`

Die Vollprüfung vor Aktivierung und Archivierung liest das vollständige Archiv.
Hash-, PMTiles- und Schema-Prüfungen laufen deshalb mit `nice -n 10` und
`ionice -c 2 -n 7`, um den Livebetrieb nicht unnötig unter I/O- oder
Page-Cache-Druck zu setzen.
- für Produktion Docker oder alternativ eine systemd-Service-Unit
- Root-Dateisystem mit mindestens dem größeren Wert aus:
  - 15 Prozent der Dateisystemgröße
  - 130 GiB frei
- der Checkout beziehungsweise das Ops-Verzeichnis muss während eines
  gestarteten Builds unverändert am gleichen Pfad verfügbar bleiben

Die Build-Runtime liegt unter `/srv`. `/Volumes/Sandisk_4TB` ist weder ein
Default noch ein unterstütztes Produktionsziel dieser Skripte.

## 1. Plan prüfen

```bash
bash scripts/europe-basemap/build-release.sh \
  --build-date 20260723 \
  --dry-run
```

Der Dry-run prüft Plattform, Werkzeuge, freien Speicher, Lock und
Versionskapazität, lädt aber nichts herunter. Eine fremde oder abgebrochene
PMTiles-Datei in `.incoming` blockiert einen neuen Build, bis offene Handles
und Fehlerursache geprüft und der Bestand bewusst bereinigt wurden. So kann
ein harter Neustart nicht unbemerkt mehrere große Teilarchive ansammeln.

## 2. Gedrosselten Build starten

Für einen langen Serverlauf wird die transiente systemd-Unit verwendet:

```bash
sudo bash scripts/europe-basemap/launch-build-systemd.sh \
  --build-date 20260723
```

Der ausgegebene Unit-Name ist eindeutig. Fortschritt:

```bash
systemctl status openkataster-europe-basemap-build-20260723-YYYYMMDDHHMMSS
journalctl -fu openkataster-europe-basemap-build-20260723-YYYYMMDDHHMMSS
```

Die Unit begrenzt den Prozess mit `Nice=10`, `CPUWeight=20`, `IOWeight=20`
und `MemoryMax=8G`. `RestrictAddressFamilies=AF_INET AF_UNIX` erzwingt IPv4
und vermeidet bekannte lange IPv6-Verbindungen, die am CDN kurz vor Ende
zurückgesetzt werden können. Der Extract selbst verwendet vier Download-
Threads und niedrige I/O-Priorität. Ein zweiter Build wird durch `flock`
abgewiesen.

Bei einem Fehler startet systemd nach 30 Sekunden neu, jedoch höchstens
dreimal innerhalb von 24 Stunden (`StartLimitBurst=3`). `pmtiles extract`
unterstützt für diesen Vorgang kein verlässliches Resume: Jeder Versuch startet
den Extract vollständig neu und schreibt eine neue Datei. Eine regulär
fehlgeschlagene Versuchsstage wird vom Buildskript entfernt. Nach einem
harten Kill verbleibende Stages werden niemals adoptiert oder aktiviert und
müssen erst nach Prüfung auf offene Handles bewusst bereinigt werden.

Alternativ kann der Build in einer bereits dauerhaft laufenden administrativen
Session direkt ausgeführt werden:

```bash
sudo bash scripts/europe-basemap/build-release.sh --build-date 20260723
```

Der direkte Prozess darf nicht durch Abbruch der SSH-Session beendet werden.

## 3. Release unabhängig prüfen

```bash
sudo bash scripts/europe-basemap/verify-release.sh \
  --release-dir \
  /srv/openkataster-tiles/basemaps/europe/versions/europe-20260723-z15
```

Die Standardprüfung liest die gesamte Datei für den SHA-256 und prüft
zusätzlich:

- PMTiles-Struktur (`pmtiles verify`)
- `tile_type=mvt`, `tile_compression=gzip`
- Zoom `0–15` und vollständige Ziel-Bounds
- Protomaps-Basemap-Schema `4.x`
- die exakte, vorab lizenzgeprüfte Layerliste; neue oder fehlende
  Upstream-Layer erzwingen vor dem nächsten Update einen No-Go
- OpenStreetMap- und ESA-WorldCover-Attribution samt Lizenzinventar
- Manifest-Schema 1, Größe, Buildwerkzeug und Binary-Hash

`--quick` überspringt nur den großen Datei-Hash. Es ist ausschließlich für
häufige Statusprüfungen gedacht.

## 4. Bereits fertigen manuellen Extract einmalig übernehmen

Falls der erste Produktions-Extract bereits mit den exakt gleichen Parametern
manuell gestartet wurde, darf er nach dem erfolgreichen Abschluss ohne
erneuten Download übernommen werden:

```bash
sudo bash scripts/europe-basemap/adopt-extract.sh \
  --input \
  /srv/openkataster-tiles/basemaps/europe/.incoming/europe-20260723-z15.pmtiles.part \
  --build-date 20260723 \
  --confirm-version europe-20260723-z15
```

Das Kommando verweigert eine noch geöffnete Datei, prüft das OSM-
Replikationsdatum gegen den Tagesbuild, alle Buildparameter, PMTiles-Struktur
und v4-Schema, berechnet Hash und Manifest und veröffentlicht erst danach das
Release-Verzeichnis atomar. Es aktiviert die Version ausdrücklich nicht.
Eine Null-Header-/97-Prozent-`.part` aus einem abgebrochenen go-pmtiles-
Extract wird bereits vor der teuren Prüfung hart abgewiesen; sie ist technisch
nicht fortsetzbar oder reparierbar.

## 5. Zuerst als Preview aktivieren

Ein bereits mit der früheren reinen OSM-Provenienz übernommener, noch nie
aktivierter Release wird nicht per Hand editiert. Nach dem Code-Deploy im
bestätigten Modus `off` erzeugt der Migrationsbefehl das Manifest aus dem
vorhandenen Archiv und dessen echten Metadaten neu. Er prüft den vollständigen
Hash, sichert das alte Manifest außerhalb des Release-Inventars und ersetzt es
atomar:

```bash
sudo bash scripts/europe-basemap/migrate-legacy-manifest.sh \
  --version europe-20260723-z15 \
  --confirm-version europe-20260723-z15
```

Dies ist die einzige bewusste Ausnahme vom Grundsatz, veröffentlichte
Release-Verzeichnisse nicht in-place zu ändern, und ist nur vor der
Erstaktivierung erlaubt.

```bash
sudo bash scripts/europe-basemap/activate-release.sh \
  --version europe-20260723-z15 \
  --mode preview
```

Die Aktivierung prüft den Release erneut vollständig, schaltet `active` und
`previous` mit atomaren Symlink-Replacements, startet nur
`openkataster-tiles-api` kontrolliert neu und prüft anschließend:

- internen Health-Endpunkt
- öffentliche Config auf `https://tiles.openkataster.de`
- echten, versionierten z0-Tile
- MVT-/gzip-/ETag-/immutable-Cache-Header
- `lsof +L1`, damit kein Prozess gelöschte Runtime-Dateien offen hält

Schlägt ein Schritt fehl, stellt das Skript Pointer und Modus automatisch
wieder her und startet die Tiles-API erneut. Ohne `--no-public-smoke` ist der
öffentliche Smoke verpflichtend. Für eine lokale Umgebung können API- und
öffentliche URL ausdrücklich überschrieben werden.

Im Preview bleibt die nationale Karte Standard. Die Europakarte ist mit
`?basemap=europe` testbar; `?basemap=national` erzwingt weiterhin den
Rückfall.

## 6. Feature-Flag umschalten

```bash
sudo bash scripts/europe-basemap/set-mode.sh preview
sudo bash scripts/europe-basemap/set-mode.sh on
sudo bash scripts/europe-basemap/set-mode.sh off
```

Die Modi:

- `off`: Runtime serverseitig deaktiviert; nationale Karte
- `preview`: nationale Karte als Standard, Europe nur per Query-Flag
- `on`: Europe als Standard, nationale Karte per Rückfall-Flag

Die `mode`-Datei wird atomar ersetzt; ein API-Restart ist dafür nicht nötig.
Jeder Wechsel wird intern und öffentlich geprüft und bei einem Fehler
automatisch zurückgenommen.

## 7. Sicherer Rollback

```bash
sudo bash scripts/europe-basemap/rollback-release.sh
```

`active` und `previous` werden kontrolliert getauscht. Vorheriger Modus,
vollständige Release-Prüfung, Neustart, Smokes, `lsof +L1` und automatische
Wiederherstellung gelten genauso wie bei einer Aktivierung.

Vorabansicht:

```bash
sudo bash scripts/europe-basemap/rollback-release.sh --dry-run
```

## 8. Dritte Version vorbereiten

Bei zwei vorhandenen Versionen bricht ein neuer Build vor dem Download ab.
Eine nicht aktive Version wird ausdrücklich archiviert:

```bash
sudo bash scripts/europe-basemap/archive-release.sh \
  --version europe-20260723-z15 \
  --confirm europe-20260723-z15 \
  --archive-root /mnt/openkataster-basemap-archive \
  --dry-run

sudo bash scripts/europe-basemap/archive-release.sh \
  --version europe-20260723-z15 \
  --confirm europe-20260723-z15 \
  --archive-root /mnt/openkataster-basemap-archive
```

`--archive-root` muss auf einem anderen Dateisystem liegen. Die aktive Version
kann nicht archiviert werden. Die Kopie wird vor dem Entfernen der Quelle
vollständig geprüft; ein passender `previous`-Pointer wird kontrolliert
entfernt. Das externe Archiv wird nicht gelöscht.

## Störungen

- **Build unterbrochen:** Nur das eindeutige Verzeichnis unter `.incoming`
  ist unvollständig. Kein Pointer zeigt dorthin. Ein Null-Header-`.part` wird
  nicht adoptiert. Der systemd-Launcher startet mit IPv4 höchstens drei
  vollständige Versuche; es gibt kein Resume.
- **Manifest/Hash falsch:** Nicht aktivieren. Quelle, Datumsstand und
  Binary-Hash prüfen; niemals das Manifest passend „umschreiben“.
- **Aktivierungs-Smoke fehlgeschlagen:** Das Skript rollt selbst zurück.
  Danach `docker logs openkataster-tiles-api` und den öffentlichen Endpunkt
  prüfen.
- **`lsof +L1` meldet PMTiles:** Keine Version archivieren. Tiles-API
  kontrolliert neu starten und prüfen, bis keine gelöschte Runtime-Datei mehr
  offen ist.
- **Europe-Karte im Browser fehlerhaft:** Sofort `set-mode.sh off`; die
  nationalen Grundkarten bleiben verfügbar.

## Tests

```bash
bash scripts/europe-basemap/tests/run.sh
```

Der Vertragstest prüft Bash-Syntax, alle `--help`-Einstiege, die gepinnten
Konstanten sowie Manifest-Erzeugung, Schema- und Manipulationserkennung.
