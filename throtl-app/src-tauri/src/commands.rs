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
    let helper = std::env::var("THROTL_SETUP_HELPER")
        .unwrap_or_else(|_| "/usr/local/bin/throtl-setup".to_string());

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
