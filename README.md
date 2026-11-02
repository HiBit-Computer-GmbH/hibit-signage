# HiBit Signage

Digital Signage für Raspberry Pi OS Lite, mit lokaler Webkonsole und optionaler Cloud. Der Pi baut die Cloud-Verbindung selbst auf und holt sich Wiedergabe, Dateien und Freigaben. Eingehende Ports am Pi sind dafür nicht nötig.

## Was abgespielt wird

- Bilder und Videos per HTTP(S)-Adresse
- Webseiten. Der Player holt sie selbst, auch wenn die Seite Iframes verbietet. Warteschlange, Scroll und Fortschrittsbalken bleiben.
- Dateien, die in der Webkonsole hochgeladen werden
- Dateien von SMB- oder NFS-Freigaben

Zuverlässig im Kiosk-Browser sind JPEG, PNG, GIF, WebP, SVG, MP4 (H.264) und WebM. Ist die Warteschlange leer, zeigt der Bildschirm die IP der Webkonsole.

## Player installieren

Auf Raspberry Pi OS Lite (Bookworm oder neuer):

```bash
git clone https://github.com/HiBit-Computer-GmbH/hibit-signage.git
cd hibit-signage
sudo ./install/install-player.sh
```

Mit Cloud, die Adresse und der Schlüssel kommen aus der Cloud-Konsole:

```bash
sudo ./install/install-player.sh \
  --cloud-url https://signage.example.com \
  --device-key HB-XXXX-XXXX-XXXX-XXXX
```

Das Skript installiert Python, Chromium, Cage, SMB/NFS-Client und legt einen Systemdienst an. Die Anzeige startet auf tty1.

Shell zum Debuggen:

- `Strg + Alt + S` oder `Strg + Alt + F2` öffnet tty2
- `Strg + Alt + F1` kehrt zur Anzeige zurück

Die lokale Konsole ist `http://<IP-des-Pi>:8080/`. Benutzer `admin`, das Passwort gibt das Installationsskript aus. Danach `initial-password.txt` im Datenverzeichnis löschen und das Passwort in der Konsole ändern.

Das öffentliche Repository kann der Pi ohne Zugangsdaten ziehen. Ein privater Fork braucht einen Deploy Key oder einen Credential Helper. Das Update nutzt `git pull --ff-only` und fragt nicht interaktiv nach Zugangsdaten.

## Cloud installieren

Auf einem Linux-Server, nicht auf dem Anzeige-Pi:

Die Cloud läuft bei uns hinter dem Nginx Proxy Manager. `--behind-proxy` setzt `HIBIT_TRUST_PROXY` und `HIBIT_COOKIE_SECURE`, damit Anmeldung und Geräteverbindung über die öffentliche Adresse funktionieren.

```bash
git clone https://github.com/HiBit-Computer-GmbH/hibit-signage.git
cd hibit-signage
sudo ./install/install-cloud.sh --admin-email admin@example.com --behind-proxy
```

Die Konsole lauscht danach nur auf Port 8090. Im Nginx Proxy Manager einen Proxy Host anlegen:

- Domain: die öffentliche Adresse, zum Beispiel `signage.example.com`
- Scheme: `http`
- Forward Hostname: die IP des Cloud-Servers, oder `127.0.0.1`, wenn der Proxy Manager auf demselben Rechner läuft
- Forward Port: `8090`
- Websockets Support: ein
- SSL: Zertifikat anfordern und HTTP auf HTTPS umleiten

In Advanced, Custom Nginx Configuration:

```nginx
client_max_body_size 4096m;
proxy_read_timeout 3600s;
proxy_send_timeout 3600s;
```

Websockets bleiben an, sonst verbinden sich die Pis nicht und die Shell in der Cloud bleibt stumm. Uploads gehen bis 4 GB. Ein eigenes Nginx ohne Proxy Manager steht in `install/nginx-cloud.conf.example`.

### Geräte aufnehmen

1. Als Cloud-Admin einen Mandanten anlegen.
2. Unter Schlüssel einen Einschreibeschlüssel erzeugen. Optional hängt der Schlüssel schon an einem Mandanten.
3. Den Player mit `--cloud-url` und `--device-key` installieren, oder die Werte später in der lokalen Konsole unter System eintragen.
4. Das Gerät erscheint als wartend. Adoptieren vergibt Name und Mandant.
5. Warteschlange, Uploads, SMB/NFS und die Shell gibt es danach auch in der Cloud. Der Pi holt den Stand und lädt Cloud-Dateien selbst.

Benutzer gibt es in drei Rollen: Cloud-Admin, Mandanten-Admin, Mandanten-Benutzer. Mandanten sehen nur ihre Geräte. Den Update-Schalter für ein Gerät und für die Cloud selbst hat nur der Cloud-Admin.

Entfernte Geräte melden sich mit demselben Schlüssel wieder als neues wartendes Gerät.

## Update

- Player, Seite System: zieht das Repository und startet Player und Anzeige neu.
- Cloud, Seite System: nur Cloud-Admin, startet die Cloud neu.
- Cloud, Gerät, Reiter Gerät: nur Cloud-Admin, schickt das Update an den Pi.

Beide Update-Skripte liegen nach der Installation root-geschützt in `/usr/local/sbin` und machen nur Fast-Forward-Pulls. Der Verlauf erscheint live. Wenn das Update durch ist, lädt die Seite neu.

## Lokal entwickeln

Python 3.11 oder neuer:

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r player\requirements.txt -r cloud\requirements.txt
$env:HIBIT_ADMIN_PASSWORD = "adminadmin"
$env:PYTHONPATH = "player"
.venv\Scripts\python -m hibit_player --port 8080
```

In einer zweiten Shell, mit `PYTHONPATH=cloud`, die Cloud auf Port 8090 starten. SMB, NFS, Cage und die Update-Skripte gibt es nur auf dem installierten Linux-System.

## Daten

Player: `/var/lib/hibit-signage`  
Cloud: `/var/lib/hibit-cloud`  
Code: `/opt/hibit-signage`

Die Dienste laufen als Benutzer `hibit`. Die Web-Shell ist die Shell dieses Benutzers, nicht root. Einhängen und Updates laufen über feste root-Skripte, die nur Pfade unter `/mnt/hibit` und die eigenen Unit-Dateien anfassen.
