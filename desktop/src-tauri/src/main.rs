// OpenKB desktop shell.
//
// Reference skeleton (Tauri v2). On launch it: (1) spawns the frozen
// openkb-api sidecar on a localhost port, (2) shows a splash window while
// polling the sidecar's health, (3) navigates the WebView to the sidecar once
// it answers, and (4) kills the sidecar on exit.
//
// NOTE: not compiled in the container used so far (no webkit2gtk). Build on a
// machine with the platform WebView libraries. Treat exact Tauri-v2 API details
// (navigate signature, resource path) as things to confirm against your Tauri
// version.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent, Url};

/// Held in Tauri state so the sidecar can be killed when the app exits.
struct Sidecar(Mutex<Option<Child>>);

const HOST: &str = "127.0.0.1";
const PORT: u16 = 8765;

/// Spawn the bundled PyInstaller onedir sidecar.
///
/// The sidecar is bundled as a resource *directory* named `sidecar` (see
/// `tauri.conf.json` `bundle.resources`), because PyInstaller onedir output is
/// a folder, not a single executable — so this can't use Tauri's `externalBin`
/// mechanism.
fn spawn_sidecar(app: &tauri::App) -> std::io::Result<Child> {
    let resource_dir = app
        .path()
        .resource_dir()
        .expect("resource dir should resolve");
    let exe_name = if cfg!(windows) {
        "openkb-api-sidecar.exe"
    } else {
        "openkb-api-sidecar"
    };
    let exe = resource_dir
        .join("sidecar")
        .join("openkb-api-sidecar")
        .join(exe_name);
    Command::new(exe)
        .args(["--host", HOST, "--port", &PORT.to_string()])
        .spawn()
}

/// Poll `url` until it answers or `timeout` elapses.
fn wait_until_ready(url: &str, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if ureq::get(url)
            .timeout(Duration::from_millis(500))
            .call()
            .is_ok()
        {
            return true;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    false
}

fn main() {
    tauri::Builder::default()
        .manage(Sidecar(Mutex::new(None)))
        .setup(|app| {
            let child = spawn_sidecar(app)?;
            app.state::<Sidecar>().0.lock().unwrap().replace(child);

            let url = format!("http://{HOST}:{PORT}/");
            let handle = app.handle().clone();
            // Poll off the main thread so the splash window can paint.
            std::thread::spawn(move || {
                if wait_until_ready(&url, Duration::from_secs(30)) {
                    if let (Some(win), Ok(parsed)) =
                        (handle.get_webview_window("main"), Url::parse(&url))
                    {
                        let _ = win.navigate(parsed);
                    }
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri app")
        .run(|app_handle, event| {
            if let RunEvent::Exit = event {
                if let Some(mut child) = app_handle.state::<Sidecar>().0.lock().unwrap().take() {
                    let _ = child.kill();
                }
            }
        });
}
