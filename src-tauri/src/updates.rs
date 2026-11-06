//! Signed release updates. No photo paths or search terms are sent.
use super::*;
use tauri::Emitter;
use tauri_plugin_updater::{Update, UpdaterExt};

#[derive(Default)]
pub struct Updates {
    pending: Mutex<Option<Update>>,
    busy: AtomicBool,
}

#[tauri::command]
pub fn update_preferences(app: tauri::AppHandle, automatic: Option<bool>) -> Result<Value, String> {
    let path = app.state::<Broker>().lock().map_err(|e| e.to_string())?.data.join("updates.json");
    if let Some(value) = automatic {
        let temporary = path.with_extension("json.tmp");
        std::fs::write(&temporary, json!({"automatic":value}).to_string()).map_err(|e| e.to_string())?;
        std::fs::rename(temporary, &path).map_err(|e| e.to_string())?;
    }
    let automatic = std::fs::read(&path).ok().and_then(|b| serde_json::from_slice::<Value>(&b).ok())
        .and_then(|v| v["automatic"].as_bool()).unwrap_or(true);
    Ok(json!({"automatic":automatic}))
}

#[tauri::command]
pub async fn check_update(app: tauri::AppHandle) -> Result<Value, String> {
    let handle = app.clone();
    let updater = app.updater_builder().timeout(Duration::from_secs(20))
        .on_before_exit(move || {
            handle.state::<Desktop>().quitting.store(true, Ordering::SeqCst);
            handle.cleanup_before_exit();
        }).build().map_err(|_| "更新設定を読み込めません。手動セットアップを利用してください。")?;
    let update = updater.check().await.map_err(|_| "更新を確認できません。通信状態、またはReleaseの公開状況を確認してください。手動更新も利用できます。")?;
    // Development/prerelease versions are never offered to stable users.
    if let Some(ref u) = update {
        if u.version.contains('-') {
            *app.state::<Updates>().pending.lock().map_err(|e| e.to_string())? = None;
            return Ok(json!({"available":false}));
        }
    }
    let result = update.as_ref().map(|u| json!({"available":true,"version":u.version,"notes":u.body}))
        .unwrap_or(json!({"available":false}));
    *app.state::<Updates>().pending.lock().map_err(|e| e.to_string())? = update;
    Ok(result)
}

struct Busy(tauri::AppHandle);
impl Drop for Busy {
    fn drop(&mut self) { self.0.state::<Updates>().busy.store(false, Ordering::SeqCst); }
}

#[tauri::command]
pub async fn install_update(app: tauri::AppHandle) -> Result<(), String> {
    if app.state::<Updates>().busy.swap(true, Ordering::SeqCst) {
        return Err("更新処理中です。完了をお待ちください。".into());
    }
    let _busy = Busy(app.clone());
    let update = app.state::<Updates>().pending.lock().map_err(|e| e.to_string())?.take()
        .ok_or("先に更新を確認してください。")?;
    let progress = app.clone();
    let mut received = 0_u64;
    let bytes = update.download(move |chunk, total| {
        received += chunk as u64;
        let _ = progress.emit("update-progress", json!({"received":received,"total":total}));
    }, || {}).await.map_err(|_| "更新の取得・署名検証に失敗しました。再確認して再試行するか、手動セットアップを利用してください。")?;
    // Do not ask Windows to replace the executable until the Worker has exited.
    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let broker = handle.state::<Broker>();
        let mut worker = broker.lock().map_err(|e| e.to_string())?;
        worker.call("shutdown", json!({}))?;
        let deadline = std::time::Instant::now() + Duration::from_secs(30);
        loop {
            if worker.child.try_wait().map_err(|e| e.to_string())?.is_some() { return Ok::<(), String>(()); }
            if std::time::Instant::now() >= deadline {
                return Err("Workerの保存処理が完了していません。解析停止後に完全終了し、手動更新してください。".into());
            }
            std::thread::sleep(Duration::from_millis(100));
        }
    }).await.map_err(|e| e.to_string())??;
    let _ = app.emit("update-progress", json!({"installing":true}));
    if update.install(bytes).is_err() {
        // The Worker was stopped above so Windows could replace files. If the
        // installer cannot start, restore the Worker instead of leaving the
        // app running with no local search service.
        let restart = app.clone();
        let restored = tauri::async_runtime::spawn_blocking(move || restart_worker(&restart))
            .await
            .map_err(|e| e.to_string())?;
        return Err(match restored {
            Ok(()) => "更新セットアップを起動できません。解析Workerを再起動しました。手動セットアップを実行してください。".into(),
            Err(_) => "更新セットアップを起動できません。アプリを再起動し、手動セットアップを実行してください。".into(),
        });
    }
    Ok(())
}
