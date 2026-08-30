# TESTING.md — wie Throtl getestet wird

Diese Datei beschreibt, was automatisiert getestet wird, wie man manuell ohne
GUI prüfen kann, dass Limits greifen, und welche Grenzen es gibt.

---

## Automatisierte Tests

```bash
make test            # python3 -m unittest discover -s tests
make lint            # py_compile aller Module
```

Aktuelle Suite (throtl/tests):

| Modul | Was | Gemockt |
|-------|-----|---------|
| `test_units` | parse/format kbps/kBs | reine Logik |
| `test_protocol` | JSON-over-Unix-Socket Framing, Client timeout/events/error | In-Process socketpair |
| `test_config` | TOML-Persistenz roundtrip, Prioritaeten, Rule-Escaping, Matching | tmp-Datei |
| `test_monitor` | nethogs-`-t`-Parser (Refreshing:-Ticks, recv=download/sent=upload, versch. Prozesse), NethogsMonitor | Injektion eines Fake-Streams |
| `test_engine` | TrafficToll-YAML-Render, tt-Subprozess (Start/Restart/Disabled), SimEngine | Fake-`tt`-Shellskript |
| `test_daemon_cli` | End-to-end Daemon(Sim)+CLI: status, set_global+Persistenz, set_process roundtrip, toggle, set_unit, list | echter Unix-Socket, SimEngine+FakeMonitor |
| `test_gui` | Rate-Format, Prioritaet-Mapping, GuiClient-RPC gegen echten Daemon | Widget-Tests übersprungen ohne Display |

> GUI-Widget-Instanziierung (`PriorityDropdown`, `RuleEditor`) wird in einer
> Headless-Sandbox (kein Wayland/X11-Display) automatisch **übersprungen**
> (`skipUnless`). Auf einer laufenden Omarchy-Session (Hyprland) läuft die volle
> GUI-Suite.

---

## Manuell: Limits testen, ohne die GUI zu öffnen

### 1) Zuerst: Ist der Daemon erreichbar und was ist die Basis?

```bash
# echten Dienst (nach Installation)
systemctl status netlimiter-clone
bin/throtl-cli status

# alternativ: Daemon manuell im Sim-Modus (kein root, kein tt benötigt)
#   bin/throtl-daemon --simulate --socket /tmp/t.sock --config-dir /tmp/tcfg &
bin/throtl-cli --socket /tmp/t.sock status
```

Ausgabe sollte `Shaping: AN`, `Engine: {…running…}` zeigen.

### 2) Regel setzen + Persistenz prüfen

```bash
bin/throtl-cli set-process --name mydl --exe /usr/bin/curl \
    --download-limit 512kbps --priority hoch
cat /etc/netlimiter-clone/config.toml    # process entry vorhanden?
```

### 3) Bandbreite real drosseln (nur mit echtem TrafficToll + root)

In **einem** Terminal:

```bash
bin/throtl-cli set-global --download-limit 50mbps --upload-limit 10mbps
bin/throtl-cli set-process --name curl --exe /usr/bin/curl \
    --download-limit 512kbps --upload-limit 128kbps
```

In **einem zweiten** Terminal dasselbe Messziel, dann messen:

```bash
# Downlink
curl -o /dev/null -w 'down=%{speed_download} B/s\n' https://speed.cloudflare.com/__down?bytes=10000000
# Uplink
curl -o /dev/null -w 'up=%{speed_upload} B/s\n' -F 'file=@somefile' https://speed.cloudflare.com/__up
```

Ist die gemessene Rate deutlich unter der Interface-Leistungsfähigkeit und nahe
am Limit (`512000 B/s` ≈ 512 kbit/s = 64 KB/s), greifen die Limits. Aufheben:

```bash
bin/throtl-cli remove-process --key 'exe:/usr/bin/curl'
```

### 4) Live-Monitoring prüfen

```bash
bin/throtl-cli monitor        # zeigt pro Sekunde aktive Prozesse + Raten
```

### 5) Shaping temporär deaktivieren

```bash
bin/throtl-cli toggle --enabled false   # Semantik: keine tc-Auflagen aktiv
bin/throtl-cli toggle --enabled true    # zurück
```

---

## Grenzen & bekannte Punkte

- **nethogs-Kapazität**: nethogs liefert den `name` (meist den exe-Pfad oder
  Prozessnamen). Die Throtl-Regeln matchen auf `exe` / `name` / `cmdline`
  (Regex). Bei exe/name werden die Werte `re.escape`-t → Literal-Match. Die
  UI zeigt sowohl Live-Prozesse als auch die (traffic) gefärbten Regeln; eine
  exakte 1:1-Verknüpfung PID→Regel ist wegen nethogs' Namensformat nicht immer
  eindeutig (`rule_name`-Zuordnung ist heuristisch).
- **TrafficToll hat keinen SIGHUP-Reload**: jede Config/Engine-Änderung
  startet `tt` neu (siehe README). Kurze Unterbrechung beim Umschalten ist
  möglich (Sekundenbruchteile), Limits gelten danach sofort wieder.
- **Priorisierung braucht Interface-Obergrenzen**: ohne globales
  `download`/`upload`-Limit ist nur Per-App-Limiting, keine QoS-Priorisierung.
- **GUI-Instanzierung**: Headless-Umgebungen können die Widget-Tests nicht
  ausführen (benötigt Display). Auf Omarchy/Hyprland sind sie aktiv.
- **`0` ist ein echtes 0-Limit** (blockt alles). Für „kein Limit“ den Schluessel
  weglassen bzw. in der GUI das Eingabefeld leer lassen.
- **Interface-Wahl**: Der Daemon wählt automatisch das Standard-Routing-
  Interface. Für VPN-Tunnels (z.B. `tailscale0`/`tun0`) per `--interface`
  festlegen.
