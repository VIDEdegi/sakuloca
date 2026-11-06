//! Local-only control of the running app. No TCP port and no second Worker.
use super::*;
use std::io;
use tokio::{io::{AsyncReadExt, AsyncWriteExt}, net::windows::named_pipe::{NamedPipeServer, ServerOptions}};
use windows_sys::Win32::{
    Foundation::{CloseHandle, LocalFree},
    Security::{Authorization::{ConvertSidToStringSidW, ConvertStringSecurityDescriptorToSecurityDescriptorW}, GetTokenInformation, TokenUser, TOKEN_QUERY, TOKEN_USER, SECURITY_ATTRIBUTES},
    System::Threading::{GetCurrentProcess, OpenProcessToken},
};

const MAX_REQUEST: usize = 64 * 1024;

// Build an explicit DACL for the process user's SID, rather than inheriting the
// default named-pipe ACL (which can allow other users to read a pipe).
fn create_pipe(name: &str, first: bool) -> io::Result<NamedPipeServer> {
    unsafe {
        let mut token = std::ptr::null_mut();
        if OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) == 0 {
            return Err(io::Error::last_os_error());
        }
        let mut size = 0;
        GetTokenInformation(token, TokenUser, std::ptr::null_mut(), 0, &mut size);
        let mut storage = vec![0usize; (size as usize).div_ceil(std::mem::size_of::<usize>())];
        let ok = GetTokenInformation(token, TokenUser, storage.as_mut_ptr().cast(), size, &mut size);
        let error = io::Error::last_os_error();
        CloseHandle(token);
        if ok == 0 { return Err(error); }
        let user = &*storage.as_ptr().cast::<TOKEN_USER>();
        let mut sid = std::ptr::null_mut();
        if ConvertSidToStringSidW(user.User.Sid, &mut sid) == 0 {
            return Err(io::Error::last_os_error());
        }
        let mut len = 0;
        while *sid.add(len) != 0 { len += 1; }
        let sid_text = String::from_utf16_lossy(std::slice::from_raw_parts(sid, len));
        LocalFree(sid.cast());
        let sddl: Vec<u16> = format!("D:P(A;;GA;;;{sid_text})").encode_utf16().chain(Some(0)).collect();
        let mut descriptor = std::ptr::null_mut();
        if ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.as_ptr(), 1, &mut descriptor, std::ptr::null_mut()) == 0 {
            return Err(io::Error::last_os_error());
        }
        let mut attributes = SECURITY_ATTRIBUTES {
            nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: descriptor,
            bInheritHandle: 0,
        };
        let pipe = ServerOptions::new().first_pipe_instance(first).reject_remote_clients(true)
            .create_with_security_attributes_raw(name, (&mut attributes as *mut SECURITY_ATTRIBUTES).cast());
        LocalFree(descriptor);
        pipe
    }
}

pub fn start(app: &tauri::AppHandle, data: &std::path::Path) -> Result<(), String> {
    let nonce = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_err(|e| e.to_string())?.as_nanos();
    let name = format!(r"\\.\pipe\SakuLoca-{}-{nonce:x}", std::process::id());
    // Register before advertising, and refuse a pre-existing first instance.
    let first = tauri::async_runtime::block_on(async { create_pipe(&name, true) }).map_err(|e| e.to_string())?;
    let metadata = data.join("automation.json");
    let temp = data.join("automation.json.new");
    std::fs::write(&temp, json!({"version":1,"pipe":name,"pid":std::process::id()}).to_string()).map_err(|e| e.to_string())?;
    std::fs::rename(temp, metadata).map_err(|e| e.to_string())?;
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let slots = Arc::new(tokio::sync::Semaphore::new(4));
        let mut listener = first;
        loop {
            if listener.connect().await.is_err() { break; }
            // Keep a listening instance available while handling this request.
            let next = match create_pipe(&name, false) { Ok(pipe) => pipe, Err(_) => break };
            let connected = std::mem::replace(&mut listener, next);
            let permit = match slots.clone().try_acquire_owned() { Ok(permit) => permit, Err(_) => { drop(connected); continue; } };
            let handle = app.clone();
            tauri::async_runtime::spawn(async move {
                let _permit = permit;
                let _ = serve(connected, handle).await;
            });
        }
    });
    Ok(())
}

async fn serve(mut pipe: NamedPipeServer, app: tauri::AppHandle) -> Result<(), String> {
    let read = async {
        let mut line = Vec::new();
        let mut buffer = [0u8; 4096];
        loop {
            let n = pipe.read(&mut buffer).await.map_err(|e| e.to_string())?;
            if n == 0 { return Err("リクエストが途中で切れました".to_owned()); }
            line.extend_from_slice(&buffer[..n]);
            if line.len() > MAX_REQUEST { return Err("リクエストが大きすぎます".to_owned()); }
            if let Some(end) = line.iter().position(|byte| *byte == b'\n') {
                return serde_json::from_slice::<Value>(&line[..end]).map_err(|e| e.to_string());
            }
        }
    };
    let request = tokio::time::timeout(Duration::from_secs(5), read).await.map_err(|_| "リクエストがタイムアウトしました")?;
    let result = match request {
        Ok(request) => tauri::async_runtime::spawn_blocking(move || dispatch(&app, request)).await.map_err(|e| e.to_string())?,
        Err(error) => Err(error),
    };
    let reply = match result { Ok(result) => json!({"result":result}), Err(error) => json!({"error":error}) };
    let bytes = format!("{reply}\n");
    tokio::time::timeout(Duration::from_secs(5), pipe.write_all(bytes.as_bytes())).await.map_err(|_| "応答がタイムアウトしました")?.map_err(|e| e.to_string())?;
    Ok(())
}

fn dispatch(app: &tauri::AppHandle, request: Value) -> Result<Value, String> {
    if request["version"] != 1 { return Err("操作APIのバージョンが不一致です".into()); }
    if app.state::<Desktop>().quitting.load(Ordering::SeqCst) { return Err("SakuLocaは終了処理中です".into()); }
    let method = request["method"].as_str().ok_or("操作名がありません")?;
    let params = request.get("params").cloned().unwrap_or(json!({}));
    if !params.is_object() { return Err("操作の引数はオブジェクトで指定してください".into()); }
    let broker = app.state::<Broker>();
    if method == "desktop" {
        let action = params["action"].as_str().ok_or("操作を指定してください")?;
        match action {
            "show" => show_window(app),
            "restart_worker" | "quit" => {
                let mut worker = broker.lock().map_err(|e| e.to_string())?;
                if worker.child.try_wait().map_err(|e| e.to_string())?.is_none() {
                    match worker.call("status", json!({})) {
                        Ok(status) if status["running"] == true => return Err("解析を一時停止し、停止完了を確認してから実行してください".into()),
                        Ok(_) => {},
                        // A broken IPC stream can precede Windows reporting that
                        // the process exited. Explicit recovery must still work.
                        Err(_) => {},
                    }
                }
                if action == "restart_worker" {
                    let data = worker.data.clone();
                    let _ = worker.child.kill(); let _ = worker.child.wait();
                    *worker = Worker::spawn(app, &data)?;
                } else {
                    // Allow the success response to be delivered before shutdown.
                    let handle = app.clone();
                    std::thread::spawn(move || { std::thread::sleep(Duration::from_millis(250)); quit_safely(&handle); });
                }
            }
            _ => return Err("許可されていないデスクトップ操作です".into()),
        }
        return Ok(json!({"ok":true,"action":action}));
    }
    const METHODS: &[&str] = &["status","folders","errors","environment","search","start","pause","retry_errors"];
    if !METHODS.contains(&method) { return Err("許可されていない操作です".into()); }
    let mut worker = broker.lock().map_err(|e| e.to_string())?;
    let mut result = worker.call(method, params)?;
    if method == "status" {
        result["automation"] = json!({"app_pid":std::process::id(),"worker_pid":worker.child.id(),"version":env!("CARGO_PKG_VERSION")});
    }
    if method == "search" {
        if let Some(items) = result["items"].as_array_mut() {
            for item in items { if let Some(row) = item.as_object_mut() { row.retain(|key, _| matches!(key.as_str(), "id"|"name"|"path"|"size"|"format"|"mtime_ns"|"score"|"kind"|"segment_id"|"start_ms"|"end_ms"|"duration_ms"|"silent")); } }
        }
    }
    Ok(result)
}
