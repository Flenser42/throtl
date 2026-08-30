# Throtl — NetLimiter-artige Bandbreiten-Limits & QoS für Linux

**Throtl** bringt NetLimiter-Funktionalität nach Linux/Omarchy: pro Anwendung
Bandbreiten-Limits (Download/Upload) und Traffic-Priorisierung, verwaltet über
eine native GTK4+libadwaita-GUI, steuerbar auch per CLI — ohne die Engine neu
zu erfinden.

- **Backend-Engine**: [TrafficToll](https://github.com/cryzed/TrafficToll)
  (`tt`, nutzt `tc` + cgroups unter der Haube) — läuft als eigener Subprozess.
- **Live-Monitoring**: [nethogs](https://github.com/raboof/nethogs) im
  Trace-Modus (`-t`) liefert, welcher Prozess gerade wie viel Bandbreite nutzt.
- **GPU-Sprache**: natives GTK4 + libadwaita (PyGObject), dunkles
  minimalistisches Design passend zu Omarchy/Hyprland.

---

## Architektur

```
┌────────────────────────────┐          ┌───────────────────────────────┐
│  Frontend (GUI oder CLI)   │          │  Daemon (root, systemd)       │
│  GTK4 + libadwaita         │          │  throtl.daemon                │
│  throtl.gui                │          │                               │
└────────────┬───────────────┘          └──────────────┬────────────────┘
             │  Unix-Socket /run/…/daemon.sock          │
             │  JSON, newline-delimited (throtl.protocol)│
             └───────────────► IPC ◄────────────────────┘
                     ┌──────────────┴──────────────┐
                     │  TrafficTollEngine (tt)      │  throtl.engine
                     │  NethogsMonitor (nethogs -t) │  throtl.monitor
                     │  ConfigStore (TOML)          │  throtl.config
                     └──────────────────────────────┘
```

- **Privilegierter Daemon** (`throtl-daemon`): läuft als `root`/systemd
  (`netlimiter-clone.service`), weil `tc` (Traffic Control), die IFB-Device
  und `nethogs -t` Root brauchen.
- **IPC**: ausschließlich ein **lokaler Unix-Socket**
  `/run/netlimiter-clone/daemon.sock` mit JSON-Nachrichten. Es wird **kein**
  Netzwerk-Port geöffnet. Zugriff nur über local only.
- **Config-Persistenz**: TOML unter `~/.config/netlimiter-clone/` (manueller
  Lauf) bzw. `/etc/netlimiter-clone/config.toml` (systemd, weil root nicht in
  fremde Homes schreiben soll). Die GUI liest die Config **ausschließlich**
  über die Daemon-API (`get_config`) — nie direkt aus der Datei.

### Wie Limits „greifen“

TrafficToll liest seine YAML-Config **einmal beim Start** und richtet die
`tc`-Filter dynamisch für die Ports der gematchten Prozesse ein. Einen
dynamischen „SIGHUP-Reload“ der Limits gibt es bei TrafficToll **nicht**.
Deshalb startet die Throtl-Engine den `tt`-Subprozess bei **jeder** Limits-
/Prioritäts-Änderung neu und schreibt vorher die aktuelle YAML:
```
Config-Änderung (GUI/CLI) ──► YAML rendern ──► tt stoppen ──► tt neu starten
```
Das ist der robusteste Weg; TrafficToll braucht dafür keinen Neustart des
Zielprozesses (die tc-Auflagen gelten sofort am Interface).

### Monitoring-Semantik

nethogs liefert im `-t`-Modus pro Tick eine Zeile `Name/pid/uid\tsent\trecv`.
Aus der nethogs-Quelle (`cui.cpp`, `Line::log`) gilt: die erste Zahl ist
**`sent_value`** (Uplink), die zweite **`recv_value`** (Downlink). Der
Throtl-Parser mappt daher `recv → download` und `sent → upload`; Werte werden
in ein internes **kbit/s**-Schema umgerechnet.

---

## Voraussetzungen (Omarchy / Arch Linux)

- Python 3.11+
- `nethogs`, `gtk4`, `libadwaita`, `python-gobject`, `python-cairo` (alle in
  Arch `[extra]`)
- `traffictoll` (pip) in einem venv unter `/opt/netlimiter-clone`
- `tc` wird vom Kernel bereitgestellt (HTB/prio), IFB-Modul fürs Ingress-Shaping

Das Setup-Skript installiert alles Nötige.

---

## Installation

```bash
git clone <this-repo> throtl && cd throtl
sudo ./setup/install.sh
```

Das Skript:
1. installiert die Systempakete (`nethogs`, `gtk4`, `libadwaita`, …),
2. erzeugt `/opt/netlimiter-clone/venv` und installiert `traffictoll`,
3. kopiert den Code und die Launcher nach `/opt/netlimiter-clone`,
4. legt `/etc/netlimiter-clone/config.toml` (Default) und `/run/netlimiter-clone`
   an,
5. installiert & startet den systemd-Dienst `netlimiter-clone`,
6. legt die `.desktop`-Datei + Icon an und bietet Autostart an.

Deinstallation: `sudo ./setup/uninstall.sh` (mit `--purge` auch Code/Config).

Da daemon + Engine **root** brauchen, läuft die Instanz systemweit; das
Frontend (GUI/CLI) läuft als **User** und spricht über den Unix-Socket mit dem
Daemon.

---

## Start & Verwendung

### GUI (nativ, GTK4+libadwaita)

Nach der Installation findest du „Throtl“ im App-Launcher (Quickshell-Menü /
wofi / rofi). Bewusst kein Kontrast-Chaos: dunkles Adwaita-Theme, wenige
Accentfarben.

```bash
# Manuell, falls nicht ueber den Launcher
/opt/netlimiter-clone/bin/throtl-gui
# Autostart (Login): identisch, startet sichtbar; kein unsichtbarer Tray-Modus
/opt/netlimiter-clone/bin/throtl-gui --autostart
```

Fenster bietet:
- **Ein/Aus-Schalter** für das komplette Shaping (ohne Config zu löschen).
- **Live-Graph** der Gesamtbandbreite (Download + Upload) über die letzten 60 s.
- **Live-Liste** aller Prozesse mit aktiver Netzwerkverbindung, inkl. aktueller
  Auf/Ab-Geschwindigkeit.
- **Regel-Editor**: pro Anwendung Download-/Upload-Limit (Mbit/s-Kbit/s,
  umschaltbar) + Prioritäts-Stufen (Kritisch/Hoch/Normal/Niedrig).

### CLI (ohne GUI testen)

Der Daemon lässt sich vollständig über die CLI steuern — damit kannst du
**bevor / ohne GUI** prüfen, ob Limits greifen:

```bash
# Status anzeigen
bin/throtl-cli status

# Globale Limits setzen (2 Mbit/s Download / 1 Mbit/s Upload)
bin/throtl-cli set-global --download-limit 2mbps --upload-limit 1mbps

# Regel fuer Firefox (Limit + Prioritaet) anlegen
bin/throtl-cli set-process --name Firefox --exe /usr/lib/firefox/firefox \
    --download-limit 2mbps --priority hoch

# Regel entfernen
bin/throtl-cli remove-process --key 'exe:/usr/lib/firefox/firefox'

# Shaping global an/aus
bin/throtl-cli toggle --enabled false

# Live-Bandbreiten pro Sekunde
bin/throtl-cli monitor

# Aktive Prozesse + angewendete Regeln
bin/throtl-cli list-processes
```

### Was bei einem manuellen Test passiert

Es gibt zwei Schichten:

1. **Config wirkt an TrafficToll** (ob der `tt`-Prozess die Limits gesetzt hat):
   siehe `systemctl status netlimiter-clone` und die YAML/TOML unter
   `/etc/netlimiter-clone`. `bin/throtl-cli status` zeigt das.

2. **Bandbreite real drosseln** (End-to-End): Starte eine Regel für einen
   Download-/Upload-Test in einem Terminal und misst die tatsächliche Rate
   mit `curl` oder einem TCP-Tool:

```bash
# Regel fuer einen Download (z.B. auf den 'curl'-Prozess)
bin/throtl-cli set-process --name curl --exe /usr/bin/curl \
    --download-limit 512kbps --priority normal

# separates Test-Terminal: messen, vorher/nachher
curl -o /dev/null -w 'Speed: %{speed_download} B/s\n' \
     http://example.com/bigfile.bin
```

Ohne physisches Interface/testbaren Download im sandboxartigen CLI-Test steht
der **Simulationsmodus** bereit (kein Root, kein tt nötig):

```bash
bin/throtl-daemon --simulate --socket /tmp/throtl.sock &
bin/throtl-cli --socket /tmp/throtl.sock status       # Limits sind "gesetzt"
bin/throtl-cli --socket /tmp/throtl.sock set-global --download-limit 2mbps
```

> **Hinweis zur Priorisierung**: TrafficToll-Priorisierung funktioniert nur,
> wenn das Interface-Limit (`download`/`upload`) **nahe an der realen
> Leitungstransferrate** liegt. Für „nur Pro-Anwendung-Limits“ ohne Priorisierung
> können die globalen Limits weggelassen werden (unbegrenzt).

---

## Sicherheit

- **Nur lokaler Unix-Socket** — kein TCP-Port. Der Socket lebt unter
  `/run/netlimiter-clone/` (root-owner, 0755-Verzeichnis).
- Das **Frontend ist User-prozess**; es kann über die API nur Limits/Prioritäten
  setzen, die der (root-)Daemon an TrafficToll weitergibt. Es gibt keinen
  beliebigen Shell-Zugriff über den Socket.
- Die YAML wird von der Engine deterministisch gerendert (kein User-shell-
  Injection); Match-Werte für exe/name werden `re.escape`-t, `cmdline` ist
  explizit als Regex gedacht.
- Der Daemon zwingt `NoNewPrivileges=true`; sein Dateizugriff ist auf
  `/run/netlimiter-clone` + `/etc/netlimiter-clone` begrenzt
  (`ReadWritePaths`).

---

## Projektstruktur

```
throtl/
  __init__.py        Pfad-/App-Konstanten
  units.py           kbit/s-Normschema, rate parse/format
  protocol.py        IPC: JSON over Unix-Socket, Client mit Reader-Thread
  config.py          TOML-Schema + Prioritaeten + Rule/Persistenz
  monitor.py         nethogs -t Parser + NethogsMonitor
  engine.py          TrafficToll-YAML-Render + tt-Prozess + SimEngine
  daemon.py          Unix-Socket-RPC-Daemon (root)
  cli.py             CLI (throtl-cli)
  gui/               GTK4+libadwaita Frontend
tests/               unittest-Suite
setup/               install/uninstall + systemd-unit + .desktop + Autostart
data/                Icon (SVG)
LICENSE  GPL-3.0
```

## Entwickeln & Testen

```bash
make test               # python3 -m unittest discover -s tests
make lint               # py_compile aller Module
```

Siehe `docs/TESTING.md` für Test-Optik und bekannte Grenzen.

## Lizenzen

Throtl-Code: GPL-3.0. TrafficToll: GPL-3.0. nethogs: GPL-2.0.
