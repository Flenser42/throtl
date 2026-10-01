// Desktop notifications (budget alerts). Best effort: inside Tauri we ask for
// permission once and then send; in a plain browser this is a no-op. The Rust
// side already registers tauri-plugin-notification and the capability grants
// `notification:default`.

import { isTauri } from "./ipc";

export async function notify(title: string, body: string): Promise<void> {
  if (!isTauri()) return;
  try {
    const mod = await import("@tauri-apps/plugin-notification");
    let granted = await mod.isPermissionGranted();
    if (!granted) granted = (await mod.requestPermission()) === "granted";
    if (!granted) return;
    mod.sendNotification({ title, body });
  } catch {
    /* notifications are optional — never break the UI over them */
  }
}
