# throtl-app

The new Throtl desktop GUI: **Tauri 2 + React + TypeScript + Vite + Tailwind CSS v4**.
It replaces the GTK4/libadwaita window with a modern dashboard while the Python
daemon, CLI and socket protocol stay exactly as they are.

> Phase 0–2 of the redesign spec: scaffold, daemon bridge, dashboard. The GTK
> GUI remains in `../throtl/gui/` until this app reaches feature parity.

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
(`VITE_MOCK=1`, or simply opened in a browser): `src/lib/mock.ts`. Add
`?static=1` to freeze the mock for screenshots, `?theme=light` for light mode
(or press `L`).

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

`src-tauri/icons/*.png` were generated from `../data/hicolor/scalable/apps/throtl.svg`.

## Layout

```
src/
  App.tsx                 dashboard composition (bento tiles, graph, list)
  styles/globals.css      design tokens (OKLCH) + component classes
  lib/  ipc.ts types.ts format.ts model.ts mock.ts buildModel.ts
  hooks/useDaemon.ts      connection + live view-model
  components/             Header, StatTile, LiveGraph, TopTalkers,
                          GlobalsTile, Toolbar, ProcessList, Sparkline, …
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
- [x] Phase 2 — Dashboard (header, stat tiles, live graph, globals, process list)
- [ ] Phase 3 — Settings / Statistics / Budgets / states
- [ ] Phase 4 — Motion, toasts, README screenshots
- [ ] Phase 5 — Packaging + CI, remove the GTK GUI after parity
