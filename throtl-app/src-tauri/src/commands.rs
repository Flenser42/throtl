//! Tauri commands. Each one forwards to the daemon socket via `Bridge`,
//! which the frontend reaches with `invoke("daemon_<name>", { params })`.

use std::sync::Arc;

use serde_json::{json, Value};
use tauri::State;

use crate::socket::Bridge;

type CmdResult = Result<Value, String>;

/// Generic escape hatch: `invoke("daemon_call", { method, params })`.
#[tauri::command]
pub async fn daemon_call(
    bridge: State<'_, Arc<Bridge>>,
    method: String,
    params: Option<Value>,
) -> CmdResult {
    bridge.call(&method, params.unwrap_or_else(|| json!({}))).await
}

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
