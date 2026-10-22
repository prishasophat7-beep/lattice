//! Tauri shell for the Lattice line assistant.
//!
//! On launch it starts hidden child processes on free localhost ports, each with a fresh random
//! key, and tells the web UI how to reach the engine:
//!
//!   llama-server   the local language model (llama.cpp), only if a model file is installed
//!   llama-server   a second one for search by meaning (--embeddings), only if an embedding
//!                  model is installed; works offline, so line PCs can use it too
//!   engine         the Python backend (app/backend), pointed at both
//!
//! The app puts itself in a Windows Job Object that kills every process in it when the app's
//! last handle closes, so no child can outlive the app, even after a crash.
//! Logs go to backend.log, llm.log and embed.log in the app's data folder.
//!
//! Where things are found (development overrides first):
//!   engine          OASIS_BACKEND_PYTHON (venv python.exe, runs `-m oasis_engine`)
//!                   else <resources>/runtime/backend/lattice-backend.exe
//!   llama-server    OASIS_LLAMA_SERVER, else <resources>/runtime/llama/llama-server.exe
//!   model           OASIS_MODEL_PATH, else the .gguf file in <data>/models (see import_model)
//!   embedding model OASIS_EMBED_MODEL_PATH, else the .gguf file in <data>/embed-models
//! The language model's server is not started when Manage has set a model server on the network
//! instead (connection.kind "remote" in <data>/config.json, which the engine writes); the embedding
//! server is not started when Manage has set an embedding server (Office profile).
//! Other OASIS_* variables (e.g. OASIS_LLM_MODEL=demo) pass through to the engine unchanged.

use std::fs;
use std::io::{BufReader, Read};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use serde::Serialize;
use tauri::{AppHandle, Manager, RunEvent};

const LLM_CONTEXT: &str = "8192";
const LLM_CACHE_RAM_MIB: &str = "512";
/// Embedding models read at most 2048 tokens; a section is embedded in one batch.
const EMBED_CONTEXT: &str = "2048";
/// Times per session a model server is restarted after it dies on its own (e.g. out of memory).
const MAX_RESTARTS: u32 = 3;

#[derive(Clone, Serialize)]
struct BackendInfo {
    url: String,
    token: String,
}

#[derive(Clone, Serialize)]
struct ModelInfo {
    /// File name of the installed model, if any
    name: Option<String>,
    /// Size of the installed model file in bytes, if any
    size: Option<u64>,
    /// llama-server process is alive
    running: bool,
    /// Why the model server is not running, if it failed to start
    error: Option<String>,
}

/// The two kinds of local model, each served by its own llama-server.
#[derive(Clone, Copy, PartialEq)]
enum Kind {
    Language,
    Embedding,
}

impl Kind {
    fn dir(self, data_dir: &Path) -> PathBuf {
        data_dir.join(match self {
            Kind::Language => "models",
            Kind::Embedding => "embed-models",
        })
    }
    fn path_override(self) -> &'static str {
        match self {
            Kind::Language => "OASIS_MODEL_PATH",
            Kind::Embedding => "OASIS_EMBED_MODEL_PATH",
        }
    }
    fn log(self) -> &'static str {
        match self {
            Kind::Language => "llm.log",
            Kind::Embedding => "embed.log",
        }
    }
}

/// One llama-server child process and what is known about it.
struct Server {
    port: u16,
    key: String,
    child: Mutex<Option<Child>>,
    model: Mutex<Option<PathBuf>>,
    error: Mutex<Option<String>>,
    restarts: Mutex<u32>,
    /// Held while the server is stopped, started or its model replaced: a status poll that comes
    /// in between must not take the stopped server for a crashed one and start a second copy.
    ops: Mutex<()>,
}

impl Server {
    fn new() -> Self {
        Server {
            port: free_port(),
            key: new_key(),
            child: Mutex::new(None),
            model: Mutex::new(None),
            error: Mutex::new(None),
            restarts: Mutex::new(0),
            ops: Mutex::new(()),
        }
    }
}

struct Services {
    backend: BackendInfo,
    data_dir: PathBuf,
    llm: Server,
    embed: Server,
    backend_child: Mutex<Option<Child>>,
    backend_port: u16,
    /// The app is closing: an engine that stops now is not restarted
    exiting: AtomicBool,
    #[cfg(windows)]
    _job: Option<job::Job>,
}

impl Services {
    fn server(&self, kind: Kind) -> &Server {
        match kind {
            Kind::Language => &self.llm,
            Kind::Embedding => &self.embed,
        }
    }
}

#[cfg(windows)]
mod job {
    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    use windows_sys::Win32::System::Threading::GetCurrentProcess;

    /// A job with kill-on-close that this app process joins. Children started afterwards are in
    /// the job from their first instruction (no race), including grandchildren such as the real
    /// python.exe behind a venv launcher. When the app exits or crashes, Windows kills them all.
    pub struct Job(HANDLE);
    // The handle is only used at creation and closed on drop; it is safe to share.
    unsafe impl Send for Job {}
    unsafe impl Sync for Job {}

    impl Job {
        pub fn contain_this_process() -> Option<Job> {
            unsafe {
                let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
                if handle.is_null() {
                    return None;
                }
                let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                let set = SetInformationJobObject(
                    handle,
                    JobObjectExtendedLimitInformation,
                    &info as *const _ as *const core::ffi::c_void,
                    std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                );
                if set == 0 || AssignProcessToJobObject(handle, GetCurrentProcess()) == 0 {
                    CloseHandle(handle);
                    return None;
                }
                Some(Job(handle))
            }
        }
    }

    impl Drop for Job {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
}

/// One Lattice per user: a second launch would start a second engine and model servers (about a
/// gigabyte more) on the same data folder. It brings the running window forward instead and exits.
#[cfg(windows)]
mod single_instance {
    use windows_sys::Win32::Foundation::{GetLastError, ERROR_ALREADY_EXISTS};
    use windows_sys::Win32::System::Threading::CreateMutexW;
    use windows_sys::Win32::UI::WindowsAndMessaging::{FindWindowW, SetForegroundWindow, ShowWindow, SW_RESTORE};

    fn wide(s: &str) -> Vec<u16> {
        s.encode_utf16().chain(std::iter::once(0)).collect()
    }

    /// True when this is the first Lattice of this user. The mutex is held until the process ends.
    pub fn claim(window_title: &str) -> bool {
        unsafe {
            let name = wide("Local\\com.oasis.lineassistant.single-instance");
            let mutex = CreateMutexW(std::ptr::null(), 0, name.as_ptr());
            if mutex.is_null() || GetLastError() != ERROR_ALREADY_EXISTS {
                return true; // first one (or no mutex: better two windows than none)
            }
            let title = wide(window_title);
            let window = FindWindowW(std::ptr::null(), title.as_ptr());
            if !window.is_null() {
                ShowWindow(window, SW_RESTORE);
                SetForegroundWindow(window);
            }
            false
        }
    }
}

fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(0)
}

fn new_key() -> String {
    uuid::Uuid::new_v4().simple().to_string()
}

fn hide_window(cmd: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    #[cfg(not(windows))]
    let _ = cmd;
}

/// Send the child's output to a log file: a fresh one, or appended (to keep an earlier crash's log).
fn log_to(cmd: &mut Command, path: &Path, append: bool) -> std::io::Result<()> {
    if !append {
        keep_previous(path);
    }
    let log = fs::OpenOptions::new()
        .create(true)
        .write(true)
        .append(append)
        .truncate(!append)
        .open(path)?;
    cmd.stdin(Stdio::null()).stdout(log.try_clone()?).stderr(log);
    Ok(())
}

/// A fresh log replaces the last one; keep that as `<name>.previous.log`, so an error from before a
/// restart can still be read (one old copy, so the logs never grow without bound).
fn keep_previous(path: &Path) {
    if fs::metadata(path).map(|m| m.len() > 0).unwrap_or(false) {
        let previous = path.with_extension("previous.log");
        let _ = fs::remove_file(&previous);
        let _ = fs::rename(path, &previous);
    }
}

fn runtime_path(app: &AppHandle, parts: &[&str]) -> Option<PathBuf> {
    let mut p = app.path().resource_dir().ok()?.join("runtime");
    for part in parts {
        p = p.join(part);
    }
    p.exists().then_some(p)
}

fn installed_model(kind: Kind, data_dir: &Path) -> Option<PathBuf> {
    if let Ok(p) = std::env::var(kind.path_override()) {
        return Some(PathBuf::from(p));
    }
    fs::read_dir(kind.dir(data_dir))
        .ok()?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().is_some_and(|x| x.eq_ignore_ascii_case("gguf")))
        .max_by_key(|p| fs::metadata(p).and_then(|m| m.modified()).ok())
}

fn read_config(data_dir: &Path) -> Option<serde_json::Value> {
    let text = fs::read_to_string(data_dir.join("config.json")).ok()?;
    serde_json::from_str(&text).ok()
}

/// Manage set a model server on the network: the built-in one is not needed (it would only use memory).
fn uses_remote_model(data_dir: &Path) -> bool {
    read_config(data_dir)
        .and_then(|c| c.get("connection")?.get("kind")?.as_str().map(|kind| kind == "remote"))
        .unwrap_or(false)
}

/// Manage set an embedding server on the network (Office profile): the engine uses that one.
fn uses_remote_embeddings(data_dir: &Path) -> bool {
    read_config(data_dir)
        .map(|c| {
            let office = c.get("profile").and_then(|p| p.as_str()) == Some("office");
            let url = c.get("embeddings").and_then(|e| e.get("base_url")).and_then(|u| u.as_str());
            office && url.is_some_and(|u| !u.is_empty())
        })
        .unwrap_or(false)
}

fn llama_server(app: &AppHandle) -> Option<PathBuf> {
    std::env::var("OASIS_LLAMA_SERVER")
        .ok()
        .map(PathBuf::from)
        .or_else(|| runtime_path(app, &["llama", "llama-server.exe"]))
}

/// Start the engine; `append_log` keeps the log of the run before (a restart after a crash).
fn spawn_engine(app: &AppHandle, s: &Services, append_log: bool) -> Result<Child, String> {
    let mut cmd = engine_command(app);
    cmd.env("OASIS_PORT", s.backend_port.to_string())
        .env("OASIS_API_TOKEN", &s.backend.token)
        .env("OASIS_DATA_DIR", &s.data_dir)
        .env("OASIS_LOG_LEVEL", "warning")
        // the engine also exits by itself if this app disappears, see watchdog.py
        .env("OASIS_PARENT_PID", std::process::id().to_string())
        .env("OASIS_LLM_BASE_URL", format!("http://127.0.0.1:{}/v1", s.llm.port))
        .env("OASIS_LLM_API_KEY", &s.llm.key)
        .env("OASIS_OUTPUT_MODE", "native")
        // always passed: the engine asks this server which model it serves, if any, so an
        // embedding model imported later is picked up without restarting the engine
        .env("OASIS_EMBED_BASE_URL", format!("http://127.0.0.1:{}/v1", s.embed.port))
        .env("OASIS_EMBED_API_KEY", &s.embed.key);
    if std::env::var("OASIS_LLM_MODEL").is_err() {
        cmd.env("OASIS_LLM_MODEL", "local");
    }
    log_to(&mut cmd, &s.data_dir.join("backend.log"), append_log).map_err(|e| e.to_string())?;
    hide_window(&mut cmd);
    cmd.spawn().map_err(|e| format!("Could not start the assistant engine: {e}"))
}

/// An engine that stops on its own (a crash, out of memory) is started again on the same port and
/// key, a few times per session; until then every question said "not responding" until the app
/// was restarted. The UI shows the engine is restarting meanwhile.
fn watch_engine(app: AppHandle) {
    let mut restarts = 0;
    loop {
        std::thread::sleep(Duration::from_secs(2));
        let Some(s) = app.try_state::<Services>() else { continue };
        if s.exiting.load(Ordering::SeqCst) {
            return;
        }
        let stopped = {
            let mut child = s.backend_child.lock().unwrap();
            match child.as_mut().map(|c| c.try_wait()) {
                Some(Ok(Some(status))) => Some(status),
                _ => None,
            }
        };
        let Some(status) = stopped else { continue };
        if restarts >= MAX_RESTARTS {
            return; // the UI keeps saying the engine stopped; backend.log says why
        }
        restarts += 1;
        let _ = fs::OpenOptions::new().append(true).open(s.data_dir.join("backend.log")).map(|mut f| {
            use std::io::Write;
            writeln!(f, "\n[Lattice] the engine stopped ({status}); restart {restarts} of {MAX_RESTARTS}")
        });
        if s.exiting.load(Ordering::SeqCst) {
            return;
        }
        let started = spawn_engine(&app, &s, true).ok();
        *s.backend_child.lock().unwrap() = started;
    }
}

fn engine_command(app: &AppHandle) -> Command {
    if let Ok(python) = std::env::var("OASIS_BACKEND_PYTHON") {
        let mut cmd = Command::new(python);
        cmd.args(["-m", "oasis_engine"]);
        return cmd;
    }
    let exe = runtime_path(app, &["backend", "lattice-backend.exe"])
        .unwrap_or_else(|| PathBuf::from("lattice-backend.exe"));
    Command::new(exe)
}

// ── GGUF metadata: is a file an embedding model? ──

fn read_u32(r: &mut impl Read) -> std::io::Result<u32> {
    let mut b = [0u8; 4];
    r.read_exact(&mut b)?;
    Ok(u32::from_le_bytes(b))
}

fn read_u64(r: &mut impl Read) -> std::io::Result<u64> {
    let mut b = [0u8; 8];
    r.read_exact(&mut b)?;
    Ok(u64::from_le_bytes(b))
}

fn read_string(r: &mut impl Read) -> std::io::Result<String> {
    let n = read_u64(r)?;
    if n > 1 << 24 {
        return Err(std::io::Error::other("implausible string length"));
    }
    let mut b = vec![0u8; n as usize];
    r.read_exact(&mut b)?;
    Ok(String::from_utf8_lossy(&b).into_owned())
}

/// Skip one metadata value of GGUF type `t`.
fn skip_value(r: &mut impl Read, t: u32) -> std::io::Result<()> {
    let size = match t {
        0 | 1 | 7 => 1, // u8, i8, bool
        2 | 3 => 2,     // u16, i16
        4..=6 => 4,     // u32, i32, f32
        10..=12 => 8,   // u64, i64, f64
        8 => return read_string(r).map(|_| ()),
        9 => {
            let item = read_u32(r)?;
            let count = read_u64(r)?;
            for _ in 0..count {
                skip_value(r, item)?;
            }
            return Ok(());
        }
        _ => return Err(std::io::Error::other("unknown GGUF value type")),
    };
    std::io::copy(&mut r.take(size), &mut std::io::sink())?;
    Ok(())
}

/// An embedding model's metadata says how to pool token vectors into one ("<arch>.pooling_type");
/// language models have no such key. Checked on Qwen3.5, EmbeddingGemma and nomic-embed files.
fn is_embedding_model(path: &Path) -> Result<bool, String> {
    let read = || -> std::io::Result<bool> {
        let mut r = BufReader::new(fs::File::open(path)?);
        let mut magic = [0u8; 4];
        r.read_exact(&mut magic)?;
        if &magic != b"GGUF" {
            return Err(std::io::Error::other("not GGUF"));
        }
        let _version = read_u32(&mut r)?;
        let _tensors = read_u64(&mut r)?;
        let kvs = read_u64(&mut r)?;
        for _ in 0..kvs {
            let key = read_string(&mut r)?;
            let t = read_u32(&mut r)?;
            if key.ends_with(".pooling_type") {
                return Ok(true);
            }
            skip_value(&mut r, t)?;
        }
        Ok(false)
    };
    read().map_err(|e| format!("This is not a GGUF model file ({e})."))
}

// ── model servers ──

/// (Re)start the llama-server of this kind with its installed model. Records the reason if it
/// cannot start. `append_log` keeps the previous log (used when restarting after a crash).
fn start_server(app: &AppHandle, s: &Services, kind: Kind, append_log: bool) {
    let _busy = s.server(kind).ops.lock().unwrap();
    spawn_server(app, s, kind, append_log);
}

/// start_server, for a caller that already holds the server's `ops` lock.
fn spawn_server(app: &AppHandle, s: &Services, kind: Kind, append_log: bool) {
    let srv = s.server(kind);
    stop(&srv.child);
    let model = installed_model(kind, &s.data_dir);
    *srv.model.lock().unwrap() = model.clone();
    let Some(model) = model else {
        // no embedding model is fine: keyword search only
        *srv.error.lock().unwrap() = (kind == Kind::Language).then(|| "No language model installed yet.".into());
        return;
    };
    let result = (|| -> Result<Child, String> {
        let server = llama_server(app).ok_or("The model server (llama-server.exe) is missing from this installation.")?;
        let mut cmd = Command::new(&server);
        cmd.arg("-m")
            .arg(&model)
            .args(["--host", "127.0.0.1", "--port", &srv.port.to_string()])
            .args(["--api-key", &srv.key])
            .args(["-np", "1", "--no-webui"]);
        match kind {
            Kind::Language => {
                cmd.args(["-c", LLM_CONTEXT, "--cache-ram", LLM_CACHE_RAM_MIB])
                    // No chain-of-thought: on a CPU it multiplies answer time, and it breaks the engine's
                    // JSON replies. Qwen3.5 and Granite 4.2 both think by default in llama.cpp.
                    .args(["--reasoning", "off"]);
            }
            Kind::Embedding => {
                // a whole section in one batch: embedding models need the input in one ubatch
                cmd.args(["--embeddings", "-c", EMBED_CONTEXT, "-b", EMBED_CONTEXT, "-ub", EMBED_CONTEXT]);
            }
        }
        if let Some(dir) = server.parent() {
            cmd.current_dir(dir); // the ggml DLLs live next to llama-server.exe
        }
        log_to(&mut cmd, &s.data_dir.join(kind.log()), append_log).map_err(|e| e.to_string())?;
        hide_window(&mut cmd);
        cmd.spawn().map_err(|e| format!("Could not start the model server: {e}"))
    })();
    match result {
        Ok(child) => {
            *srv.child.lock().unwrap() = Some(child);
            *srv.error.lock().unwrap() = None;
        }
        Err(e) => *srv.error.lock().unwrap() = Some(e),
    }
}

fn stop(slot: &Mutex<Option<Child>>) {
    if let Some(mut child) = slot.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn info_of(srv: &Server, kind: Kind) -> ModelInfo {
    let running = srv
        .child
        .lock()
        .unwrap()
        .as_mut()
        .is_some_and(|c| matches!(c.try_wait(), Ok(None)));
    let model = srv.model.lock().unwrap().clone();
    let name = model
        .as_ref()
        .and_then(|p| p.file_name())
        .map(|n| n.to_string_lossy().into_owned());
    let size = model.as_ref().and_then(|p| std::fs::metadata(p).ok()).map(|m| m.len());
    let mut error = srv.error.lock().unwrap().clone();
    if error.is_none() && name.is_some() && !running {
        error = Some(format!("The model server stopped. See {} in the app's data folder.", kind.log()));
    }
    ModelInfo { name, size, running, error }
}

/// What the UI polls. A model server that died on its own is restarted a few times before the
/// failure is reported, so an unattended line PC recovers without anyone opening Manage.
fn checked_info(app: &AppHandle, kind: Kind) -> ModelInfo {
    let s = app.state::<Services>();
    let srv = s.server(kind);
    // being stopped, started or replaced right now: report it, never restart it from here
    let Ok(_busy) = srv.ops.try_lock() else {
        return ModelInfo { error: None, ..info_of(srv, kind) };
    };
    let info = info_of(srv, kind);
    let crashed = info.name.is_some() && !info.running && srv.error.lock().unwrap().is_none();
    if crashed {
        let mut restarts = srv.restarts.lock().unwrap();
        if *restarts < MAX_RESTARTS {
            *restarts += 1;
            drop(restarts);
            spawn_server(app, &s, kind, true);
            return info_of(srv, kind);
        }
    }
    info
}

/// Copy a .gguf model into the app's data folder, replacing the previous one of its kind, and
/// restart that model server with it. A language model is refused as the embedding model and
/// the other way round: each would load, but answer nonsense.
fn import(app: &AppHandle, kind: Kind, path: &str) -> Result<ModelInfo, String> {
    let src = PathBuf::from(path);
    let embedding = is_embedding_model(&src)?;
    match (kind, embedding) {
        (Kind::Language, true) => {
            return Err("This is an embedding model (for search by meaning), not a language model. \
                        Import it under \"Search by meaning\"."
                .into())
        }
        (Kind::Embedding, false) => {
            return Err("This is a language model, not an embedding model. \
                        Import it with \"Import model file...\"."
                .into())
        }
        _ => {}
    }
    let s = app.state::<Services>();
    let srv = s.server(kind);
    let dir = kind.dir(&s.data_dir);
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let name = src.file_name().ok_or("The file has no name.")?.to_owned();
    let dest = dir.join(&name);
    let partial = dir.join(format!("{}.part", name.to_string_lossy()));
    fs::copy(&src, &partial).map_err(|e| format!("Copying the model failed: {e}"))?;
    // a copy keeps the source's date, and the newest file is the one used (installed_model): if
    // the old file could not be deleted, an older download would lose to it
    if let Ok(f) = fs::File::options().write(true).open(&partial) {
        let _ = f.set_modified(std::time::SystemTime::now());
    }

    let _busy = srv.ops.lock().unwrap();
    stop(&srv.child); // release the old model file before replacing it
    remove_models(&dir, Some(&partial));
    fs::rename(&partial, &dest).map_err(|e| e.to_string())?;
    *srv.restarts.lock().unwrap() = 0;
    spawn_server(app, &s, kind, false);
    Ok(info_of(srv, kind))
}

/// Delete the .gguf files in a models folder, except `keep`. Windows may hold a file open for a
/// moment after the server using it exits, so each is tried for up to two seconds.
fn remove_models(dir: &Path, keep: Option<&Path>) {
    if let Ok(entries) = fs::read_dir(dir) {
        for p in entries.filter_map(|e| e.ok().map(|e| e.path())) {
            if Some(p.as_path()) != keep && p.extension().is_some_and(|x| x.eq_ignore_ascii_case("gguf")) {
                for _ in 0..10 {
                    if fs::remove_file(&p).is_ok() || !p.exists() {
                        break;
                    }
                    std::thread::sleep(std::time::Duration::from_millis(200));
                }
            }
        }
    }
}

// ── commands ──

/// Start or stop the built-in model server, when Manage switches between it and a remote model.
#[tauri::command]
fn set_model_enabled(app: AppHandle, enabled: bool) -> ModelInfo {
    let s = app.state::<Services>();
    let _busy = s.llm.ops.lock().unwrap();
    if enabled {
        *s.llm.restarts.lock().unwrap() = 0;
        spawn_server(&app, &s, Kind::Language, false);
    } else {
        stop(&s.llm.child);
        *s.llm.model.lock().unwrap() = None; // not "crashed": nothing to restart
        *s.llm.error.lock().unwrap() = None;
    }
    info_of(&s.llm, Kind::Language)
}

#[tauri::command]
fn backend_info(s: tauri::State<'_, Services>) -> BackendInfo {
    s.backend.clone()
}

#[tauri::command]
fn model_info(app: AppHandle) -> ModelInfo {
    checked_info(&app, Kind::Language)
}

#[tauri::command]
fn embed_model_info(app: AppHandle) -> ModelInfo {
    checked_info(&app, Kind::Embedding)
}

#[tauri::command]
async fn import_model(app: AppHandle, path: String) -> Result<ModelInfo, String> {
    import(&app, Kind::Language, &path)
}

#[tauri::command]
async fn import_embed_model(app: AppHandle, path: String) -> Result<ModelInfo, String> {
    import(&app, Kind::Embedding, &path)
}

/// Turn search by meaning off on this computer: stop its server and delete the model file.
#[tauri::command]
fn remove_embed_model(app: AppHandle) -> ModelInfo {
    let s = app.state::<Services>();
    let _busy = s.embed.ops.lock().unwrap();
    stop(&s.embed.child);
    remove_models(&Kind::Embedding.dir(&s.data_dir), None);
    *s.embed.model.lock().unwrap() = None;
    *s.embed.error.lock().unwrap() = None;
    info_of(&s.embed, Kind::Embedding)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn gguf(keys: &[(&str, u32, Vec<u8>)]) -> Vec<u8> {
        let mut out = b"GGUF".to_vec();
        out.extend(3u32.to_le_bytes());
        out.extend(0u64.to_le_bytes());
        out.extend((keys.len() as u64).to_le_bytes());
        for (key, t, value) in keys {
            out.extend((key.len() as u64).to_le_bytes());
            out.extend(key.as_bytes());
            out.extend(t.to_le_bytes());
            out.extend(value);
        }
        out
    }

    fn string(s: &str) -> Vec<u8> {
        let mut v = (s.len() as u64).to_le_bytes().to_vec();
        v.extend(s.as_bytes());
        v
    }

    fn check(name: &str, bytes: Vec<u8>) -> Result<bool, String> {
        let path = std::env::temp_dir().join(format!("lattice-test-{name}-{}.gguf", std::process::id()));
        fs::write(&path, bytes).unwrap();
        let result = is_embedding_model(&path);
        let _ = fs::remove_file(&path);
        result
    }

    #[test]
    fn tells_embedding_models_from_language_models() {
        let mut tokens = 8u32.to_le_bytes().to_vec(); // an array of two strings, like the vocabulary
        tokens.extend(2u64.to_le_bytes());
        tokens.extend(string("hello"));
        tokens.extend(string("world"));
        let chat = gguf(&[
            ("general.architecture", 8, string("qwen35")),
            ("tokenizer.ggml.tokens", 9, tokens.clone()),
            ("qwen35.context_length", 4, 8192u32.to_le_bytes().to_vec()),
        ]);
        let embed = gguf(&[
            ("general.architecture", 8, string("gemma-embedding")),
            ("tokenizer.ggml.tokens", 9, tokens),
            ("gemma-embedding.pooling_type", 4, 1u32.to_le_bytes().to_vec()),
        ]);
        assert_eq!(check("chat", chat), Ok(false));
        assert_eq!(check("embed", embed), Ok(true));
        assert!(check("junk", b"PK\x03\x04not a model".to_vec()).is_err());
    }

    /// Real model files: LATTICE_TEST_GGUF="path=true;path=false" cargo test -- --ignored
    #[test]
    #[ignore]
    fn real_model_files() {
        let spec = std::env::var("LATTICE_TEST_GGUF").unwrap_or_default();
        for item in spec.split(';').filter(|s| !s.is_empty()) {
            let (path, expected) = item.rsplit_once('=').unwrap();
            assert_eq!(is_embedding_model(Path::new(path)), Ok(expected == "true"), "{path}");
        }
    }
}

pub fn run() {
    #[cfg(windows)]
    if !single_instance::claim("Lattice") {
        return;
    }
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            #[cfg(windows)]
            let job = job::Job::contain_this_process();

            let data_dir = app.path().app_local_data_dir()?;
            fs::create_dir_all(&data_dir)?;
            let port = free_port();
            let services = Services {
                backend: BackendInfo {
                    url: format!("http://127.0.0.1:{port}"),
                    token: new_key(),
                },
                data_dir: data_dir.clone(),
                llm: Server::new(),
                embed: Server::new(),
                backend_child: Mutex::new(None),
                backend_port: port,
                exiting: AtomicBool::new(false),
                #[cfg(windows)]
                _job: job,
            };

            if !uses_remote_model(&data_dir) {
                start_server(app.handle(), &services, Kind::Language, false);
            }
            if !uses_remote_embeddings(&data_dir) {
                start_server(app.handle(), &services, Kind::Embedding, false);
            }

            let child = spawn_engine(app.handle(), &services, false)?;
            *services.backend_child.lock().unwrap() = Some(child);

            app.manage(services);
            let handle = app.handle().clone();
            std::thread::spawn(move || watch_engine(handle));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            backend_info,
            model_info,
            import_model,
            set_model_enabled,
            embed_model_info,
            import_embed_model,
            remove_embed_model
        ])
        .build(tauri::generate_context!())
        .expect("error while building the Lattice app")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(s) = app.try_state::<Services>() {
                    s.exiting.store(true, Ordering::SeqCst);
                    stop(&s.backend_child);
                    stop(&s.llm.child);
                    stop(&s.embed.child);
                }
            }
        });
}
