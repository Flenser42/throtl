// Thin wrapper over the Tauri command bridge. The Rust side (src-tauri)
// owns the Unix-socket connection to the daemon; the frontend never talks
// to the socket directly. When the app runs in a plain browser (UI work,
// screenshots) `isTauri()` is false and callers fall back to mock data.

export const isMock =
  import.meta.env.VITE_MOCK === "1" || !isTauri();

export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

type InvokeFn = <T>(cmd: string, args?: Record<string, unknown>) => Promise<T>;

async function getInvoke(): Promise<InvokeFn> {
  const mod = await import("@tauri-apps/api/core");
  return mod.invoke as InvokeFn;
}

/** Invoke a daemon command handled by the Rust bridge. */
export async function invokeDaemon<T>(
  cmd: string,
  args?: Record<string, unknown>,
): Promise<T> {
  if (!isTauri()) throw new Error("not running inside Tauri");
  const invoke = await getInvoke();
  return invoke<T>(`daemon_${cmd}`, { params: args ?? {} });
}

/** Subscribe to a Rust-emitted event (daemon:update, daemon:state, daemon:error). */
export async function listen<T>(
  event: string,
  handler: (payload: T) => void,
): Promise<() => void> {
  if (!isTauri()) return () => {};
  const mod = await import("@tauri-apps/api/event");
  return mod.listen<T>(event, (e) => handler(e.payload));
}

export async function emitToDaemon(cmd: string, args?: Record<string, unknown>) {
  return invokeDaemon(cmd, args);
}
