// LEDGER desktop shell.
//
// This is deliberately almost nothing. The control surface is `ledger app`,
// a stdlib HTTP server in the Python package, and this is a window pointed at
// it plus the lifecycle around that process. Keeping the logic on the Python
// side means the browser, this window, and anything else see one
// implementation, and it keeps the shell replaceable.
//
// It starts `ledger app --no-open` as a child, waits for it to answer, and
// kills it on exit. A shell that leaves an orphaned server holding a port is
// worse than no shell, because the next launch silently talks to the old one.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::ErrorKind;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, WindowEvent};

const PORT: u16 = 8129;
const STARTUP_TIMEOUT: Duration = Duration::from_secs(20);

struct Server(Mutex<Option<Child>>);

fn url() -> String {
    format!("http://127.0.0.1:{PORT}")
}

/// True once the control surface answers. Polled rather than assumed: the
/// first paint against a server that is not up yet is a blank window, and the
/// user reads that as a broken app rather than a slow one.
fn is_up() -> bool {
    use std::io::{Read, Write};
    use std::net::TcpStream;

    let Ok(mut stream) = TcpStream::connect(("127.0.0.1", PORT)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
    if stream
        .write_all(b"GET /api/health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
        .is_err()
    {
        return false;
    }
    let mut buffer = String::new();
    let _ = stream.read_to_string(&mut buffer);
    buffer.contains("\"ok\": true") || buffer.contains("\"ok\":true")
}

/// Start our own server, unless one is already serving.
///
/// Reusing a running instance is the right default: somebody with `ledger app`
/// open in a terminal and then launching the window should get one server, not
/// a port conflict. The returned Option is None in that case, so we also do
/// not kill a server we did not start.
fn spawn_server() -> Result<Option<Child>, String> {
    if is_up() {
        return Ok(None);
    }

    let child = Command::new("ledger")
        .args(["app", "--no-open", "--port", &PORT.to_string()])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| match e.kind() {
            ErrorKind::NotFound => {
                "`ledger` is not on PATH. Install it with pip, then reopen \
                 this window."
                    .to_string()
            }
            _ => format!("could not start `ledger app`: {e}"),
        })?;

    let deadline = Instant::now() + STARTUP_TIMEOUT;
    while Instant::now() < deadline {
        if is_up() {
            return Ok(Some(child));
        }
        std::thread::sleep(Duration::from_millis(150));
    }
    Err(format!(
        "`ledger app` did not answer on port {PORT} within {}s",
        STARTUP_TIMEOUT.as_secs()
    ))
}

fn main() {
    let server = match spawn_server() {
        Ok(child) => Server(Mutex::new(child)),
        Err(message) => {
            // No window yet, so there is nowhere to render this. Stderr and a
            // non-zero exit is the honest answer; a blank window would not be.
            eprintln!("LEDGER: {message}");
            std::process::exit(1);
        }
    };

    tauri::Builder::default()
        .manage(server)
        .setup(|app| {
            let window = tauri::WebviewWindowBuilder::new(
                app,
                "main",
                tauri::WebviewUrl::External(url().parse().unwrap()),
            )
            .title("LEDGER")
            .inner_size(1100.0, 760.0)
            .min_inner_size(760.0, 520.0)
            .build()?;
            let _ = window.set_focus();
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::Destroyed = event {
                // Only ever a server we started ourselves.
                if let Some(state) = window.app_handle().try_state::<Server>() {
                    if let Ok(mut guard) = state.0.lock() {
                        if let Some(child) = guard.as_mut() {
                            let _ = child.kill();
                        }
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("failed to start the LEDGER window");
}
