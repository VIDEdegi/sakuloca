#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use serde_json::{json, Value};
use std::{
    io::{BufRead, BufReader, Write},
    path::PathBuf,
    process::{Child, ChildStdin, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Arc, Mutex,
    },
    time::Duration,
};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{Manager, State};
use tauri_plugin_dialog::DialogExt;
mod updates;
mod automation;

struct Worker {
    data: PathBuf,
    child: Child,
    stdin: ChildStdin,
    replies: mpsc::Receiver<String>,
    next_id: u64,
    _job: win32job::Job,
}
impl Worker {
    fn spawn(app: &tauri::AppHandle, data: &PathBuf) -> Result<Self, String> {
        #[cfg(debug_assertions)]
        let mut command = {
            let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap()
                .to_path_buf();
            let mut cmd = Command::new(root.join("tools/python/Scripts/python.exe"));
            cmd.arg(root.join("worker/main.py"));
            cmd
        };
        #[cfg(not(debug_assertions))]
        let mut command = Command::new(
            app.path()
                .resource_dir()
                .map_err(|e| e.to_string())?
                .join("worker/media-search-worker.exe"),
        );
        let _ = app;
        command
            .arg("--data")
            .arg(data)
            .env("TEMP", data.join("tmp"))
            .env("TMP", data.join("tmp"))
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let mut child = command
            .spawn()
            .map_err(|e| format!("解析Workerを起動できません: {e}"))?;
        use std::os::windows::io::AsRawHandle;
        let mut limits = win32job::ExtendedLimitInfo::new();
        limits.limit_kill_on_job_close();
        let job = match win32job::Job::create_with_limit_info(&limits) {
            Ok(job) => job,
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(format!("Worker終了管理を作成できません: {error}"));
            }
        };
        if let Err(error) = job.assign_process(child.as_raw_handle() as isize) {
            let _ = child.kill();
            let _ = child.wait();
            return Err(format!("Worker終了管理を設定できません: {error}"));
        }
        let stdin = child.stdin.take().ok_or("Worker stdin missing")?;
        let stdout = child.stdout.take().ok_or("Worker stdout missing")?;
        let stderr = child.stderr.take().ok_or("Worker stderr missing")?;
        let (sender, replies) = mpsc::channel();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                match line {
                    Ok(line) => {
                        if sender.send(line).is_err() {
                            break;
                        }
                    }
                    Err(_) => break,
                }
            }
        });
        let log_path = data.join("worker.log");
        // Keep the previous run's log so a crash that triggered a restart is
        // still diagnosable. Only the last two runs are retained.
        if log_path.exists() {
            let previous = data.join("worker.log.1");
            let _ = std::fs::remove_file(&previous);
            let _ = std::fs::rename(&log_path, &previous);
        }
        std::thread::spawn(move || {
            let mut written = 0usize;
            let mut log = std::fs::File::create(log_path).ok();
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                if written < 2 * 1024 * 1024 {
                    if let Some(f) = log.as_mut() {
                        let _ = writeln!(f, "{line}");
                        written += line.len() + 1;
                    }
                }
            }
        });
        let mut worker = Self {
            data: data.clone(),
            child,
            stdin,
            replies,
            next_id: 0,
            _job: job,
        };
        worker.call("status", json!({}))?;
        Ok(worker)
    }
    fn call(&mut self, method: &str, params: Value) -> Result<Value, String> {
        self.call_with_timeout(method, params, Duration::from_secs(120))
    }
    fn call_with_timeout(
        &mut self,
        method: &str,
        params: Value,
        timeout: Duration,
    ) -> Result<Value, String> {
        if self.child.try_wait().map_err(|e| e.to_string())?.is_some() {
            return Err("解析Workerが終了しました。アプリを再起動してください。保存済みデータは保持されています。".into());
        }
        self.next_id += 1;
        let id = self.next_id;
        let payload = json!({"version":1,"id":id,"method":method,"params":params});
        writeln!(self.stdin, "{payload}").map_err(|e| e.to_string())?;
        self.stdin.flush().map_err(|e| e.to_string())?;
        let line = match self.replies.recv_timeout(timeout) {
            Ok(line) => line,
            Err(error) => {
                let _ = self.child.kill();
                return Err(format!(
                    "Workerから応答がありません。アプリを再起動してください: {error}"
                ));
            }
        };
        let response: Value =
            serde_json::from_str(&line).map_err(|e| format!("Worker応答が不正です: {e}"))?;
        if response["id"] != id {
            return Err("Worker応答IDが不一致です".into());
        }
        if let Some(error) = response.get("error") {
            return Err(error.as_str().unwrap_or("Worker error").to_owned());
        }
        Ok(response["result"].clone())
    }
}
impl Drop for Worker {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}
type Broker = Arc<Mutex<Worker>>;

#[tauri::command]
async fn worker_request(
    method: String,
    params: Value,
    broker: State<'_, Broker>,
) -> Result<Value, String> {
    const METHODS: &[&str] = &[
        "status",
        "preferences",
        "environment",
        "diagnose",
        "model_info",
        "download_model",
        "cancel_download",
        "errors",
        "retry_errors",
        "clear_runtime_cache",
        "folders",
        "add_folder",
        "folder_action",
        "configure",
        "start",
        "pause",
        "search",
        "preview",
        "reference_preview",
        "clear_thumbnails",
    ];
    if !METHODS.contains(&method.as_str()) {
        return Err("許可されていない操作です".into());
    }
    let broker = broker.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        broker
            .lock()
            .map_err(|e| e.to_string())?
            .call(&method, params)
    })
    .await
    .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn choose_path(app: tauri::AppHandle, kind: String) -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let picker = app.dialog().file();
        let selection = match kind.as_str() {
            "folder" => picker
                .set_title("検索対象の写真・音声フォルダーを選択")
                .blocking_pick_folder(),
            "storage" => picker
                .set_title("保存先の移行先フォルダーを選択")
                .blocking_pick_folder(),
            "model_dir" => picker
                .set_title("モデルの保存先フォルダーを選択")
                .blocking_pick_folder(),
            "image" => picker
                .set_title("類似画像検索の参照画像")
                .add_filter("写真", &["jpg", "jpeg", "png", "webp"])
                .blocking_pick_file(),
            "model" => picker
                .set_title("既存のLiteRT-LMモデルを選択")
                .add_filter("LiteRT-LM", &["litertlm"])
                .blocking_pick_file(),
            _ => return Err("不正な選択種別です".to_owned()),
        };
        selection
            .map(|p| {
                p.into_path()
                    .map(|p| p.to_string_lossy().to_string())
                    .map_err(|e| e.to_string())
            })
            .transpose()
    })
    .await
    .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn open_media(id: u64, reveal: bool, broker: State<'_, Broker>) -> Result<(), String> {
    let broker = broker.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        let file = broker
            .lock()
            .map_err(|e| e.to_string())?
            .call("file", json!({"id":id}))?;
        let path = file["path"].as_str().ok_or("ファイルのパスがありません")?;
        let mut cmd = Command::new("explorer.exe");
        if reveal {
            cmd.arg(format!("/select,{path}"));
        } else {
            cmd.arg(path);
        }
        cmd.spawn().map_err(|e| e.to_string())?;
        Ok(())
    })
    .await
    .map_err(|e| e.to_string())?
}
struct Desktop {
    background: AtomicBool,
    quitting: AtomicBool,
    confirming: AtomicBool,
    running: AtomicBool,
}
struct TrayState {
    pause: MenuItem<tauri::Wry>,
    resume: MenuItem<tauri::Wry>,
}
fn show_window(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}
fn confirm_quit(app: &tauri::AppHandle) {
    let desktop = app.state::<Desktop>();
    if desktop.quitting.load(Ordering::SeqCst)
        || desktop.confirming.swap(true, Ordering::SeqCst)
    {
        return;
    }
    if desktop.running.load(Ordering::SeqCst) {
        let answer = rfd::MessageDialog::new()
            .set_title("SakuLocaを終了")
            .set_description("解析中です。処理中の写真または音声区間を保存して解析を停止し、終了します。")
            .set_level(rfd::MessageLevel::Warning)
            .set_buttons(rfd::MessageButtons::YesNo)
            .show();
        if answer != rfd::MessageDialogResult::Yes {
            desktop.confirming.store(false, Ordering::SeqCst);
            return;
        }
    }
    desktop.confirming.store(false, Ordering::SeqCst);
    quit_safely(app);
}
fn restart_worker(app: &tauri::AppHandle) -> Result<(), String> {
    let broker = app.state::<Broker>();
    let mut old = broker.lock().map_err(|e| e.to_string())?;
    let _ = old.child.kill();
    let _ = old.child.wait();
    let replacement = Worker::spawn(app, &old.data)?;
    *old = replacement;
    Ok(())
}
fn quit_safely(app: &tauri::AppHandle) {
    if app.state::<Desktop>().quitting.swap(true, Ordering::SeqCst) {
        return;
    }
    let handle = app.clone();
    std::thread::spawn(move || {
        if let Ok(mut worker) = handle.state::<Broker>().lock() {
            let _ = worker.call("shutdown", json!({}));
            let deadline = std::time::Instant::now() + Duration::from_secs(15);
            while std::time::Instant::now() < deadline {
                if worker.child.try_wait().ok().flatten().is_some() {
                    break;
                }
                std::thread::sleep(Duration::from_millis(100));
            }
            let _ = worker.child.kill();
            let _ = worker.child.wait();
        }
        handle.exit(0);
    });
}
#[tauri::command]
async fn desktop_action(app: tauri::AppHandle, action: String) -> Result<Value, String> {
    match action.as_str() {
        "hide" => {
            if !app.state::<Desktop>().background.load(Ordering::SeqCst) {
                return Err("設定でバックグラウンド実行を許可してください".into());
            }
            app.get_webview_window("main")
                .ok_or("ウィンドウがありません")?
                .hide()
                .map_err(|e| e.to_string())?;
        }
        "close" => {
            app.get_webview_window("main")
                .ok_or("ウィンドウがありません")?
                .close()
                .map_err(|e| e.to_string())?;
        }
        "show" => show_window(&app),
        "quit" => {
            let handle = app.clone();
            tauri::async_runtime::spawn_blocking(move || confirm_quit(&handle))
                .await
                .map_err(|e| e.to_string())?;
        }
        "restart_worker" => {
            let handle = app.clone();
            tauri::async_runtime::spawn_blocking(move || restart_worker(&handle))
                .await
                .map_err(|e| e.to_string())??;
        }
        "log" => {
            let data = app
                .state::<Broker>()
                .lock()
                .map_err(|e| e.to_string())?
                .data
                .clone();
            Command::new("notepad.exe")
                .arg(data.join("worker.log"))
                .spawn()
                .map_err(|e| e.to_string())?;
        }
        _ => return Err("不正な操作です".into()),
    }
    Ok(json!({"ok":true}))
}
const AUTOSTART_KEY: &str = "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run";
fn registry_run(
    operation: &str,
    name: &str,
    value: Option<&str>,
) -> Result<std::process::Output, String> {
    let mut command = Command::new("reg.exe");
    command.args([operation, AUTOSTART_KEY, "/v", name]);
    match value {
        Some(value) => {
            command.args(["/t", "REG_SZ", "/d", value, "/f"]);
        }
        None => {
            command.arg("/f");
        }
    }
    use std::os::windows::process::CommandExt;
    command.creation_flags(0x08000000);
    command.output().map_err(|e| e.to_string())
}
#[tauri::command]
async fn desktop_preferences(
    app: tauri::AppHandle,
    preferences: Value,
    autostart: Option<bool>,
    minimized: Option<bool>,
) -> Result<Value, String> {
    if let Some(enabled) = autostart {
        if std::env::var_os("MEDIA_SEARCH_DATA").is_some() {
            return Err("環境変数で保存先を指定している間は自動起動を変更できません。通常起動で設定してください".into());
        }
        let executable = std::env::current_exe().map_err(|e| e.to_string())?;
        // v1.0.0 registered the old product name. Remove it so the user never
        // gets two login entries after upgrading.
        let _ = registry_run("delete", "LocalPhotoSearch", None);
        if enabled {
            let target = format!(
                "\"{}\"{}",
                executable.display(),
                if minimized.unwrap_or(false) {
                    " --background"
                } else {
                    ""
                }
            );
            let result = registry_run("add", "SakuLoca", Some(&target))?;
            if !result.status.success() {
                return Err("Windows自動起動を登録できません。ユーザー権限を確認してください".into());
            }
        } else {
            let _ = registry_run("delete", "SakuLoca", None);
        }
    }
    let handle = app.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        handle
            .state::<Broker>()
            .lock()
            .map_err(|e| e.to_string())?
            .call("preferences", preferences)
    })
    .await
    .map_err(|e| e.to_string())??;
    app.state::<Desktop>().background.store(
        result["background"].as_bool().unwrap_or(false),
        Ordering::SeqCst,
    );
    Ok(result)
}
#[tauri::command]
async fn migrate_storage(app: tauri::AppHandle, destination: String) -> Result<Value, String> {
    if std::env::var_os("MEDIA_SEARCH_DATA").is_some() {
        return Err(
            "環境変数MEDIA_SEARCH_DATAが保存先を固定しています。通常起動で保存先を変更してください"
                .into(),
        );
    }
    let handle = app.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        let broker = handle.state::<Broker>();
        let mut worker = broker.lock().map_err(|e| e.to_string())?;
        // Verification hashes the model and every thumbnail. Large libraries
        // legitimately exceed the normal two-minute request timeout.
        let result = worker.call_with_timeout(
            "migrate",
            json!({"destination":destination}),
            Duration::from_secs(3600),
        )?;
        let source = worker.data.join("updates.json");
        if source.exists() {
            let bytes = std::fs::read(source).map_err(|e| e.to_string())?;
            let target = PathBuf::from(result["data_path"].as_str().ok_or("移行先が不明です")?).join("updates.json");
            std::fs::write(&target, &bytes).map_err(|e| format!("更新設定のコピーに失敗しました。元の保存先を維持します: {e}"))?;
            if std::fs::read(target).map_err(|e| e.to_string())? != bytes {
                return Err("更新設定のコピー確認に失敗しました。元の保存先を維持します".into());
            }
        }
        Ok::<Value, String>(result)
    })
    .await
    .map_err(|e| e.to_string())??;
    let pointer = storage_pointer()?;
    let temporary = pointer.with_extension("json.new");
    std::fs::write(
        &temporary,
        json!({"data_path":result["data_path"]}).to_string(),
    )
    .map_err(|e| e.to_string())?;
    std::fs::rename(temporary, pointer).map_err(|e| {
        format!("コピーは完了しましたが保存先設定に失敗しました。元データで継続します: {e}")
    })?;
    // Switching only at restart avoids open WebView2/SQLite handles being moved.
    quit_safely(&app);
    Ok(result)
}
#[tauri::command]
fn open_reference(kind: String) -> Result<(), String> {
    let url = match kind.as_str() {
        "model" => "https://huggingface.co/litert-community/embeddinggemma-2-740m-litert-lm",
        "license" => "https://huggingface.co/google/embeddinggemma-2",
        "releases" => "https://github.com/VIDEdegi/sakuloca/releases",
        "guide" => "https://blog.videdegi.com/2026/10/sakuloca.html",
        "webview" => "https://developer.microsoft.com/microsoft-edge/webview2/",
        _ => return Err("許可されていないリンクです".into()),
    };
    Command::new("explorer.exe")
        .arg(url)
        .spawn()
        .map_err(|e| e.to_string())?;
    Ok(())
}
fn storage_pointer() -> Result<PathBuf, String> {
    Ok(
        PathBuf::from(std::env::var_os("LOCALAPPDATA").ok_or("LOCALAPPDATA missing")?)
            .join("LocalPhotoSearch/storage.json"),
    )
}
fn data_directory() -> Result<PathBuf, String> {
    if let Some(path) = std::env::var_os("MEDIA_SEARCH_DATA") {
        return Ok(PathBuf::from(path));
    }
    // A single persistent pointer lets application updates preserve the same database.
    let pointer = PathBuf::from(std::env::var_os("LOCALAPPDATA").ok_or("LOCALAPPDATA missing")?)
        .join("LocalPhotoSearch/storage.json");
    if pointer.exists() {
        let value: Value =
            serde_json::from_slice(&std::fs::read(&pointer).map_err(|e| e.to_string())?)
                .map_err(|e| e.to_string())?;
        return Ok(PathBuf::from(
            value["data_path"].as_str().ok_or("Invalid data pointer")?,
        ));
    }
    let default = pointer.parent().unwrap().join("data");
    std::fs::create_dir_all(pointer.parent().unwrap()).map_err(|e| e.to_string())?;
    std::fs::write(pointer, json!({"data_path":default}).to_string()).map_err(|e| e.to_string())?;
    Ok(default)
}
fn main() {
    let data = match data_directory() {
        Ok(data) => data,
        Err(error) => {
            rfd::MessageDialog::new()
                .set_title("SakuLocaを起動できません")
                .set_description(error)
                .set_level(rfd::MessageLevel::Error)
                .show();
            return;
        }
    };
    let mut context = tauri::generate_context!();
    context.config_mut().app.app_directories_override = Some(
        tauri::utils::config::AppDirectoriesOverride::Root(data.clone()),
    );
    let application = tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(updates::Updates::default())
        .manage(Desktop {
            background: AtomicBool::new(false),
            quitting: AtomicBool::new(false),
            confirming: AtomicBool::new(false),
            running: AtomicBool::new(false),
        })
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .setup(move |app| {
            std::fs::create_dir_all(data.join("tmp"))?;
            let mut worker = Worker::spawn(app.handle(), &data).map_err(std::io::Error::other)?;
            let initial = worker
                .call("status", json!({}))
                .map_err(std::io::Error::other)?;
            if initial["resume_requested"] == true
                && initial["preferences"]["background"] == true
                && initial["preferences"]["resume_on_launch"] == true
            {
                let _ = worker.call("start", json!({}));
            }
            app.state::<Desktop>().background.store(
                initial["preferences"]["background"]
                    .as_bool()
                    .unwrap_or(false),
                Ordering::SeqCst,
            );
            app.state::<Desktop>()
                .running
                .store(initial["running"] == true, Ordering::SeqCst);
            app.manage(Arc::new(Mutex::new(worker)));
            // Automation shares this exact broker; it never opens another database/AI engine.
            automation::start(app.handle(), &data).map_err(std::io::Error::other)?;
            let config = &app.config().app.windows[0];
            let window = tauri::WebviewWindowBuilder::from_config(app.handle(), config)?
                .data_directory(data.join("webview"))
                .build()?;
            if std::env::args().any(|arg| arg == "--background")
                && initial["preferences"]["background"] == true
                && initial["preferences"]["setup_complete"] == true
            {
                let _ = window.hide();
            }
            let running = initial["running"] == true;
            let show = MenuItem::with_id(app, "show", "検索ウィンドウを表示", true, None::<&str>)?;
            let pause = MenuItem::with_id(app, "pause", "解析を一時停止", running, None::<&str>)?;
            let resume = MenuItem::with_id(app, "start", "差分解析 / 再開", !running, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "完全終了", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &pause, &resume, &quit])?;
            app.manage(TrayState {
                pause: pause.clone(),
                resume: resume.clone(),
            });
            TrayIconBuilder::with_id("main-tray")
                .icon(app.default_window_icon().unwrap().clone())
                .tooltip("SakuLoca · 待機中")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => show_window(app),
                    "quit" => {
                        let handle = app.clone();
                        std::thread::spawn(move || confirm_quit(&handle));
                    }
                    "pause" | "start" => {
                        let handle = app.clone();
                        let method = event.id.as_ref().to_owned();
                        std::thread::spawn(move || {
                            let result = handle
                                .state::<Broker>()
                                .lock()
                                .map_err(|e| e.to_string())
                                .and_then(|mut w| w.call(&method, json!({})));
                            if result.is_err() {
                                show_window(&handle);
                            }
                        });
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if matches!(
                        event,
                        TrayIconEvent::Click {
                            button: MouseButton::Left,
                            button_state: MouseButtonState::Up,
                            ..
                        }
                    ) {
                        show_window(tray.app_handle());
                    }
                })
                .build(app)?;
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let mut next_scan = std::time::Instant::now();
                loop {
                    std::thread::sleep(Duration::from_secs(5));
                    if handle.state::<Desktop>().quitting.load(Ordering::SeqCst) {
                        break;
                    }
                    let state = if let Ok(mut worker) = handle.state::<Broker>().try_lock() {
                        match worker.call("status", json!({})) {
                            Ok(status) => {
                                handle.state::<Desktop>().background.store(
                                    status["preferences"]["background"]
                                        .as_bool()
                                        .unwrap_or(false),
                                    Ordering::SeqCst,
                                );
                                let running = status["running"] == true;
                                handle
                                    .state::<Desktop>()
                                    .running
                                    .store(running, Ordering::SeqCst);
                                let tray_state = handle.state::<TrayState>();
                                let _ = tray_state.pause.set_enabled(running);
                                let _ = tray_state.resume.set_enabled(!running);
                                if status["preferences"]["auto_update"] == true
                                    && status["preferences"]["manual_pause"] != true
                                    && status["progress"]["phase"] != "error"
                                    && status["preferences"]["setup_complete"] == true
                                    && status["running"] == false
                                    && std::time::Instant::now() >= next_scan
                                {
                                    let _ = worker.call("start", json!({}));
                                    next_scan = std::time::Instant::now()
                                        + Duration::from_secs(
                                            status["preferences"]["update_minutes"]
                                                .as_u64()
                                                .unwrap_or(30)
                                                * 60,
                                        );
                                }
                                if running {
                                    let processed = status["progress"]["processed"]
                                        .as_u64()
                                        .unwrap_or(0);
                                    let discovered = status["progress"]["discovered"]
                                        .as_u64()
                                        .unwrap_or(0);
                                    if status["analysis_kind"] == "audio" {
                                        format!("音声解析中 {processed}区間")
                                    } else if discovered > 0 {
                                        format!("解析中 {processed}/{discovered}")
                                    } else {
                                        "解析中 · GPU/CPUを使用しています".to_owned()
                                    }
                                } else if status["progress"]["phase"] == "error" {
                                    "解析エラー · ウィンドウで確認".to_owned()
                                } else {
                                    "待機中 / 一時停止".to_owned()
                                }
                            }
                            Err(_) => "Worker停止 · 再起動が必要".to_owned(),
                        }
                    } else {
                        "AI処理中 · GPU/CPUを使用しています".to_owned()
                    };
                    if let Some(tray) = handle.tray_by_id("main-tray") {
                        let _ = tray.set_tooltip(Some(format!("SakuLoca · {state}")));
                    }
                }
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            worker_request,
            choose_path,
            open_media,
            desktop_action,
            desktop_preferences,
            migrate_storage,
            open_reference,
            updates::update_preferences,
            updates::check_update,
            updates::install_update
        ])
        .build(context);
    let application = match application {
        Ok(app) => app,
        Err(error) => {
            rfd::MessageDialog::new()
                .set_title("SakuLocaを起動できません")
                .set_description(error.to_string())
                .set_level(rfd::MessageLevel::Error)
                .show();
            return;
        }
    };
    application.run(|app, event| match event {
        tauri::RunEvent::WindowEvent {
            label,
            event: tauri::WindowEvent::CloseRequested { api, .. },
            ..
        } if label == "main" => {
            api.prevent_close();
            if app.state::<Desktop>().background.load(Ordering::SeqCst) {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.hide();
                }
            } else {
                let handle = app.clone();
                std::thread::spawn(move || confirm_quit(&handle));
            }
        }
        tauri::RunEvent::ExitRequested { api, .. } => {
            if !app.state::<Desktop>().quitting.load(Ordering::SeqCst) {
                api.prevent_exit();
                quit_safely(app);
            }
        }
        _ => {}
    });
}
