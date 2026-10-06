//! System tray icon and menu.
//!
//! The tray mirrors the daemon's global shaping switch ("Shaping on/off"):
//! the check item is updated from the daemon's own `list_processes` snapshot
//! (see [`crate::socket`]), so it stays correct no matter which surface —
//! tray, dashboard or CLI — flipped the switch. Clicking the item toggles the
//! daemon through the same `toggle_enabled` method the `daemon_toggle` command
//! uses, then notifies the webview so it refetches its model.

use std::sync::Arc;

use serde_json::json;
use tauri::{
    menu::{CheckMenuItem, Menu, MenuItem},
    tray::TrayIconBuilder,
    AppHandle, Emitter, Manager,
};

use crate::socket::Bridge;

/// Managed state that lets other modules (the socket poller) keep the tray's
/// "Shaping on/off" check item in sync with the daemon.
pub struct TrayState {
    shaping: CheckMenuItem<tauri::Wry>,
}

impl TrayState {
    /// Reflect the daemon's global shaping switch in the tray menu.
    pub fn set_shaping(&self, enabled: bool) {
        let _ = self.shaping.set_checked(enabled);
    }
}

/// Update the tray's "Shaping on/off" check item from the daemon state.
/// No-op if the tray was never built.
pub fn set_shaping(app: &AppHandle, enabled: bool) {
    if let Some(state) = app.try_state::<TrayState>() {
        state.set_shaping(enabled);
    }
}

/// Create the tray icon and its menu. Called once from the app `setup` hook.
pub fn build(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open Throtl", true, None::<&str>)?;
    let shaping =
        CheckMenuItem::with_id(app, "shaping", "Shaping on/off", true, false, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &shaping, &quit])?;

    let icon = tauri::image::Image::from_bytes(include_bytes!("../icons/32x32.png"))?;

    // Keep the check item reachable for the socket poller's sync.
    app.manage(TrayState {
        shaping: shaping.clone(),
    });

    let _tray = TrayIconBuilder::new()
        .icon(icon)
        .menu(&menu)
        .tooltip("Throtl")
        .on_menu_event(move |app, event| match event.id().as_ref() {
            "open" => {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.unminimize();
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
            "shaping" => {
                // muda auto-toggles the check item on click, so is_checked()
                // already holds the desired new state.
                let next = shaping.is_checked().unwrap_or(false);
                let bridge = app.state::<Arc<Bridge>>().inner().clone();
                let handle = app.clone();
                tauri::async_runtime::spawn(async move {
                    match bridge.call("toggle_enabled", json!({ "enabled": next })).await {
                        Ok(_) => {
                            let _ = handle.emit("daemon:tray-toggle", next);
                        }
                        Err(err) => {
                            let _ = handle.emit("daemon:error", err);
                        }
                    }
                });
            }
            "quit" => app.exit(0),
            _ => {}
        })
        .build(app)?;

    Ok(())
}
