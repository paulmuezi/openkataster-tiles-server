# Betrieb

## Backups

Der große Server erstellt täglich um 02:20 Uhr eine Sicherung unter
`/srv/openkataster-backups/daily`. Sie enthält konsistente SQLite-Backups von
PocketBase und Usage-Daten, aktive Tile-Manifeste, App-Konfiguration und die
jeweilige Git-Revision. Der Website-Server sichert seine Konfiguration täglich
um 02:35 Uhr unter `/srv/openkataster-backups/website`.

Jeder Lauf prüft die SQLite-Integrität, die Tar-Archive und eine temporäre
Wiederherstellung der wichtigsten Konfigurationsdateien. Statusdateien liegen
unter `/var/lib/openkataster-backup/`.

Für eine externe Kopie wird auf dem jeweiligen Server in der passenden
`/etc/openkataster-*-backup.env` gesetzt:

```bash
OFFSITE_RSYNC_TARGET=backup-user@backup-host:/path/to/openkataster
```

Die großen PMTiles-Versionen werden nicht täglich dupliziert. Sie müssen vor
dem Produktivstart einmalig auf eine Storage Box oder einen zweiten Server
repliziert werden.

## Monitoring

`openkataster-monitor.timer` läuft auf dem Website-Server alle fünf Minuten.
Er prüft Website, Planer, API, PocketBase, lokalen freien Speicher, den letzten
Website-Backup-Lauf sowie Docker- und Speicherstatus des großen Servers.

Alarm- und Wiederherstellungs-E-Mails werden erst nach Eintrag einer Adresse in
`/etc/openkataster-monitoring.env` versandt:

```bash
ALERT_EMAIL=operations@example.com
```

Der Zugriff vom Website-Server auf den großen Server verwendet den eingeschränkten
Benutzer `ok-monitor` mit einem erzwungenen Statuskommando.

## Wiederherstellung

1. Betroffenen Dienst stoppen.
2. Passenden Backup-Ordner auswählen und `SHA256SUMS` prüfen.
3. SQLite-Dateien mit `sqlite3 <datei> 'PRAGMA integrity_check;'` kontrollieren.
4. Konfiguration nach einem temporären Entpacken gezielt zurückspielen.
5. Dienst starten und `/health`, `/api/v1` sowie `/planer` prüfen.

Eine vollständige Wiederherstellung der PMTiles erfordert die externe
Tile-Replikation; die aktiven Manifeste im täglichen Backup geben die benötigten
Versionen eindeutig an.
