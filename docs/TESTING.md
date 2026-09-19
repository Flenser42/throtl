# TESTING.md — how Throtl is tested

This document describes what is covered by the automated tests, how to verify
manually (without the GUI) that limits actually apply, and what the known
limitations are.

---

## Automated tests

```bash
make check           # ruff + full unittest suite
make test            # tests only (python3 -m unittest discover -s tests)
make lint            # ruff check .
```

Current suite (`tests/`):

| Module | What it covers | Mocked |
|--------|----------------|--------|
| `test_units` | parse/format kbps/kBs | pure logic |
| `test_protocol` | JSON-over-Unix-socket framing, buffering, client timeout/events/errors | in-process socketpair |
| `test_config` | TOML persistence round-trip, priorities, rule escaping, matching | temp file |
| `test_monitor` | nethogs `-t` parser (Refreshing ticks, recv=download/sent=upload, different processes), NethogsMonitor | injected fake stream |
| `test_engine` | TrafficToll YAML rendering, `tt` subprocess (start/restart/disabled), SimEngine | fake `tt` shell script |
| `test_daemon_cli` | end-to-end daemon (sim) + CLI: status, set_global + persistence, set_process round-trip/update, toggle, set_unit, list | real Unix socket, SimEngine + fake monitor |
| `test_gui` | rate formatting, priority mapping, GuiClient RPC against a real daemon, process table (grouping/sorting/in-place updates), bandwidth graph (window/auto-scroll) | widget tests skipped without a display |

> GUI widget instantiation (`PriorityDropdown`, `RuleEditor`, `ProcessTable`,
> `BandwidthGraph`) is automatically **skipped** in a headless environment (no
> Wayland/X11 display). On a running Wayland session the full GUI suite runs.

---

## Manual: testing limits without opening the GUI

### 1) Is the daemon reachable, and what is the baseline?

```bash
# real service (after installation)
systemctl status netlimiter-clone
throtl-cli status

# alternative: run the daemon manually in simulation mode
#   throtl-daemon --simulate --socket /tmp/t.sock --config-dir /tmp/tcfg &
throtl-cli --socket /tmp/t.sock status
```

The output should show `Shaping: ON` and an `Engine: …running…` line.

### 2) Set a rule and check persistence

```bash
throtl-cli set-process --name mydl --exe /usr/bin/curl \
    --download-limit 512kbps --priority hoch
cat /etc/netlimiter-clone/config.toml    # process entry present?
```

### 3) Actually throttle bandwidth (only with real TrafficToll + root)

In **one** terminal:

```bash
throtl-cli set-global --download-limit 50mbps --upload-limit 10mbps
throtl-cli set-process --name curl --exe /usr/bin/curl \
    --download-limit 512kbps --upload-limit 128kbps
```

In a **second** terminal, measure the same transfer:

```bash
# downlink
curl -o /dev/null -w 'down=%{speed_download} B/s\n' \
     'https://speed.cloudflare.com/__down?bytes=10000000'
# uplink
curl -o /dev/null -w 'up=%{speed_upload} B/s\n' -F 'file=@somefile' \
     https://speed.cloudflare.com/__up
```

If the measured rate is clearly below the link capacity and close to the limit
(`512000 B/s` ≈ 512 kbit/s = 64 KB/s), the limits are working. Remove the rule
with:

```bash
throtl-cli remove-process --key 'exe:/usr/bin/curl'
```

### 4) Check live monitoring

```bash
throtl-cli monitor        # per-second active processes + rates
```

### 5) Temporarily disable shaping

```bash
throtl-cli toggle --enabled false   # no tc rules active
throtl-cli toggle --enabled true    # back
```

---

## Limits & known points

- **nethogs naming**: nethogs reports the command line as the process name. Rules
  match on `exe` / `name` (escaped literals) or `cmdline` (regex). The GUI groups
  processes by application and shows live traffic; mapping a PID to a rule is
  heuristic because of nethogs' name format.
- **TrafficToll has no SIGHUP reload**: every config/engine change restarts `tt`
  (see README). There can be a sub-second interruption while switching; the
  limits apply again immediately afterwards.
- **Prioritisation needs caps**: without a global `download`/`upload` limit there
  is only per-app limiting, no QoS prioritisation.
- **`0` is a real 0-limit** (blocks everything). For “no limit”, omit the key, or
  leave the GUI field empty.
- **Interface selection**: the daemon auto-detects the default-route interface.
  For VPN tunnels (e.g. `tailscale0`/`tun0`) pin it with `--interface`.
- **GUI instantiation**: headless environments cannot run the widget tests (a
  display is required). On Omarchy/Hyprland they run.
