// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Host the existing ECT HTML/CSS/JavaScript interface in a Rust-owned window.
// Embedded application assets are the only files exposed to the webview protocol.
// JSON requests go to one bounded worker; SQLite never runs on the window thread.
// Replies return through the native event loop without waiting on JS callbacks.
// Ordinary close bounds helper shutdown; confirmed updates await actual cleanup.
// RPC modes exercise the same opening/reader dispatcher without native windows.
use anyhow::{bail, Result};
use mailsearch_rust::bridge::{Bridge, Request};
use std::{
    io::{self, BufRead, Write},
    path::PathBuf,
};

#[cfg(all(target_os = "macos", feature = "native-smoke"))]
#[path = "../native_smoke.rs"]
mod native_smoke;

#[cfg(all(target_os = "windows", feature = "native-smoke"))]
#[path = "../native_smoke_windows.rs"]
mod native_smoke;

fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    #[cfg(target_os = "windows")]
    {
        if args.first().is_some_and(|arg| arg == "--check-webview") {
            println!("WebView2 {}", wry::webview_version()?);
            return Ok(());
        }
        let graphical = args.is_empty()
            || args.first().is_some_and(|arg| {
                arg == "--archive" || arg == "--native-smoke" || arg == "--startup-smoke"
            });
        if graphical {
            if let Err(error) = wry::webview_version() {
                rfd::MessageDialog::new()
                    .set_title("Email Collection Toolkit — WebView2 required")
                    .set_level(rfd::MessageLevel::Error)
                    .set_description(format!("Microsoft Edge WebView2 Runtime is missing or unavailable.\n\nInstall the WebView2 Evergreen Runtime from:\nhttps://developer.microsoft.com/microsoft-edge/webview2/\n\nFor a disconnected computer, download the Standalone Installer for this computer's architecture on another computer and transfer it using approved media. Contact your IT administrator if installation is restricted.\n\nECT does not install WebView2 automatically.\n\nDetails: {error}"))
                    .show();
                bail!("WebView2 Runtime is unavailable: {error}");
            }
        }
    }
    #[cfg(target_os = "windows")]
    if let [flag, output] = args.as_slice() {
        if flag == "--msix-test" {
            let executable = std::env::current_exe()?;
            let root = executable.parent().unwrap();
            let result = std::process::Command::new(root.join("python/python.exe"))
                .arg("-I")
                .arg(root.join("test_windows_msix.py"))
                .arg(&executable)
                .arg(output)
                .arg("--installed-root")
                .arg(root)
                .output()?;
            let log = PathBuf::from(output).with_extension("log");
            let mut contents = result.stdout;
            contents.extend_from_slice(&result.stderr);
            std::fs::write(log, contents)?;
            anyhow::ensure!(result.status.success(), "Packaged self-test failed");
            return Ok(());
        }
    }
    match args.as_slice() {
        [flag] if flag == "--check-updater" => {
            #[cfg(target_os = "macos")]
            {
                mailsearch_rust::macos::inspect()?;
            }
            #[cfg(target_os = "macos")]
            let updater = mailsearch_rust::updater::Updater::inspect()?;
            #[cfg(not(target_os = "macos"))]
            let updater = mailsearch_rust::updater::Updater::new(false, Default::default(), || {});
            anyhow::ensure!(updater.available(), "{}", updater.detail);
            println!("{}", updater.detail);
            Ok(())
        }
        #[cfg(target_os = "macos")]
        [flag] if flag == "--updater-shutdown-probe" => {
            mailsearch_rust::updater::inspect_shutdown()
        }
        [flag] if flag == "--updater-fence" => {
            let installation = mailsearch_rust::update_policy::Installation::default();
            if installation.reserve()? {
                println!("reserved");
                io::stdout().flush()?;
                let mut line = String::new();
                io::stdin().read_line(&mut line)?;
                #[cfg(target_os = "macos")]
                if line.trim() == "native-error" {
                    let failure: block2::RcBlock<dyn Fn()> = block2::RcBlock::new(|| {
                        // Exercise a real Objective-C exception, not a mock SDK.
                        let object = objc2_foundation::NSObject::new();
                        let exception = unsafe { objc2::rc::Retained::cast_unchecked(object) };
                        objc2::exception::throw(exception);
                    });
                    anyhow::ensure!(
                        installation.invoke(&failure).is_err(),
                        "Expected native continuation failure"
                    );
                } else {
                    installation.cancel();
                }
                #[cfg(not(target_os = "macos"))]
                installation.cancel();
                println!("released");
                io::stdout().flush()?;
                io::stdin().read_line(&mut line)?;
            } else {
                println!("blocked");
            }
            Ok(())
        }
        #[cfg(target_os = "macos")]
        [flag] if flag == "--check-macos-integration" => {
            println!("{}", mailsearch_rust::macos::inspect()?);
            Ok(())
        }
        [] => native(startup_archive()?, None, None),
        [flag, path] if flag == "--rpc" => {
            let mut bridge = Bridge::open(&PathBuf::from(path))?;
            for line in io::stdin().lock().lines() {
                let request: Request = serde_json::from_str(&line?)?;
                println!("{}", serde_json::to_string(&bridge.reply(request))?);
                io::stdout().flush()?;
            }
            Ok(())
        }
        [flag, path]
            if matches!(
                flag.as_str(),
                "--opening-rpc" | "--creation-rpc" | "--opening-reader-rpc"
            ) =>
        {
            opening_rpc(
                PathBuf::from(path),
                flag == "--creation-rpc",
                flag == "--opening-reader-rpc",
            )
        }
        [flag] if flag == "--opening-rpc" => opening_rpc(
            startup_archive()?.ok_or_else(|| anyhow::anyhow!("No usable recent archive"))?,
            false,
            false,
        ),
        [flag, path, query] if flag == "--probe" => {
            let mut bridge = Bridge::open(&PathBuf::from(path))?;
            let start = std::time::Instant::now();
            let reply = bridge.reply(Request {
                id: 1,
                method: "search_start".into(),
                args: vec![serde_json::json!(query)],
            });
            if let Some(error) = reply.error {
                bail!("{error}");
            }
            let generation = reply.result.unwrap()["generation"].clone();
            let mut acknowledged = 0;
            loop {
                let reply = bridge.reply(Request {
                    id: 2,
                    method: "search_status".into(),
                    args: vec![generation.clone()],
                });
                if let Some(error) = reply.error {
                    bail!("{error}");
                }
                let status = reply.result.unwrap();
                let window = status["window"].as_u64().unwrap();
                if window > acknowledged || status["complete"] == true {
                    println!(
                        "Window {window}: {} matches, complete={}, {} ms",
                        status["count"],
                        status["complete"],
                        start.elapsed().as_millis()
                    );
                }
                if let Some(error) = status["error"].as_str() {
                    bail!("{error}");
                }
                if status["complete"] == true {
                    break;
                }
                if window > acknowledged {
                    bridge.reply(Request {
                        id: 3,
                        method: "search_advance".into(),
                        args: vec![generation.clone(), serde_json::json!(window)],
                    });
                    acknowledged = window;
                }
                anyhow::ensure!(start.elapsed().as_secs() < 135, "Probe did not finish");
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
            Ok(())
        }
        [flag, path] if flag == "--archive" => native(Some(PathBuf::from(path)), None, None),
        [flag, path, message_flag, message, highlight_flag, highlights]
            if flag == "--archive"
                && message_flag == "--message"
                && highlight_flag == "--highlights" =>
        {
            let message = message.parse::<i64>()?;
            let highlights = serde_json::from_str::<Vec<String>>(highlights)?;
            let mut parameters = url::form_urlencoded::Serializer::new(String::new());
            parameters
                .append_pair("standalone", "1")
                .append_pair("message", &message.to_string());
            for term in highlights {
                parameters.append_pair("highlight", &term);
            }
            native(Some(PathBuf::from(path)), None, Some(parameters.finish()))
        }
        #[cfg(feature = "native-smoke")]
        [flag] if flag == "--startup-smoke" => {
            println!("{}", startup_writes_available());
            Ok(())
        }
        #[cfg(feature = "native-smoke")]
        [flag, path, output] if flag == "--native-smoke" => {
            native(Some(PathBuf::from(path)), Some(PathBuf::from(output)), None)
        }
        #[cfg(all(target_os = "macos", feature = "native-smoke"))]
        [flag, path, phase, output]
            if flag == "--native-editor-smoke" && matches!(phase.as_str(), "mutate" | "verify") =>
        {
            native(
                Some(PathBuf::from(path)),
                Some(PathBuf::from(output)),
                Some(format!("native-editors={phase}")),
            )
        }
        _ => bail!(
            "Usage: mailsearch-webview --archive DIRECTORY (or --rpc DIRECTORY for headless tests)"
        ),
    }
}

fn startup_archive() -> Result<Option<PathBuf>> {
    let recent = mailsearch_rust::documents::Documents::load(
        &mailsearch_rust::documents::Documents::path()?,
    )?;
    Ok(recent
        .first_openable_archive()
        .map(std::path::Path::to_owned))
}

fn opening_rpc(path: PathBuf, create: bool, retain: bool) -> Result<()> {
    use std::sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Arc,
    };
    let abort = Arc::new(AtomicBool::new(false));
    let reader_abort = abort.clone();
    let (sender, receiver) = mpsc::sync_channel(64);
    std::thread::spawn(move || {
        for line in io::stdin().lock().lines() {
            let Ok(request) = line
                .and_then(|line| serde_json::from_str::<Request>(&line).map_err(io::Error::other))
            else {
                break;
            };
            if matches!(request.method.as_str(), "opening_abort" | "quit") {
                reader_abort.store(true, Ordering::Release);
                break;
            } else if sender.try_send(request).is_err() {
                break;
            }
        }
        reader_abort.store(true, Ordering::Release);
    });
    let request = receiver.recv()?;
    anyhow::ensure!(request.method == "opening_start", "Expected opening_start");
    let mut result = (|| {
        if create {
            mailsearch_rust::engine::Engine::create_archive(&path, &abort)?;
        }
        mailsearch_rust::opening::open(&path, &abort, || {
            println!(
                "{}",
                serde_json::json!({"id":0,"result":"recovering","error":null})
            );
            let _ = io::stdout().flush();
        })
    })();
    if retain {
        result = result.and_then(|bridge| {
            finish_opening(bridge, &abort, || {
                mailsearch_rust::documents::Documents::remember(
                    &mailsearch_rust::documents::Documents::path()?,
                    &path,
                )
            })
        });
    }
    let reply = mailsearch_rust::bridge::Reply {
        id: request.id,
        result: result.as_ref().ok().map(|_| serde_json::json!(true)),
        error: result.as_ref().err().map(|error| format!("{error:#}")),
    };
    println!("{}", serde_json::to_string(&reply)?);
    io::stdout().flush()?;
    if retain {
        if let Ok(mut bridge) = result {
            for request in receiver {
                println!("{}", serde_json::to_string(&bridge.reply(request))?);
                io::stdout().flush()?;
            }
        }
    }
    Ok(())
}

fn startup_writes_available() -> bool {
    mailsearch_rust::engine::ARCHIVE_WRITING_SUPPORTED
        && mailsearch_rust::engine::Engine::open(std::path::Path::new("."))
            .and_then(|mut engine| engine.call("capabilities", &[]))
            .is_ok_and(|capabilities| {
                capabilities["available"] == true && capabilities["write_available"] == true
            })
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn native(
    _path: Option<PathBuf>,
    _smoke_output: Option<PathBuf>,
    _parameters: Option<String>,
) -> Result<()> {
    bail!("The native shell is enabled on macOS and Windows; RPC and core tests are portable")
}

#[cfg(any(target_os = "macos", target_os = "windows", test))]
fn allowed_navigation(url: &str, windows: bool) -> bool {
    // Sandboxed MIME frames navigate to these inert internal documents. They
    // remain excluded from IPC trust, as do remote/file/data destinations.
    matches!(url, "about:blank" | "about:srcdoc")
        || trusted_document(url, windows)
        || trusted_welcome(url, windows)
        || trusted_opening(url, windows)
        || trusted_panel(url, windows)
}

fn trusted_document(url: &str, windows: bool) -> bool {
    url == if windows {
        "http://ect.localhost/index.html"
    } else {
        "ect://localhost/index.html"
    }
}

#[cfg(any(target_os = "macos", target_os = "windows", test))]
fn trusted_opening(url: &str, windows: bool) -> bool {
    url == if windows {
        "http://ect.localhost/opening.html"
    } else {
        "ect://localhost/opening.html"
    }
}

#[cfg(any(target_os = "macos", target_os = "windows", test))]
fn trusted_welcome(url: &str, windows: bool) -> bool {
    url == if windows {
        "http://ect.localhost/welcome.html"
    } else {
        "ect://localhost/welcome.html"
    }
}

#[cfg(any(target_os = "macos", target_os = "windows", test))]
fn trusted_panel(value: &str, windows: bool) -> bool {
    let Ok(url) = url::Url::parse(value) else {
        return false;
    };
    url.scheme() == if windows { "http" } else { "ect" }
        && url.host_str()
            == Some(if windows {
                "ect.localhost"
            } else {
                "localhost"
            })
        && url.port().is_none()
        && url.username().is_empty()
        && url.password().is_none()
        && ["/identity.html", "/options.html", "/ingests.html"].contains(&url.path())
        && url.fragment().is_none()
        && (url.query().is_none()
            || (url.path() == "/identity.html"
                && matches!(url.query(), Some("kind=name" | "kind=institution"))))
}

fn finish_opening(
    mut bridge: Bridge,
    abort: &std::sync::atomic::AtomicBool,
    remember: impl FnOnce() -> Result<()>,
) -> Result<Bridge> {
    if let Err(error) = remember() {
        bridge.opening_notice(format!(
            "Archive opened, but its recent-document preference could not be saved: {error:#}"
        ));
    }
    anyhow::ensure!(
        !abort.load(std::sync::atomic::Ordering::Acquire),
        "Opening aborted. The archive cannot be opened."
    );
    bridge.enable_desktop();
    Ok(bridge)
}

#[cfg(any(target_os = "macos", target_os = "windows", test))]
fn quiesce_reader(bridge: &mut Option<std::result::Result<Bridge, String>>) -> (bool, Result<()>) {
    if let Some(Ok(bridge)) = bridge {
        (true, bridge.quiesce())
    } else {
        bridge.take();
        (false, Ok(()))
    }
}

#[cfg(any(target_os = "macos", target_os = "windows"))]
fn native(
    path: Option<PathBuf>,
    smoke_output: Option<PathBuf>,
    parameters: Option<String>,
) -> Result<()> {
    use mailsearch_rust::bridge::{native_asset, Reply, SCRIPT};
    use std::{borrow::Cow, sync::mpsc, thread};
    use tao::{
        event::{Event, WindowEvent},
        event_loop::{ControlFlow, EventLoopBuilder},
        window::WindowBuilder,
    };
    use wry::WebViewBuilder;
    enum NativeEvent {
        #[cfg(all(target_os = "macos", feature = "native-smoke"))]
        DragSmoke(Vec<String>),
        Reply(Reply),
        ReaderReady,
        Capabilities(serde_json::Value),
        Recovering,
        Shell(Request),
        Menu(muda::MenuEvent),
        Quit,
        QuitFinished(bool, Result<()>),
        QuitTimedOut(u64),
        ExportCleanup(anyhow::Result<()>),
        Print(u64),
        Welcome(Request),
        SelectArchive(PathBuf, bool),
        Notice(String),
        #[cfg(feature = "native-smoke")]
        Snapshot,
        #[cfg(feature = "native-smoke")]
        ResizeSmoke,
        #[cfg(all(target_os = "windows", feature = "native-smoke"))]
        AcceleratorSmoke,
        #[cfg(feature = "native-smoke")]
        SmokeFinished(Result<()>),
    }
    let mut event_builder = EventLoopBuilder::<NativeEvent>::with_user_event();
    #[cfg(target_os = "windows")]
    let accelerators = std::rc::Rc::new(std::cell::Cell::new((0isize, 0isize)));
    #[cfg(all(target_os = "windows", feature = "native-smoke"))]
    let translated = std::rc::Rc::new(std::cell::Cell::new(0usize));
    #[cfg(target_os = "windows")]
    {
        use tao::platform::windows::EventLoopBuilderExtWindows;
        use windows_sys::Win32::UI::WindowsAndMessaging::{TranslateAcceleratorW, MSG};
        let handles = accelerators.clone();
        #[cfg(feature = "native-smoke")]
        let translated = translated.clone();
        event_builder.with_msg_hook(move |message| {
            let (window, menu) = handles.get();
            if window == 0 || menu == 0 || message.is_null() {
                return false;
            }
            // Tao supplies the live MSG on this GUI thread. The window and menu
            // own these handles until LoopDestroyed clears them before disposal.
            let handled = unsafe {
                TranslateAcceleratorW(window as _, menu as _, message.cast::<MSG>()) != 0
            };
            #[cfg(feature = "native-smoke")]
            if handled {
                translated.set(translated.get() + 1);
            }
            handled
        });
    }
    let events = event_builder.build();
    #[cfg(target_os = "macos")]
    mailsearch_rust::macos::configure_identity()?;
    let mut welcome = path.is_none();
    let mut welcome_writable = false;
    let mut archive_path = path.clone();
    let window = WindowBuilder::new()
        .with_title(format!(
            "Email Collection Toolkit — {} · Rust",
            path.as_ref()
                .map(|path| path.display().to_string())
                .unwrap_or_else(|| "Open an archive".into())
        ))
        .with_inner_size(tao::dpi::LogicalSize::new(1250.0, 850.0))
        .build(&events)?;
    let mut menu = Some(mailsearch_rust::shell::menu(&window)?);
    #[cfg(target_os = "windows")]
    {
        use tao::platform::windows::WindowExtWindows;
        accelerators.set((window.hwnd() as isize, menu.as_ref().unwrap().haccel()));
    }
    let menu_proxy = events.create_proxy();
    muda::MenuEvent::set_event_handler(Some(move |event| {
        let _ = menu_proxy.send_event(NativeEvent::Menu(event));
    }));
    let quit_proxy = events.create_proxy();
    #[cfg(target_os = "macos")]
    {
        let termination_proxy = events.create_proxy();
        mailsearch_rust::macos::coordinate_termination(move || {
            let _ = termination_proxy.send_event(NativeEvent::Quit);
        })?;
    }
    let mut shell = Some(mailsearch_rust::shell::Shell::new(move || {
        let _ = quit_proxy.send_event(NativeEvent::Quit);
    })?);
    let proxy = events.create_proxy();
    let (sender, receiver) = mpsc::sync_channel::<Request>(64);
    let abort = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let closing = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let worker_abort = abort.clone();
    if welcome {
        let capability_proxy = events.create_proxy();
        thread::spawn(move || {
            let available = startup_writes_available();
            let _ = capability_proxy.send_event(NativeEvent::Capabilities(
                serde_json::json!({"available":available,"write_available":available}),
            ));
        });
    }
    let remember = smoke_output.is_none();
    thread::Builder::new()
        .name("archive-web-reader".into())
        .spawn(move || {
            let mut bridge: Option<std::result::Result<Bridge, String>> = None;
            let mut selected_path = path;
            let mut create = false;
            while let Ok(request) = receiver.recv() {
                if request.method == "shutdown" {
                    let (ready, result) = quiesce_reader(&mut bridge);
                    let _ = proxy.send_event(NativeEvent::QuitFinished(ready, result));
                    continue;
                }
                if request.method == "opening_select" {
                    create = request
                        .args
                        .get(1)
                        .and_then(|value| value.as_bool())
                        .unwrap_or(false);
                    selected_path = request
                        .args
                        .first()
                        .and_then(|value| value.as_str())
                        .map(PathBuf::from);
                    continue;
                }
                if request.method == "opening_start" {
                    if bridge.is_some() {
                        continue;
                    }
                    let result = selected_path
                        .as_ref()
                        .ok_or_else(|| anyhow::anyhow!("No archive selected"))
                        .and_then(|path| {
                            if create {
                                mailsearch_rust::engine::Engine::create_archive(
                                    path,
                                    &worker_abort,
                                )?;
                            }
                            mailsearch_rust::opening::open(path, &worker_abort, || {
                                let _ = proxy.send_event(NativeEvent::Recovering);
                            })
                            .and_then(|bridge| {
                                finish_opening(bridge, &worker_abort, || {
                                    if !remember {
                                        return Ok(());
                                    }
                                    mailsearch_rust::documents::Documents::remember(
                                        &mailsearch_rust::documents::Documents::path()?,
                                        path,
                                    )
                                })
                            })
                        })
                        .map_err(|error| format!("{error:#}"));
                    let reply = Reply {
                        id: request.id,
                        result: result.as_ref().ok().map(|_| serde_json::json!(true)),
                        error: result.as_ref().err().cloned(),
                    };
                    if result.is_ok() {
                        let _ = proxy.send_event(NativeEvent::ReaderReady);
                    }
                    bridge = Some(result);
                    let _ = proxy.send_event(NativeEvent::Reply(reply));
                    continue;
                }
                let capabilities = request.method == "engine_status";
                let reply = match &mut bridge {
                    Some(Ok(bridge)) => bridge.reply(request),
                    _ => Reply {
                        id: request.id,
                        result: None,
                        error: Some("The archive has not been opened.".into()),
                    },
                };
                if capabilities {
                    let _ = proxy.send_event(NativeEvent::Capabilities(
                        reply
                            .result
                            .clone()
                            .unwrap_or(serde_json::json!({"available":false})),
                    ));
                }
                if proxy.send_event(NativeEvent::Reply(reply)).is_err() {
                    break;
                }
            }
        })?;
    let shutdown_sender = sender.clone();
    let ipc_proxy = events.create_proxy();
    let ipc_abort = abort.clone();
    let ipc_closing = closing.clone();
    let diagnostics = std::env::var_os("ECT_RUST_WEBVIEW_DIAGNOSTICS").is_some();
    #[cfg(feature = "native-smoke")]
    let smoke_enabled = smoke_output.is_some();
    let window_parameters = format!(
        "window.__rustWindowParameters={};",
        serde_json::to_string(&parameters.unwrap_or_default())?
    );
    #[cfg(target_os = "windows")]
    let mut context = {
        let directory = PathBuf::from(
            std::env::var_os("LOCALAPPDATA")
                .ok_or_else(|| anyhow::anyhow!("LOCALAPPDATA is unavailable"))?,
        )
        .join("Email Collection Toolkit/WebView2");
        std::fs::create_dir_all(&directory)?;
        wry::WebContext::new(Some(directory))
    };
    #[cfg(target_os = "windows")]
    let builder = WebViewBuilder::new_with_web_context(&mut context);
    #[cfg(not(target_os = "windows"))]
    let builder = WebViewBuilder::new();
    let builder = builder
        .with_initialization_script(&window_parameters)
        .with_custom_protocol("ect".into(), |_, request| {
            let (status, mime, bytes) = match native_asset(request.uri().path(), cfg!(windows)) {
                Some((mime, bytes)) => (200, mime, bytes),
                None => (404, "text/plain", Cow::Borrowed(b"Not found".as_slice())),
            };
            wry::http::Response::builder()
                .status(status)
                .header("Content-Type", mime)
                .body(bytes)
                .unwrap()
        })
        .with_initialization_script(SCRIPT)
        .with_initialization_script(mailsearch_rust::shell::SCRIPT)
        .with_navigation_handler(move |url| {
            let trusted = allowed_navigation(&url, cfg!(target_os = "windows"));
            if diagnostics {
                eprintln!("Webview navigation: {url:?}, trusted={trusted}");
            }
            trusted
        })
        .with_ipc_handler(move |request| {
            let url = request.uri().to_string();
            let opening = trusted_opening(&url, cfg!(target_os = "windows"));
            let is_welcome = trusted_welcome(&url, cfg!(target_os = "windows"));
            let trusted =
                trusted_document(&url, cfg!(target_os = "windows")) || opening || is_welcome;
            if diagnostics {
                eprintln!("Webview IPC: {url:?}, trusted={trusted}");
            }
            if !trusted {
                return;
            }
            if let Ok(message) = serde_json::from_str::<Request>(request.body()) {
                if ipc_closing.load(std::sync::atomic::Ordering::Acquire)
                    && message.method != "quit"
                {
                    let _ = ipc_proxy.send_event(NativeEvent::Reply(Reply {
                        id: message.id,
                        result: None,
                        error: Some("Finishing archive work before closing…".into()),
                    }));
                    return;
                }
                if is_welcome {
                    if matches!(
                        message.method.as_str(),
                        "welcome_open" | "welcome_new" | "welcome_status"
                    ) {
                        let _ = ipc_proxy.send_event(NativeEvent::Welcome(message));
                    } else if message.method == "quit" {
                        let _ = ipc_proxy.send_event(NativeEvent::Quit);
                    } else if matches!(
                        message.method.as_str(),
                        "shell_status" | "preferences_save" | "check_updates"
                    ) {
                        let _ = ipc_proxy.send_event(NativeEvent::Shell(message));
                    }
                    return;
                }
                if opening && message.method == "opening_abort" {
                    ipc_abort.store(true, std::sync::atomic::Ordering::Release);
                    return;
                }
                if opening && !matches!(message.method.as_str(), "opening_start" | "quit") {
                    return;
                }
                #[cfg(feature = "native-smoke")]
                if smoke_enabled {
                    match message.method.as_str() {
                        #[cfg(target_os = "macos")]
                        "native_smoke_drag" => {
                            if let Ok(tokens) =
                                serde_json::from_value(serde_json::Value::Array(message.args))
                            {
                                let _ = ipc_proxy.send_event(NativeEvent::DragSmoke(tokens));
                            }
                            return;
                        }
                        "native_smoke_close_ready" => {
                            eprintln!("Native smoke: verified quit during unfinished search");
                            let _ = ipc_proxy.send_event(NativeEvent::Quit);
                            return;
                        }
                        "native_smoke_resize" => {
                            let _ = ipc_proxy.send_event(NativeEvent::ResizeSmoke);
                            return;
                        }
                        #[cfg(target_os = "windows")]
                        "native_smoke_accelerator" => {
                            let _ = ipc_proxy.send_event(NativeEvent::AcceleratorSmoke);
                            return;
                        }
                        "native_smoke_ready" => {
                            let _ = ipc_proxy.send_event(NativeEvent::Snapshot);
                            return;
                        }
                        "native_smoke_failed" => {
                            let _ = ipc_proxy.send_event(NativeEvent::SmokeFinished(Err(
                                anyhow::anyhow!("Native UI assertion: {:?}", message.args),
                            )));
                            return;
                        }
                        _ => (),
                    }
                }
                if matches!(
                    message.method.as_str(),
                    "shell_status" | "preferences_save" | "check_updates"
                ) {
                    let _ = ipc_proxy.send_event(NativeEvent::Shell(message));
                    return;
                }
                if message.method == "print" {
                    let _ = ipc_proxy.send_event(NativeEvent::Print(message.id));
                    return;
                }
                if message.method == "quit" {
                    let _ = ipc_proxy.send_event(NativeEvent::Quit);
                    return;
                }
                let id = message.id;
                if sender.try_send(message).is_err() {
                    let _ = ipc_proxy.send_event(NativeEvent::Reply(Reply {
                        id,
                        result: None,
                        error: Some("Reader busy; retry after the current operation".into()),
                    }));
                }
            }
        })
        .with_url(if welcome {
            "ect://localhost/welcome.html"
        } else {
            "ect://localhost/opening.html"
        });
    #[cfg(feature = "native-smoke")]
    let builder = if smoke_enabled {
        {
            #[cfg(target_os = "macos")]
            let driver = include_str!("../../native-smoke.js");
            #[cfg(target_os = "windows")]
            let driver = include_str!("../../native-smoke-windows.js");
            builder
                .with_initialization_script_for_main_only(driver, false)
                .with_initialization_script(
                    if std::env::var_os("ECT_RUST_NATIVE_CLOSE_SMOKE").is_some() {
                        "window.__ectCloseSmoke = true;"
                    } else {
                        "window.__ectCloseSmoke = false;"
                    },
                )
        }
    } else {
        builder
    };
    let view = builder.build(&window)?;
    #[cfg(target_os = "macos")]
    mailsearch_rust::drag::install(&view)?;
    #[cfg(feature = "native-smoke")]
    let snapshot_proxy = events.create_proxy();
    #[cfg(feature = "native-smoke")]
    let mut smoke_output = smoke_output;
    #[cfg(feature = "native-smoke")]
    if smoke_enabled {
        let timeout_proxy = events.create_proxy();
        thread::spawn(move || {
            thread::sleep(std::time::Duration::from_secs(60));
            let _ = timeout_proxy.send_event(NativeEvent::SmokeFinished(Err(anyhow::anyhow!(
                "Native smoke timed out"
            ))));
        });
    }
    let reply_proxy = events.create_proxy();
    let mut quitting = false;
    let mut worker_finished = false;
    let mut quit_timed_out = false;
    let mut quit_epoch = 0u64;
    let mut update_failure = None;
    let mut exports_cleaned = false;
    let mut cleanup_failed = false;
    let mut reader_ready = false;
    #[cfg(feature = "native-smoke")]
    let mut smoke_passed = false;
    #[cfg(all(target_os = "windows", feature = "native-smoke"))]
    let mut accelerator_trial = None;
    events.run(move |event, _, flow| {
        *flow = ControlFlow::Wait;
        match event {
            Event::Opened { urls } if !quitting => {
                match mailsearch_rust::documents::opened_paths(&urls) {
                    Ok(paths) => for path in paths {
                        let _ = reply_proxy.send_event(NativeEvent::SelectArchive(path, false));
                    },
                    Err(error) => { let _=reply_proxy.send_event(NativeEvent::Notice(format!("{error:#}"))); }
                }
            }
            Event::Reopen { .. } => window.set_focus(),
            Event::UserEvent(NativeEvent::Welcome(request)) if welcome && !quitting => {
                if request.method == "welcome_status" {
                    let _ = reply_proxy.send_event(NativeEvent::Reply(Reply{id:request.id,result:Some(serde_json::json!({"write_available":welcome_writable})),error:None}));
                    return;
                }
                let result: Result<Option<PathBuf>> = (|| {
                    if request.method == "welcome_open" {
                        return Ok(mailsearch_rust::desktop::pick_archive());
                    }
                    anyhow::ensure!(welcome_writable,"Archive creation is unavailable");
                    let path = rfd::FileDialog::new().set_title("Choose empty destination for new archive").pick_folder();
                    Ok(path)
                })();
                match result {
                    Ok(Some(path)) => {let _=reply_proxy.send_event(NativeEvent::SelectArchive(path, request.method == "welcome_new"));},
                    result => {let _=reply_proxy.send_event(NativeEvent::Reply(Reply{id:request.id,result:Some(serde_json::json!(false)),error:result.err().map(|error|format!("{error:#}"))}));}
                }
            }
            Event::UserEvent(NativeEvent::SelectArchive(path, create)) if !quitting => {
                if welcome {
                    let request=Request{id:0,method:"opening_select".into(),args:vec![serde_json::json!(path),serde_json::json!(create)]};
                    let result = shutdown_sender.try_send(request).map_err(anyhow::Error::from)
                        .and_then(|()| view.load_url(if cfg!(windows){"http://ect.localhost/opening.html"}else{"ect://localhost/opening.html"}).map_err(anyhow::Error::from));
                    match result {
                        Ok(()) => {
                            window.set_title(&format!("Email Collection Toolkit — {} · Rust",path.display()));
                            archive_path=Some(path);
                            welcome=false;
                        }
                        Err(error) => {let _=reply_proxy.send_event(NativeEvent::Notice(format!("{error:#}")));}
                    }
                } else if archive_path.as_ref().and_then(|current|current.canonicalize().ok()) != path.canonicalize().ok() {
                    let result=std::env::current_exe().map_err(anyhow::Error::from)
                        .and_then(|executable|mailsearch_rust::desktop::spawn(std::process::Command::new(executable).arg("--archive").arg(path)));
                    if let Err(error)=result {let _=reply_proxy.send_event(NativeEvent::Notice(format!("{error:#}")));}
                } else {window.set_focus();}
            }
            Event::UserEvent(NativeEvent::Notice(message)) => {
                let text=serde_json::to_string(&message).unwrap();
                let _=view.evaluate_script(&format!("if(window.mailArchiverNotice){{window.mailArchiverNotice({text})}}else{{document.getElementById('detail')?.replaceChildren(document.createTextNode({text}))}}"));
            }
            Event::WindowEvent {
                event: WindowEvent::CloseRequested,
                ..
            }
            | Event::UserEvent(NativeEvent::Quit) => {
                if !quitting {
                    quitting=true;
                    if let Some(shell) = &shell { shell.updater().begin_shutdown(); }
                    quit_epoch += 1;
                    closing.store(true, std::sync::atomic::Ordering::Release);
                    let cleanup_proxy = reply_proxy.clone();
                    mailsearch_rust::drag::close(move |result| {
                        let _ = cleanup_proxy.send_event(NativeEvent::ExportCleanup(result));
                    });
                    abort.store(true, std::sync::atomic::Ordering::Release);
                    let shutdown = shutdown_sender.clone();
                    thread::spawn(move || { let _ = shutdown.send(Request{id:0,method:"shutdown".into(),args:vec![]}); });
                    let watchdog=reply_proxy.clone();
                    let epoch = quit_epoch;
                    thread::spawn(move||{thread::sleep(std::time::Duration::from_secs(5));let _=watchdog.send_event(NativeEvent::QuitTimedOut(epoch));});
                }
            }
            Event::UserEvent(NativeEvent::QuitTimedOut(epoch)) if quitting && epoch == quit_epoch => quit_timed_out = true,
            Event::UserEvent(NativeEvent::QuitFinished(ready, result)) => {
                // Opening can finish after Quit hides its ReaderReady event.
                // The worker owns the retained bridge and reports its actual state.
                reader_ready = ready;
                worker_finished = true;
                if let Err(error) = result { eprintln!("{error:#}"); cleanup_failed = true; }
            }
            Event::UserEvent(NativeEvent::ExportCleanup(result)) => {
                exports_cleaned = true;
                if let Err(error) = result {
                    eprintln!("{error:#}");
                    cleanup_failed = true;
                }
            }
            #[cfg(all(target_os = "macos", feature = "native-smoke"))]
            Event::UserEvent(NativeEvent::DragSmoke(tokens)) => {
                let result = tokens.iter().try_for_each(|token| mailsearch_rust::drag::inspect(token));
                match result {
                    Ok(()) => { let _ = view.evaluate_script("window.__rustDragVerified=true;"); }
                    Err(error) => { let _ = reply_proxy.send_event(NativeEvent::SmokeFinished(Err(error))); }
                }
            }
            #[cfg(feature = "native-smoke")]
            Event::UserEvent(NativeEvent::ResizeSmoke) => {
                window.set_inner_size(tao::dpi::LogicalSize::new(1100.0, 740.0));
            }
            #[cfg(all(target_os = "windows", feature = "native-smoke"))]
            Event::UserEvent(NativeEvent::AcceleratorSmoke) => {
                match native_smoke::KeyboardState::preferences(&window) {
                    Ok(guard) => accelerator_trial = Some((guard, translated.get())),
                    Err(error) => { let _ = reply_proxy.send_event(NativeEvent::SmokeFinished(Err(error))); }
                }
            }
            #[cfg(feature = "native-smoke")]
            Event::UserEvent(NativeEvent::Snapshot) => {
                if let Some(output) = smoke_output.take() {
                    let proxy = snapshot_proxy.clone();
                    native_smoke::snapshot(&view, output, move |result| {
                        let _ = proxy.send_event(NativeEvent::SmokeFinished(result));
                    });
                }
            }
            #[cfg(feature = "native-smoke")]
            Event::UserEvent(NativeEvent::SmokeFinished(result)) => {
                if let Err(error) = &result {
                    eprintln!("{error:#}");
                }
                smoke_passed = result.is_ok();
                let _ = reply_proxy.send_event(NativeEvent::Quit);
            }
            Event::UserEvent(NativeEvent::Shell(request)) if !quitting => {
                if let Some(shell) = &mut shell {
                    let _ = reply_proxy.send_event(NativeEvent::Reply(shell.reply(request)));
                }
            }
            Event::UserEvent(NativeEvent::Print(id)) if !quitting => {
                let result = view.print();
                let _ = reply_proxy.send_event(NativeEvent::Reply(Reply {
                    id,
                    result: result.as_ref().ok().map(|_| serde_json::json!(true)),
                    error: result.err().map(|e| e.to_string()),
                }));
            }
            Event::UserEvent(NativeEvent::Menu(event)) if !quitting => {
                let action = event.id.as_ref();
                #[cfg(all(target_os = "windows", feature = "native-smoke"))]
                if action == "preferences" {
                    if let Some((guard, baseline)) = accelerator_trial.take() {
                        drop(guard);
                        if translated.get() <= baseline {
                            let _ = reply_proxy.send_event(NativeEvent::SmokeFinished(Err(anyhow::anyhow!("Preferences bypassed TranslateAcceleratorW"))));
                            return;
                        }
                        eprintln!("Native smoke: verified Preferences through TranslateAcceleratorW");
                        let _ = view.evaluate_script("window.__rustNativeAcceleratorHandled=true");
                    }
                }
                if action == "quit" {
                    let _=reply_proxy.send_event(NativeEvent::Quit);
                } else if welcome && matches!(action, "about" | "preferences" | "updates") {
                    let _ = view.evaluate_script(&format!("window.__rustShellAction({})", serde_json::to_string(action).unwrap()));
                } else if welcome {
                    if matches!(action,"open_archive"|"new_archive") {
                        let method=if action=="open_archive"{"welcome_open"}else{"welcome_new"};
                        let _=reply_proxy.send_event(NativeEvent::Welcome(Request{id:0,method:method.into(),args:vec![]}));
                    } else if let Some(index)=action.strip_prefix("recent-").and_then(|value|value.parse::<usize>().ok()) {
                        if let Some(path)=menu.as_ref().and_then(|menu|menu.recent_path(index)) {let _=reply_proxy.send_event(NativeEvent::SelectArchive(path.to_owned(), false));}
                    }
                } else if !reader_ready || abort.load(std::sync::atomic::Ordering::Acquire) {
                    // Opening owns this window until recovery and validation finish.
                } else if let Some(index)=action.strip_prefix("recent-").and_then(|value|value.parse::<usize>().ok()) {
                    if let Some(path) = menu.as_ref().and_then(|menu|menu.recent_path(index)) {
                        let path = serde_json::to_string(&path.to_string_lossy()).unwrap();
                        let _=view.evaluate_script(&format!("window.pywebview.api.open_recent({path}).catch(e=>window.mailArchiverNotice(e.message))"));
                    }
                } else if matches!(action, "open_archive" | "new_search_window" | "new_archive" | "import_directory" | "open_options" | "open_ingest_window") {
                    let _ = view.evaluate_script(&format!(
                        "window.pywebview.api[{}]().catch(e=>window.mailArchiverNotice(e.message))",
                        serde_json::to_string(action).unwrap()
                    ));
                } else if matches!(action, "about" | "preferences" | "updates") {
                    if let Err(error) = view.evaluate_script(&format!(
                        "window.__rustShellAction({})",
                        serde_json::to_string(action).unwrap()
                    )) {
                        eprintln!("Menu delivery failed: {error}");
                    }
                }
            }
            Event::LoopDestroyed => {
                #[cfg(all(target_os = "windows", feature = "native-smoke"))]
                drop(accelerator_trial.take());
                // Stop WinSparkle callbacks before dropping its DLL and native menu.
                #[cfg(target_os = "windows")]
                accelerators.set((0, 0));
                shell.take();
                menu.take();
            }
            Event::UserEvent(NativeEvent::Capabilities(status)) => {
                if welcome {
                    welcome_writable=status["write_available"] == true;
                    let _=view.evaluate_script(&format!("window.__rustWelcomeCapabilities?.({status})"));
                }
                if let Some(menu) = &menu {
                    menu.capabilities(&status);
                    #[cfg(feature = "native-smoke")]
                    let _ = view.evaluate_script(&format!("window.__rustMenuState={}",menu.state()));
                }
            }
            Event::UserEvent(NativeEvent::Recovering) => {
                let _ = view.evaluate_script("window.__rustOpening?.()");
            }
            Event::UserEvent(NativeEvent::ReaderReady) if !quitting => reader_ready = true,
            Event::UserEvent(NativeEvent::Reply(reply)) => {
                if let Ok(value) = serde_json::to_string(&reply) {
                    if let Err(error) =
                        view.evaluate_script(&format!("window.__rustReply({value})"))
                    {
                        eprintln!("Reply delivery failed: {error}");
                    }
                }
            }
            _ => (),
        }
        let updater = shell.as_ref().map(mailsearch_rust::shell::Shell::updater);
        if let Some(updater) = updater {
            if quitting && worker_finished && exports_cleaned {
                if cleanup_failed && updater.waiting() { updater.cancel_installation("Could not clean temporary exports; update installation canceled."); }
                updater.shutdown_ready(!cleanup_failed);
            }
            if let Some(failure) = updater.take_failure() { update_failure = Some(failure); }
        }
        if quitting && worker_finished && exports_cleaned && update_failure.is_some() {
            // No installer was handed control. Restore the same reader and allow
            // a later ordinary Quit instead of leaving a dead worker or latch.
            let failure = update_failure.take().unwrap();
            quitting = false;
            worker_finished = false;
            quit_timed_out = false;
            exports_cleaned = false;
            closing.store(false, std::sync::atomic::Ordering::Release);
            abort.store(false, std::sync::atomic::Ordering::Release);
            if let Some(updater) = updater { updater.shutdown_ready(false); }
            if !cleanup_failed { mailsearch_rust::drag::resume(); }
            let _ = view.evaluate_script("window.dispatchEvent(new Event('mailarchiver-exports-invalidated'))");
            if !reader_ready {
                welcome = true;
                archive_path = None;
                let _ = view.load_url(if cfg!(windows) { "http://ect.localhost/welcome.html" } else { "ect://localhost/welcome.html" });
            }
            #[cfg(target_os = "macos")]
            mailsearch_rust::macos::finish_termination(false);
            let _ = reply_proxy.send_event(NativeEvent::Notice(format!("Update installation canceled: {failure}")));
        }
        let waiting = updater.is_some_and(|updater| updater.waiting());
        #[cfg(target_os = "macos")]
        if quitting && worker_finished && exports_cleaned && !cleanup_failed && updater.is_some_and(|updater| updater.installing()) {
            mailsearch_rust::macos::finish_termination(true);
        }
        if quitting && (worker_finished || quit_timed_out) && exports_cleaned && !waiting && update_failure.is_none() {
            #[cfg(target_os = "macos")]
            if mailsearch_rust::macos::finish_termination(true) { return; }
            #[cfg(feature = "native-smoke")]
            let failed = cleanup_failed || (smoke_enabled && !smoke_passed);
            #[cfg(not(feature = "native-smoke"))]
            let failed = cleanup_failed;
            *flow = ControlFlow::ExitWithCode(i32::from(failed));
        }
    });
}

#[cfg(test)]
mod tests {
    use super::{trusted_document, trusted_panel, trusted_welcome};

    #[test]
    fn failed_recent_preference_does_not_discard_a_valid_reader() {
        // Open requirement: a real ancillary filesystem failure is a notice,
        // while verified reading remains available and Abort still closes it.
        let directory = tempfile::tempdir().unwrap();
        let archive = directory.path().join("Readable.mailarchive");
        mailsearch_rust::demo::create(&archive).unwrap();
        let blocked = directory.path().join("preferences");
        std::fs::write(&blocked, b"Existing user-owned file").unwrap();
        let settings = blocked.join("recent.json");
        let mut bridge = super::finish_opening(
            super::Bridge::open(&archive).unwrap(),
            &std::sync::atomic::AtomicBool::new(false),
            || mailsearch_rust::documents::Documents::remember(&settings, &archive),
        )
        .unwrap();
        let request = || super::Request {
            id: 2,
            method: "opening_notices".into(),
            args: vec![],
        };
        let notices = bridge.reply(request()).result.unwrap();
        assert_eq!(notices.as_array().unwrap().len(), 1);
        assert!(notices[0]
            .as_str()
            .unwrap()
            .contains("recent-document preference could not be saved"));
        assert_eq!(
            bridge.reply(request()).result.unwrap(),
            serde_json::json!([])
        );
        let reply = bridge.reply(super::Request {
            id: 1,
            method: "search".into(),
            args: vec![serde_json::json!("observatory")],
        });
        assert!(reply.error.is_none());
        assert_eq!(
            reply.result.unwrap()["results"].as_array().unwrap().len(),
            2
        );
        assert_eq!(std::fs::read(blocked).unwrap(), b"Existing user-owned file");
        assert!(super::finish_opening(
            super::Bridge::open(&archive).unwrap(),
            &std::sync::atomic::AtomicBool::new(true),
            || Ok(())
        )
        .is_err());
    }

    #[test]
    fn shutdown_reports_retained_reader_after_opening_finishes_during_quit() {
        // Canceled-update requirement: actual worker state restores a usable reader
        // even when the foreground ignored its queued ReaderReady while quitting.
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("Race.mailarchive");
        mailsearch_rust::demo::create(&path).unwrap();
        let files = ["archive.sqlite3", "search.sqlite3", "data/mbox/DEMO.mbox"];
        let before: Vec<_> = files
            .iter()
            .map(|file| std::fs::read(path.join(file)).unwrap())
            .collect();
        let mut bridge = Some(Ok(super::Bridge::open(&path).unwrap()));
        let (reader_ready, cleanup) = super::quiesce_reader(&mut bridge);
        cleanup.unwrap();
        assert!(
            reader_ready,
            "Canceled installation must restore the retained reader"
        );
        let reply = bridge
            .as_mut()
            .unwrap()
            .as_mut()
            .unwrap()
            .reply(super::Request {
                id: 1,
                method: "search".into(),
                args: vec![serde_json::json!("observatory")],
            });
        assert!(reply.error.is_none());
        assert_eq!(
            reply.result.unwrap()["results"].as_array().unwrap().len(),
            2
        );
        drop(bridge);
        for (file, bytes) in files.iter().zip(before) {
            assert_eq!(std::fs::read(path.join(file)).unwrap(), bytes);
        }
        let mut failed = Some(Err("Opening aborted".into()));
        let (reader_ready, cleanup) = super::quiesce_reader(&mut failed);
        cleanup.unwrap();
        assert!(
            !reader_ready && failed.is_none(),
            "Welcome must allow another opening attempt"
        );
    }

    #[test]
    fn passive_mime_frames_can_load_without_native_ipc_trust() {
        for windows in [false, true] {
            for url in ["about:blank", "about:srcdoc"] {
                assert!(super::allowed_navigation(url, windows));
                assert!(!trusted_document(url, windows));
                assert!(!trusted_welcome(url, windows));
                assert!(!trusted_panel(url, windows));
            }
            for url in [
                "about:blank?evil",
                "about:srcdoc#evil",
                "file:///tmp/mail.html",
                "data:text/html,evil",
                "javascript:alert(1)",
                "https://example.test/mail.html",
            ] {
                assert!(!super::allowed_navigation(url, windows));
            }
        }
    }

    #[test]
    fn welcome_ipc_requires_the_exact_local_page() {
        for (url, windows) in [
            ("ect://localhost/welcome.html", false),
            ("http://ect.localhost/welcome.html", true),
        ] {
            assert!(trusted_welcome(url, windows));
            assert!(!trusted_welcome(url, !windows));
            for suffix in ["?path=/tmp/archive", "#fragment", "/", ".evil"] {
                assert!(!trusted_welcome(&format!("{url}{suffix}"), windows));
            }
        }
        assert!(!trusted_welcome("https://example.test/welcome.html", false));
    }

    #[test]
    fn opening_origin_is_exact_and_separate_from_reader_editors() {
        for windows in [false, true] {
            let origin = if windows {
                "http://ect.localhost/opening.html"
            } else {
                "ect://localhost/opening.html"
            };
            assert!(super::trusted_opening(origin, windows));
            assert!(!super::trusted_opening(origin, !windows));
            assert!(!trusted_document(origin, windows));
            assert!(!trusted_panel(origin, windows));
            for suffix in ["?q=1", "#hash", "/evil", ".evil"] {
                assert!(!super::trusted_opening(
                    &format!("{origin}{suffix}"),
                    windows
                ));
            }
        }
    }

    #[test]
    fn editor_navigation_does_not_grant_direct_ipc() {
        // Editors receive a narrow parent bridge; message and arbitrary frames never do.
        for windows in [false, true] {
            let origin = if windows {
                "http://ect.localhost"
            } else {
                "ect://localhost"
            };
            for path in [
                "/options.html",
                "/ingests.html",
                "/identity.html?kind=name",
                "/identity.html?kind=institution",
            ] {
                let value = format!("{origin}{path}");
                assert!(trusted_panel(&value, windows));
                assert!(!trusted_document(&value, windows));
            }
            for path in [
                "/message.html",
                "/options.html?path=elsewhere",
                "/identity.html?kind=evil",
                "/identity.html#fragment",
                "/app.js",
            ] {
                assert!(!trusted_panel(&format!("{origin}{path}"), windows));
            }
            assert!(!trusted_panel(
                "https://evil.example/identity.html?kind=name",
                windows
            ));
        }
    }

    #[test]
    fn only_the_platform_main_document_can_navigate_or_invoke_ipc() {
        // Native-shell requirement: WebView2's mapped origin is exact, not a
        // host prefix or permission for external pages, frames, or asset paths.
        for windows in [false, true] {
            let trusted = if windows {
                "http://ect.localhost/index.html"
            } else {
                "ect://localhost/index.html"
            };
            assert!(trusted_document(trusted, windows));
            assert!(!trusted_document(trusted, !windows));
            for suffix in ["?query=1", "#fragment", "/child", ".evil"] {
                assert!(!trusted_document(&format!("{trusted}{suffix}"), windows));
            }
            for untrusted in [
                "https://ect.localhost/index.html",
                "http://ect.localhost.evil/index.html",
                "http://ect.localhost:80/index.html",
                "http://evil@ect.localhost/index.html",
                "http://ect.localhost/app.js",
                "ect://localhost/app.js",
                "ect://evil/index.html",
                "file:///index.html",
                "about:blank",
            ] {
                assert!(!trusted_document(untrusted, windows), "{untrusted}");
            }
        }
    }
}
