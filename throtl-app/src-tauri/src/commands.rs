//! Tauri commands. Each one forwards to the daemon socket via `Bridge`,
//! which the frontend reaches with `invoke("daemon_<name>", { params })`.

use std::process::{Command, Stdio};
use std::sync::Arc;

use serde_json::{json, Value};
use tauri::State;

use crate::socket::Bridge;

type CmdResult = Result<Value, String>;

macro_rules! commands_without_params {
    ($($name:ident => $method:literal),* $(,)?) => {
        $(
            #[tauri::command]
            pub async fn $name(bridge: State<'_, Arc<Bridge>>) -> CmdResult {
                bridge.call($method, json!({})).await
            }
        )*
    };
}

macro_rules! commands_with_params {
    ($($name:ident => $method:literal),* $(,)?) => {
        $(
            #[tauri::command]
            pub async fn $name(
                bridge: State<'_, Arc<Bridge>>,
                params: Option<Value>,
            ) -> CmdResult {
                bridge.call($method, params.unwrap_or_else(|| json!({}))).await
            }
        )*
    };
}

commands_without_params!(
    daemon_status => "status",
    daemon_get_config => "get_config",
    daemon_get_state => "get_state",
    daemon_list_processes => "list_processes",
    daemon_get_budgets => "get_budgets",
    daemon_list_profiles => "list_profiles",
    daemon_list_interfaces => "list_interfaces",
    daemon_reset_stats => "reset_stats",
);

commands_with_params!(
    daemon_set_global => "set_global",
    daemon_set_process => "set_process",
    daemon_remove_process => "remove_process",
    daemon_toggle => "toggle_enabled",
    daemon_set_unit => "set_unit",
    daemon_get_stats => "get_stats",
    daemon_get_stats_history => "get_stats_history",
    daemon_set_budget => "set_budget",
    daemon_remove_budget => "remove_budget",
    daemon_activate_profile => "activate_profile",
    daemon_set_profile => "set_profile",
    daemon_delete_profile => "delete_profile",
    daemon_set_schedule => "set_schedule",
    daemon_set_start_profile => "set_start_profile",
    daemon_set_interface => "set_interface",
    daemon_import_config => "import_config",
);

/// Trigger Throtl's one-time privileged setup through polkit.
///
/// Spawns `pkexec` on the `throtl-setup` helper. pkexec asks the desktop user
/// for an admin password via the session's polkit authentication agent and then
/// runs the helper as root (granting the user membership of the `throtl` group,
/// or — from a source checkout — running the full installer). The program and
/// its single argument are hard-coded here, so the webview can *request* the
/// setup but never executes arbitrary commands.
#[tauri::command]
pub async fn setup_run() -> Result<(), String> {
    // Fixed path only: the polkit policy matches exactly this path, and an
    // env-controlled override would let a crafted launch environment redirect
    // the admin-authenticated pkexec at an arbitrary binary.
    let helper = "/usr/local/bin/throtl-setup".to_string();

    if !std::path::Path::new(&helper).exists() {
        return Err(format!(
            "setup helper not found at {helper} - install Throtl first (see README)"
        ));
    }

    let mut child = Command::new("pkexec")
        .arg(&helper)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| match e.kind() {
            std::io::ErrorKind::NotFound => {
                "pkexec not found - install polkit and a polkit authentication agent".to_string()
            }
            _ => format!("failed to start pkexec: {e}"),
        })?;

    std::thread::spawn(move || {
        if let Err(e) = child.wait() {
            eprintln!("throtl-setup (pkexec) did not exit cleanly: {e}");
        }
    });

    Ok(())
}

/// Path of the per-user XDG autostart entry for the dashboard.
///
/// Uses `$XDG_CONFIG_HOME` when it is set and non-empty, falling back to
/// `$HOME/.config` as the XDG Base Directory spec prescribes.
fn autostart_path() -> std::path::PathBuf {
    let config = std::env::var("XDG_CONFIG_HOME")
        .ok()
        .filter(|s| !s.is_empty())
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| {
            std::path::PathBuf::from(std::env::var("HOME").unwrap_or_default()).join(".config")
        });
    config.join("autostart").join("throtl-app.desktop")
}

const AUTOSTART_DESKTOP: &str = "\
[Desktop Entry]
Type=Application
Name=Throtl
Comment=Per-application bandwidth limits and traffic prioritisation
Exec=throtl-app --background
Terminal=false
X-GNOME-Autostart-enabled=true
";

/// Whether the dashboard is currently registered to start on login.
#[tauri::command]
pub fn get_autostart() -> bool {
    autostart_path().exists()
}

/// Create (`enabled`) or remove the per-user autostart entry.
#[tauri::command]
pub fn set_autostart(enabled: bool) -> Result<(), String> {
    let path = autostart_path();
    if enabled {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        std::fs::write(&path, AUTOSTART_DESKTOP).map_err(|e| e.to_string())?;
    } else {
        let _ = std::fs::remove_file(&path);
    }
    Ok(())
}
