//! Throtl desktop shell: a Tauri 2 app whose Rust side bridges to the Python
//! daemon over its Unix socket. The webview renders React and talks to the
//! bridge through `#[tauri::command]`s and events only.

mod commands;
mod socket;

use std::path::PathBuf;

use socket::Bridge;

pub fn run() {
    let socket_path = std::env::var("THROTL_SOCKET")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("/run/throtl/daemon.sock"));

    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .setup(move |app| {
            let bridge = Bridge::spawn(app.handle().clone(), socket_path.clone());
            app.manage(bridge);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::daemon_call,
            commands::daemon_status,
            commands::daemon_get_config,
            commands::daemon_get_state,
            commands::daemon_list_processes,
            commands::daemon_get_budgets,
            commands::daemon_list_profiles,
            commands::daemon_reset_stats,
            commands::daemon_set_global,
            commands::daemon_set_process,
            commands::daemon_remove_process,
            commands::daemon_toggle,
            commands::daemon_set_unit,
            commands::daemon_get_stats,
            commands::daemon_get_stats_history,
            commands::daemon_set_budget,
            commands::daemon_remove_budget,
            commands::daemon_activate_profile,
            commands::daemon_set_profile,
            commands::daemon_delete_profile,
            commands::daemon_set_schedule,
            commands::daemon_set_start_profile,
            commands::daemon_import_config,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Throtl");
}
