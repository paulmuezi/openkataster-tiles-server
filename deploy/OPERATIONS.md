# Betrieb

## Backups

Der große Server erstellt alle sechs Stunden eine Sicherung unter
`/srv/openkataster-backups/daily`. Sie enthält konsistente SQLite-Backups von
PocketBase, Usage- und Upload-Control-Daten, PocketBase-Dateien, API-
Recovery-Code, aktive Tile-Manifeste, App-Konfiguration und die jeweilige
Git-Revision. Der Website-Server sichert seine Konfiguration weiterhin täglich
um 02:35 Uhr unter `/srv/openkataster-backups/website`.

Jeder Lauf prüft die SQLite-Integrität, die Tar-Archive und eine temporäre
Wiederherstellung der wichtigsten Konfigurationsdateien. Statusdateien liegen
unter `/var/lib/openkataster-backup/`.

Nach erfolgreicher lokaler Prüfung wird der Critical-Satz mit einem Public Key
verschlüsselt und per eingeschränktem SFTP-Zugang zum Website-/Edge-Server
übertragen. Dort wird die Ciphertext-Prüfsumme kontrolliert und das Archiv für
30 Tage rootgeschützt abgelegt. Einzelheiten und Recovery-Schlüsselpfade stehen
in `deploy/OFFSITE_BACKUP.md`.

Die großen PMTiles-Versionen werden nicht täglich dupliziert. Sie müssen vor
dem Produktivstart einmalig auf eine Storage Box oder einen zweiten Server
repliziert werden.

## Monitoring

`openkataster-monitor.timer` läuft auf dem Website-Server alle fünf Minuten.
Er prüft Website, Planer, API, PocketBase, lokalen freien Speicher, den letzten
Website-Backup-Lauf, den Eingang des verschlüsselten Core-Backups sowie Docker-
und Speicherstatus des großen Servers. Ein Core-Offsite-Backup gilt nach neun
Stunden ohne gültigen Eingang als veraltet.

Alarm- und Wiederherstellungs-E-Mails werden erst nach Eintrag einer Adresse in
`/etc/openkataster-monitoring.env` versandt:

```bash
ALERT_EMAIL=operations@example.com
```

Der Zugriff vom Website-Server auf den großen Server verwendet den eingeschränkten
Benutzer `ok-monitor` mit einem erzwungenen Statuskommando.

## Wiederherstellung

1. Betroffenen Dienst stoppen.
2. Passenden lokalen Backup-Ordner auswählen oder das verschlüsselte Archiv vom
   Edge holen und mit dem ausschließlich lokal verwahrten Private Key entschlüsseln.
3. `SHA256SUMS` im entpackten Backup-Verzeichnis prüfen.
4. SQLite-Dateien mit `sqlite3 <datei> 'PRAGMA integrity_check;'` kontrollieren.
5. Konfiguration nach einem temporären Entpacken gezielt zurückspielen.
6. Dienst starten und `/health`, `/api/v1` sowie `/planer` prüfen.

Eine vollständige Wiederherstellung der PMTiles erfordert die externe
Tile-Replikation; die aktiven Manifeste im Critical-Backup geben die benötigten
Versionen eindeutig an.
