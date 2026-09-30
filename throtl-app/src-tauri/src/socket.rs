//! Unix-socket JSON-RPC client for the Throtl daemon.
//!
//! The daemon (throtl/daemon.py) speaks newline-delimited JSON over
//! `/run/throtl/daemon.sock`. JS cannot open a Unix socket, so the Rust side
//! owns the connection, correlates request/response by id, polls
//! `list_processes` once a second and forwards snapshots to the UI as
//! `daemon:update` events. Connection state is reported as `daemon:state`.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::unix::OwnedWriteHalf;
use tokio::net::UnixStream;
use tokio::sync::{mpsc, oneshot, Mutex};

type Pending = Arc<Mutex<HashMap<u64, oneshot::Sender<Result<Value, String>>>>>;

pub struct Request {
    method: String,
    params: Value,
    reply: oneshot::Sender<Result<Value, String>>,
}

/// Handle the commands use to send a request to the daemon.
pub struct Bridge {
    tx: mpsc::Sender<Request>,
}

impl Bridge {
    pub fn spawn(app: AppHandle, socket_path: PathBuf) -> Arc<Self> {
        let (tx, rx) = mpsc::channel(64);
        tauri::async_runtime::spawn(connection_loop(app, socket_path, rx));
        Arc::new(Self { tx })
    }

    pub async fn call(&self, method: &str, params: Value) -> Result<Value, String> {
        let (reply, rx) = oneshot::channel();
        self.tx
            .send(Request {
                method: method.to_string(),
                params,
                reply,
            })
            .await
            .map_err(|_| "daemon bridge is closed".to_string())?;
        match tokio::time::timeout(CALL_TIMEOUT, rx).await {
            Ok(Ok(result)) => result,
            Ok(Err(_)) => Err("daemon bridge dropped the request".to_string()),
            Err(_) => Err("daemon request timed out".to_string()),
        }
    }
}

/// No single RPC may hang the UI forever.
const CALL_TIMEOUT: Duration = Duration::from_secs(10);
/// Matches the daemon's protocol.py MAX_MESSAGE_SIZE (1 MiB).
const MAX_MESSAGE_SIZE: usize = 1 << 20;

async fn connection_loop(app: AppHandle, socket_path: PathBuf, mut rx: mpsc::Receiver<Request>) {
    let mut backoff = Duration::from_millis(500);
    loop {
        match UnixStream::connect(&socket_path).await {
            Ok(stream) => {
                backoff = Duration::from_millis(500);
                let _ = app.emit("daemon:state", "connected");
                match run_connection(&app, stream, &mut rx).await {
                    Ok(true) => {}
                    // All senders dropped (app teardown): stop reconnecting.
                    Ok(false) => return,
                    Err(error) => {
                        let _ = app.emit("daemon:error", error);
                    }
                }
                let _ = app.emit("daemon:state", "offline");
            }
            Err(error) => {
                let state = if error.kind() == std::io::ErrorKind::PermissionDenied {
                    "denied"
                } else {
                    "offline"
                };
                let _ = app.emit("daemon:state", state);
            }
        }
        tokio::time::sleep(backoff).await;
        backoff = (backoff * 2).min(Duration::from_secs(10));
    }
}

async fn run_connection(
    app: &AppHandle,
    stream: UnixStream,
    rx: &mut mpsc::Receiver<Request>,
) -> Result<bool, String> {
    let (read_half, mut write_half) = stream.into_split();
    let mut reader = BufReader::new(read_half);
    let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
    let mut next_id: u64 = 1;
    let mut line = String::new();
    let mut poll = tokio::time::interval(Duration::from_secs(1));
    poll.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);

    loop {
        tokio::select! {
            _ = poll.tick() => {
                write_request(&mut write_half, &pending, &mut next_id, "list_processes", json!({}), None)
                    .await?;
            }
            request = rx.recv() => {
                match request {
                    Some(Request { method, params, reply }) => {
                        write_request(&mut write_half, &pending, &mut next_id, &method, params, Some(reply))
                            .await?;
                    }
                    // All senders dropped: nothing left to serve.
                    None => return Ok(false),
                }
            }
            read = reader.read_line(&mut line) => {
                let n = read.map_err(|e| e.to_string())?;
                if n == 0 {
                    return Err("daemon closed the connection".into());
                }
                if line.len() > MAX_MESSAGE_SIZE {
                    return Err("daemon sent an oversized message".into());
                }
                if let Ok(value) = serde_json::from_str::<Value>(line.trim()) {
                    dispatch(app, &pending, value).await;
                }
                line.clear();
            }
        }
    }
}

async fn write_request(
    write_half: &mut OwnedWriteHalf,
    pending: &Pending,
    next_id: &mut u64,
    method: &str,
    params: Value,
    reply: Option<oneshot::Sender<Result<Value, String>>>,
) -> Result<(), String> {
    let id = *next_id;
    *next_id += 1;
    if let Some(reply) = reply {
        pending.lock().await.insert(id, reply);
    }
    let message = json!({ "id": id, "method": method, "params": params });
    write_half
        .write_all(format!("{message}\n").as_bytes())
        .await
        .map_err(|e| e.to_string())?;
    write_half.flush().await.map_err(|e| e.to_string())?;
    Ok(())
}

async fn dispatch(app: &AppHandle, pending: &Pending, value: Value) {
    // Server-pushed events (no id).
    if let Some(event) = value.get("event") {
        let _ = app.emit("daemon:event", event.clone());
        return;
    }
    let Some(id) = value.get("id").and_then(Value::as_u64) else {
        return;
    };
    match pending.lock().await.remove(&id) {
        Some(sender) => {
            let outcome = if let Some(error) = value.get("error") {
                Err(error
                    .get("message")
                    .and_then(Value::as_str)
                    .unwrap_or("daemon error")
                    .to_string())
            } else {
                Ok(value.get("result").cloned().unwrap_or(Value::Null))
            };
            let _ = sender.send(outcome);
        }
        None => {
            // Unsolicited snapshot from the 1 Hz poller -> push to the UI.
            if let Some(result) = value.get("result") {
                let _ = app.emit("daemon:update", result.clone());
            }
        }
    }
}
