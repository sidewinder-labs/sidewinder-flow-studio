//! Sidewinder Flow Studio desktop shell.
//!
//! Starts the bundled backend (a frozen FastAPI app, see app/desktop.py) with the
//! OS app-data directory, waits for its `SFS_READY port=<n>` line, then points the
//! window at http://127.0.0.1:<n>/. The backend serves both the frontend and the
//! API, so the page is same-origin with its API and WebSocket. When the app
//! exits, the backend's stdin is closed (it shuts down cleanly) and, failing
//! that, it is killed. Updates are in update.rs.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, RunEvent, Url};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};

mod update;

const READY_PREFIX: &str = "SFS_READY port=";
const READY_TIMEOUT: Duration = Duration::from_secs(90);
const LOG_ROTATE_BYTES: u64 = 5 * 1024 * 1024;

struct Backend(Mutex<Option<Child>>);

/// Set while the app is stopping the backend on purpose (exit, update), so its
/// end isn't reported as a crash.
static STOPPING: AtomicBool = AtomicBool::new(false);

/// Keep one previous log: <name>.1, replaced once the current one passes 5 MB.
fn rotate(path: &Path) {
    if fs::metadata(path).map(|m| m.len() > LOG_ROTATE_BYTES).unwrap_or(false) {
        let mut old = path.as_os_str().to_owned();
        old.push(".1");
        let _ = fs::rename(path, old);
    }
}

/// logs/shell.log: the shell's own problems (panics, a backend that died).
fn shell_log(app: &AppHandle, message: &str) {
    let Ok(dir) = app.path().app_data_dir() else { return };
    let path = dir.join("logs").join("shell.log");
    let _ = fs::create_dir_all(dir.join("logs"));
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(f, "{} {message}", utc_now());
    }
}

/// "2026-10-31 18:37:43 UTC", without a date crate (civil-from-days, H. Hinnant).
fn utc_now() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let (days, rem) = (secs.div_euclid(86_400), secs.rem_euclid(86_400));
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!("{year:04}-{month:02}-{day:02} {:02}:{:02}:{:02} UTC", rem / 3600, rem % 3600 / 60, rem % 60)
}

/// The backend ended while the app was in use: say so, offer a restart.
fn backend_died(app: &AppHandle, log_path: &Path) {
    let status = app
        .state::<Backend>()
        .0
        .lock()
        .unwrap()
        .as_mut()
        .and_then(|c| c.wait().ok())
        .map(|s| s.to_string())
        .unwrap_or_else(|| "unknown status".into());
    shell_log(app, &format!("backend stopped unexpectedly ({status})"));
    let app = app.clone();
    let message = format!(
        "The Flow Studio engine stopped unexpectedly ({status}).\n\n\
         Your saved projects are safe. Unsaved changes since the last save may be lost.\n\n\
         Details are in {}",
        log_path.display());
    app.dialog()
        .message(message)
        .title("Flow Studio stopped")
        .kind(MessageDialogKind::Error)
        .buttons(MessageDialogButtons::OkCancelCustom("Restart".into(), "Close".into()))
        .show(move |restart| {
            if restart {
                app.restart();
            } else {
                app.exit(1);
            }
        });
}

/// The bundled backend executable, or $SFS_BACKEND when set (development).
fn backend_executable(app: &AppHandle) -> tauri::Result<PathBuf> {
    if let Ok(path) = std::env::var("SFS_BACKEND") {
        return Ok(PathBuf::from(path));
    }
    let name = if cfg!(windows) { "sfs-backend.exe" } else { "sfs-backend" };
    Ok(app.path().resource_dir()?.join("sfs-backend").join(name))
}

/// The last `max` bytes of a log file, for the error window.
fn log_tail(path: &Path, max: u64) -> String {
    let Ok(mut f) = File::open(path) else { return String::new() };
    let len = f.metadata().map(|m| m.len()).unwrap_or(0);
    let _ = f.seek(SeekFrom::Start(len.saturating_sub(max)));
    let mut s = String::new();
    let _ = f.read_to_string(&mut s);
    s
}

fn show_error(app: &AppHandle, message: &str) {
    if let Some(window) = app.get_webview_window("main") {
        let js = format!("window.sfsShowError({})", serde_json::to_string(message).unwrap_or_default());
        let _ = window.eval(&js);
    }
}

fn start_backend(app: &AppHandle) -> Result<(), String> {
    let data_dir = app.path().app_data_dir().map_err(|e| e.to_string())?;
    let log_dir = data_dir.join("logs");
    fs::create_dir_all(&log_dir).map_err(|e| format!("Cannot create {}: {e}", log_dir.display()))?;
    let log_path = log_dir.join("backend.log");
    rotate(&log_path);
    rotate(&log_dir.join("shell.log"));
    let log = OpenOptions::new().create(true).append(true).open(&log_path).map_err(|e| e.to_string())?;
    let exe = backend_executable(app).map_err(|e| e.to_string())?;

    let mut command = Command::new(&exe);
    command
        .arg("--data-dir")
        .arg(&data_dir)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::from(log.try_clone().map_err(|e| e.to_string())?));
    // `--safe-mode` on the app's command line: start with no plugins (e.g. a
    // shortcut "Sidewinder Flow Studio --safe-mode" when a plugin misbehaves).
    if std::env::args().any(|a| a == "--safe-mode") {
        command.env("SFS_SAFE_MODE", "1");
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW: no console window
    }
    let mut child = command
        .spawn()
        .map_err(|e| format!("Could not start the backend ({}): {e}", exe.display()))?;
    let stdout = child.stdout.take().expect("piped stdout");
    app.state::<Backend>().0.lock().unwrap().replace(child);

    let ready = Arc::new(AtomicBool::new(false));

    // Read the backend's stdout: the ready line, then anything else into the log.
    {
        let app = app.clone();
        let ready = ready.clone();
        let log_path = log_path.clone();
        let mut log = log;
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if let Some(port) = line.strip_prefix(READY_PREFIX) {
                    if let (Ok(port), Some(window)) = (port.trim().parse::<u16>(), app.get_webview_window("main")) {
                        ready.store(true, Ordering::SeqCst);
                        let url = Url::parse(&format!("http://127.0.0.1:{port}/")).expect("valid url");
                        let _ = window.navigate(url);
                    }
                } else {
                    let _ = writeln!(log, "{line}");
                }
            }
            if !ready.load(Ordering::SeqCst) {
                shell_log(&app, "backend stopped before it was ready");
                show_error(&app, &format!(
                    "The backend stopped before it was ready.\n\nLog: {}\n\n{}",
                    log_path.display(), log_tail(&log_path, 3000)));
            } else if !STOPPING.load(Ordering::SeqCst) {
                backend_died(&app, &log_path);
            }
        });
    }

    // Give up waiting after READY_TIMEOUT.
    {
        let app = app.clone();
        std::thread::spawn(move || {
            let start = Instant::now();
            while start.elapsed() < READY_TIMEOUT {
                if ready.load(Ordering::SeqCst) {
                    return;
                }
                std::thread::sleep(Duration::from_millis(200));
            }
            shell_log(&app, "backend did not start in time");
            show_error(&app, &format!(
                "The backend did not start within {} seconds.\n\nLog: {}",
                READY_TIMEOUT.as_secs(), log_path.display()));
        });
    }
    Ok(())
}

/// Close the backend's stdin (it exits on EOF); kill it if it hasn't after 5 s.
pub(crate) fn stop_backend(app: &AppHandle) {
    STOPPING.store(true, Ordering::SeqCst);
    let Some(mut child) = app.state::<Backend>().0.lock().unwrap().take() else { return };
    drop(child.stdin.take());
    let start = Instant::now();
    while start.elapsed() < Duration::from_secs(5) {
        if let Ok(Some(_)) = child.try_wait() {
            return;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    let _ = child.kill();
    let _ = child.wait();
}

/// The log directory, for the panic hook (which has no AppHandle).
static LOG_DIR: std::sync::OnceLock<PathBuf> = std::sync::OnceLock::new();

fn panic_log_dir() -> Option<PathBuf> {
    LOG_DIR.get().cloned()
}

fn main() {
    // WebKitGTK's DMA-BUF renderer leaves regions unpainted on some GPUs
    // (seen with NVIDIA: Graphic Walker panels and chart text). Must be set
    // before WebKit starts; a user's own setting wins.
    #[cfg(target_os = "linux")]
    if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
    }

    // Panics go to logs/shell.log as well as stderr (nobody sees stderr).
    let default_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        if let Some(dir) = panic_log_dir() {
            if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(dir.join("shell.log")) {
                let _ = writeln!(f, "panic: {info}");
            }
        }
        default_hook(info);
    }));

    tauri::Builder::default()
        // A second launch focuses the running window instead of starting again.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(Backend(Mutex::new(None)))
        .setup(|app| {
            if let Ok(dir) = app.path().app_data_dir() {
                let _ = LOG_DIR.set(dir.join("logs"));
            }
            if let Err(message) = start_backend(app.handle()) {
                show_error(app.handle(), &message);
            }
            if update::enabled() {
                update::check_in_background(app.handle().clone());
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the Tauri application")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                stop_backend(app);
            }
        });
}
