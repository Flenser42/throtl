# throtl-app

The new Throtl desktop GUI: **Tauri 2 + React + TypeScript + Vite + Tailwind CSS v4**.
It replaces the old GTK4/libadwaita window (removed in this release) while the
Python daemon, CLI and socket protocol stay exactly as they are.

> All redesign phases are done: the GTK window and its tests are gone, and the
dashboard covers rules, profiles, schedules, global caps, budgets (with desktop
alerts), statistics and the update notice.

## Architecture

```
React (src/)  ──invoke()/events──▶  Rust (src-tauri/)  ──Unix socket──▶  throtl-daemon
   UI only                            socket.rs          JSON-RPC          (unchanged)
```

- **JS cannot open a Unix socket**, so `src-tauri/src/socket.rs` owns the
  connection to `/run/throtl/daemon.sock` (newline-delimited JSON-RPC),
  correlates responses by id, polls `list_processes` at 1 Hz and emits
  `daemon:update`; connection state is emitted as `daemon:state`
  (`connected` / `offline` / `denied`).
- Write actions are `#[tauri::command]`s (`daemon_set_global`, `daemon_set_process`, …)
  in `src-tauri/src/commands.rs`; the frontend calls them through
  `src/lib/ipc.ts`.
- The daemon is **never** talked to from the webview directly, and the CSP
  keeps the webview local-only.

## Develop

```bash
npm install
npm run dev            # http://localhost:1420
```

Without a running daemon / Tauri shell the UI runs on a deterministic mock
(`VITE_MOCK=1`, or simply opened in a browser): `src/lib/mock.ts`. URL
parameters make the UI deterministic for screenshots and deep links:
`?static=1` freezes the mock, `?theme=light|dark`, `?pin=27` pins the graph
crosshair, `?sheet=new|rule|stats|budgets|settings`, `?pop=profile`, `?menu=1`
and `?mockstate=offline|denied|connecting`. `L` toggles light/dark.

```bash
npm run build          # typecheck + production bundle into dist/
npm run preview        # serve dist/
```

## Desktop build (needs Rust + WebKitGTK)

```bash
# Debian/Ubuntu system deps
sudo apt install libwebkit2gtk-4.1-dev build-essential curl wget file \
  libxdo-dev libssl-dev librsvg2-dev libayatana-appindicator3-dev
# Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

npm run tauri dev      # runs the Rust shell + Vite
npm run tauri build    # .deb / .rpm / .AppImage
```

If `cargo` is not on `PATH` (rustup installed with `--no-modify-path`), prefix
with `PATH="$HOME/.cargo/bin:$PATH"`. With the full system deps present the
bundler needs no sudo and produces `src-tauri/target/release/bundle/`
(`.deb`, `.rpm`, `.AppImage`); `cargo clippy --all-targets -- -D warnings` and
`cargo check` are expected to stay clean.

`src-tauri/icons/*.png` were generated from `../data/hicolor/scalable/apps/throtl.svg`.

## Layout

```
src/
  App.tsx                 composition + sheet/profile routing
  styles/globals.css      design tokens (OKLCH) + component classes
  lib/  ipc.ts api.ts types.ts format.ts model.ts mock.ts buildModel.ts
  hooks/useDaemon.ts      connection, live view-model and write actions
  components/             Header, Overview (hero + graph), LiveGraph,
                          Toolbar, ProcessList, Sheet, SettingsSheet,
                          StatisticsSheet, BudgetsSheet, RuleSheet,
                          ProfileMenu, States, Toast, AnimatedNumber, icons
src-tauri/
  src/socket.rs           Unix-socket JSON-RPC client + 1 Hz poller
  src/commands.rs         tauri::command wrappers
  src/lib.rs  src/main.rs
```

All colours come from the CSS tokens in `globals.css`; components never
hard-code a colour. Numbers use JetBrains Mono with `tabular-nums`.

## Status

- [x] Phase 0 — Tauri + React + Tailwind scaffold, tokens, fonts
- [x] Phase 1 — Rust socket bridge, commands, events, mock mode
- [x] Phase 2 — Dashboard (header, live graph, process list)
- [x] Phase 3 — Settings / Statistics / Budgets / rule editor / states / toasts
- [x] Phase 4 — Motion (reduced-motion aware), empty/offline states, screenshots
- [x] Phase 5 — frontend + Rust CI, release bundles (`.deb`/`.rpm`)
- [x] Phase 6 — GTK window removed after feature parity, dashboard polish
