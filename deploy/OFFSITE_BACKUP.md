# Verschlüsseltes Critical-/Personendaten-Backup

Stand: 15. Juli 2026

## Umfang

Der neue Core-Server `195.201.242.216` ist ab jetzt die maßgebliche Quelle für
Nutzer-, Abo- und Bestelldaten. Historische PocketBase-Bestände der alten Web-
und API-Server werden auf ausdrückliche Entscheidung nicht in diese Sicherung
übernommen.

Alle sechs Stunden erstellt `openkataster-backup.timer` konsistente SQLite-
Online-Backups und prüft sie mit `PRAGMA integrity_check`. Enthalten sind:

- PocketBase `data.db` und `auxiliary.db`
- lokale PocketBase-Dateien beziehungsweise Profilbilder
- API-Usage und Admin-Upload-Control
- PocketBase-Migrationen, Hooks und der aktuelle API-Anwendungscode
- Runtime-/Compose-Konfiguration, Secrets und aktive Tile-Manifeste

Suchanalyse-Rohdaten und große Tile-/Parquet-Artefakte sind bewusst nicht Teil
dieses Critical-Backups. Für die Geodaten existiert eine lokale Masterkopie.

## Transport und Aufbewahrung

Der Core bündelt den geprüften Satz, verschlüsselt ihn mit dem öffentlichen
OpenPGP-Schlüssel und lädt ausschließlich das verschlüsselte Archiv samt
SHA-256-Datei auf `46.224.214.20` hoch.

Der SFTP-Benutzer `okb-core` besitzt kein Passwort, keine Shell und keinen
Zugriff außerhalb seines Chroots. Der Edge-Ingest prüft die Ciphertext-
Prüfsumme, verschiebt das Archiv in ein nur für root lesbares Verzeichnis und
bewahrt es 30 Tage auf. Der Upload-Benutzer kann bereits archivierte Stände
nicht lesen oder löschen.

Relevante Pfade und Units:

- Core lokal: `/srv/openkataster-backups/daily`
- Edge verschlüsselt: `/srv/openkataster-offsite/archive/core`
- Core-Timer: `openkataster-backup.timer`
- Edge-Ingest: `openkataster-offsite-ingest.timer`
- Edge-Status: `/var/lib/openkataster-offsite/core.env`

Das Monitoring meldet einen Fehlerzustand, wenn länger als neun Stunden kein
gültiges Offsite-Backup eingegangen ist. Die Empfängeradresse für externe
Alarm- und Recovery-E-Mails ist als `ALERT_EMAIL` in
`/etc/openkataster-monitoring.env` hinterlegt und wurde per Testmail geprüft.

## Recovery-Schlüssel

Der private Schlüssel liegt ausschließlich lokal auf Pauls Mac und darf nicht
in Git oder auf den Server kopiert werden:

- `/Users/paul/.config/openkataster/openkataster-offsite-backup-private.asc`
- `/Users/paul/.config/openkataster/backup-gpg-passphrase`
- `/Users/paul/.config/openkataster/backup-gpg/`

Der öffentliche Schlüssel liegt unter:

- `/Users/paul/.config/openkataster/openkataster-offsite-backup-public.asc`

Private Key und Passphrase müssen zusätzlich getrennt in einem Passwortmanager
oder auf einem Offline-Datenträger gesichert werden. Ohne den privaten Schlüssel
ist ein Restore der Offsite-Archive absichtlich nicht möglich.

## Restore-Prüfung

1. Archiv und `.sha256` als root vom Edge herunterladen.
2. Ciphertext mit `sha256sum -c` prüfen.
3. Archiv mit dem lokalen privaten OpenPGP-Schlüssel entschlüsseln.
4. Das äußere Tar-Archiv entpacken und darin `sha256sum -c SHA256SUMS` ausführen.
5. Jede SQLite-Datei mit `PRAGMA integrity_check` prüfen.
6. Erst danach gezielt auf einen Ersatzserver zurückspielen.

Am 15. Juli 2026 wurde dieser Ablauf vollständig in einem isolierten lokalen
Verzeichnis getestet. Ciphertext, inneres Manifest, alle vier SQLite-Dateien
und alle drei Tar-Archive wurden erfolgreich validiert; die entschlüsselten
Testdaten wurden anschließend gelöscht.

## Verbleibende Grenze

Core und Edge liegen beide bei Hetzner, und der Edge besitzt nur eine virtuelle
Disk. Diese Lösung schützt vor dem Verlust des Bare-Metal-Core, ist aber noch
keine vollständige 3-2-1-Sicherung. Eine spätere dritte Kopie gehört in einen
separaten privaten Bucket oder zu einem anderen Anbieter. Die vorhandenen
öffentlichen Tile-/Export-Buckets dürfen nicht für Personendaten verwendet
werden.
