// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Host the existing ECT HTML/CSS/JavaScript interface in a Rust-owned window.
// Embedded application assets are the only files exposed to the webview protocol.
// JSON requests go to one bounded worker; SQLite never runs on the window thread.
// Replies return through the native event loop without waiting on JS callbacks.
// Closing cancels the archive service with an independent five-second exit bound.
// The --rpc mode exercises the same dispatcher headlessly over standard I/O.
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
    match args.as_slice() {
        [] => {
            let recent = mailsearch_rust::documents::Documents::load(
                &mailsearch_rust::documents::Documents::path()?,
            )?;
            if let Some(path) = recent
                .recent
                .first()
                .filter(|path| mailsearch_rust::Archive::open(path).is_ok())
            {
                return native(path.clone(), None, None);
            }
            let decision=rfd::MessageDialog::new().set_title("Email Collection Toolkit")
                .set_description("Create a new archive? Choose Yes to create an empty archive, No to open an existing archive, or Cancel to exit.")
                .set_buttons(rfd::MessageButtons::YesNoCancel).show();
            if decision == rfd::MessageDialogResult::Cancel {
                return Ok(());
            }
            let create = decision == rfd::MessageDialogResult::Yes;
            if let Some(path) = rfd::FileDialog::new()
                .set_title(if create {
                    "Choose empty destination for new archive"
                } else {
                    "Open email archive"
                })
                .pick_folder()
            {
                if create {
                    mailsearch_rust::engine::Engine::open(&path)?.call("create", &[])?;
                }
                native(path, None, None)
            } else {
                Ok(())
            }
        }
        [flag, path] if flag == "--rpc" => {
            let mut bridge = Bridge::open(&PathBuf::from(path))?;
            for line in io::stdin().lock().lines() {
                let request: Request = serde_json::from_str(&line?)?;
                println!("{}", serde_json::to_string(&bridge.reply(request))?);
                io::stdout().flush()?;
            }
            Ok(())
        }
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
        [flag, path] if flag == "--archive" => native(PathBuf::from(path), None, None),
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
            native(PathBuf::from(path), None, Some(parameters.finish()))
        }
        #[cfg(feature = "native-smoke")]
        [flag, path, output] if flag == "--native-smoke" => {
            native(PathBuf::from(path), Some(PathBuf::from(output)), None)
        }
        _ => bail!(
            "Usage: mailsearch-webview --archive DIRECTORY (or --rpc DIRECTORY for headless tests)"
        ),
    }
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn native(
    _path: PathBuf,
    _smoke_output: Option<PathBuf>,
    _parameters: Option<String>,
) -> Result<()> {
    bail!("The native shell is enabled on macOS and Windows; RPC and core tests are portable")
}

#[cfg(any(target_os = "macos", target_os = "windows", test))]
fn trusted_document(url: &str, windows: bool) -> bool {
    url == if windows {
        "http://ect.localhost/index.html"
    } else {
        "ect://localhost/index.html"
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

#[cfg(any(target_os = "macos", target_os = "windows"))]
fn native(path: PathBuf, smoke_output: Option<PathBuf>, parameters: Option<String>) -> Result<()> {
    use mailsearch_rust::bridge::{asset, Reply, SCRIPT};
    use std::{borrow::Cow, sync::mpsc, thread};
    use tao::{
        event::{Event, WindowEvent},
        event_loop::{ControlFlow, EventLoopBuilder},
        window::WindowBuilder,
    };
    use wry::WebViewBuilder;
    enum NativeEvent {
        Reply(Reply),
        Shell(Request),
        Menu(muda::MenuEvent),
        Quit,
        QuitFinished,
        Print(u64),
        #[cfg(feature = "native-smoke")]
        Snapshot,
        #[cfg(feature = "native-smoke")]
        ResizeSmoke,
        #[cfg(feature = "native-smoke")]
        SmokeFinished(Result<()>),
    }
    if smoke_output.is_none() {
        if mailsearch_rust::Archive::open(&path).is_err() {
            mailsearch_rust::engine::Engine::open(&path)?.call("recover", &[])?;
            mailsearch_rust::Archive::open(&path)?;
        }
        mailsearch_rust::documents::Documents::remember(
            &mailsearch_rust::documents::Documents::path()?,
            &path,
        )?;
    }
    let events = EventLoopBuilder::<NativeEvent>::with_user_event().build();
    let window = WindowBuilder::new()
        .with_title(format!(
            "Email Collection Toolkit — {} · Rust",
            path.display()
        ))
        .with_inner_size(tao::dpi::LogicalSize::new(1250.0, 850.0))
        .build(&events)?;
    let mut menu = Some(mailsearch_rust::shell::menu(&window)?);
    let menu_proxy = events.create_proxy();
    muda::MenuEvent::set_event_handler(Some(move |event| {
        let _ = menu_proxy.send_event(NativeEvent::Menu(event));
    }));
    let quit_proxy = events.create_proxy();
    let mut shell = Some(mailsearch_rust::shell::Shell::new(move || {
        let _ = quit_proxy.send_event(NativeEvent::Quit);
    })?);
    let proxy = events.create_proxy();
    let (sender, receiver) = mpsc::sync_channel::<Request>(64);
    thread::Builder::new()
        .name("archive-web-reader".into())
        .spawn(move || {
            let mut bridge = Bridge::open(&path)
                .map(|mut bridge| {
                    bridge.enable_desktop();
                    bridge
                })
                .map_err(|e| format!("{e:#}"));
            while let Ok(request) = receiver.recv() {
                if request.method == "shutdown" {
                    drop(bridge);
                    let _ = proxy.send_event(NativeEvent::QuitFinished);
                    return;
                }
                let reply = match &mut bridge {
                    Ok(bridge) => bridge.reply(request),
                    Err(error) => Reply {
                        id: request.id,
                        result: None,
                        error: Some(error.clone()),
                    },
                };
                if proxy.send_event(NativeEvent::Reply(reply)).is_err() {
                    break;
                }
            }
        })?;
    let shutdown_sender = sender.clone();
    let ipc_proxy = events.create_proxy();
    let diagnostics = std::env::var_os("ECT_RUST_WEBVIEW_DIAGNOSTICS").is_some();
    let smoke_enabled = smoke_output.is_some();
    let window_parameters = format!(
        "window.__rustWindowParameters={};",
        serde_json::to_string(&parameters.unwrap_or_default())?
    );
    let builder = WebViewBuilder::new()
        .with_initialization_script(&window_parameters)
        .with_custom_protocol("ect".into(), |_, request| {
            let (status, mime, bytes) = match asset(request.uri().path()) {
                Some((mime, bytes)) => (200, mime, bytes),
                None => (404, "text/plain", b"Not found".as_slice()),
            };
            wry::http::Response::builder()
                .status(status)
                .header("Content-Type", mime)
                .body(Cow::Borrowed(bytes))
                .unwrap()
        })
        .with_initialization_script(SCRIPT)
        .with_initialization_script(mailsearch_rust::shell::SCRIPT)
        .with_navigation_handler(move |url| {
            let trusted = trusted_document(&url, cfg!(target_os = "windows"))
                || trusted_panel(&url, cfg!(target_os = "windows"));
            if diagnostics {
                eprintln!("Webview navigation: {url:?}, trusted={trusted}");
            }
            trusted
        })
        .with_ipc_handler(move |request| {
            let url = request.uri().to_string();
            let trusted = trusted_document(&url, cfg!(target_os = "windows"));
            if diagnostics {
                eprintln!("Webview IPC: {url:?}, trusted={trusted}");
            }
            if !trusted {
                return;
            }
            if let Ok(message) = serde_json::from_str::<Request>(request.body()) {
                #[cfg(feature = "native-smoke")]
                if smoke_enabled {
                    match message.method.as_str() {
                        "native_smoke_close_ready" => {
                            eprintln!("Native smoke: verified quit during unfinished search");
                            let _ = ipc_proxy.send_event(NativeEvent::Quit);
                            return;
                        }
                        "native_smoke_resize" => {
                            let _ = ipc_proxy.send_event(NativeEvent::ResizeSmoke);
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
        .with_url("ect://localhost/index.html");
    #[cfg(feature = "native-smoke")]
    let builder = if smoke_enabled {
        {
            #[cfg(target_os = "macos")]
            let driver = include_str!("../../native-smoke.js");
            #[cfg(target_os = "windows")]
            let driver = include_str!("../../native-smoke-windows.js");
            builder
                .with_initialization_script(driver)
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
    events.run(move |event, _, flow| {
        *flow = ControlFlow::Wait;
        match event {
            Event::WindowEvent {
                event: WindowEvent::CloseRequested,
                ..
            }
            | Event::UserEvent(NativeEvent::Quit) => {
                if !quitting {
                    quitting=true;
                    let _=shutdown_sender.try_send(Request{id:0,method:"shutdown".into(),args:vec![]});
                    let watchdog=reply_proxy.clone();
                    thread::spawn(move||{thread::sleep(std::time::Duration::from_secs(5));let _=watchdog.send_event(NativeEvent::QuitFinished);});
                }
            }
            Event::UserEvent(NativeEvent::QuitFinished) => {
                *flow = ControlFlow::ExitWithCode(if smoke_enabled { 1 } else { 0 });
            }
            #[cfg(feature = "native-smoke")]
            Event::UserEvent(NativeEvent::ResizeSmoke) => {
                window.set_inner_size(tao::dpi::LogicalSize::new(1100.0, 740.0));
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
                *flow = ControlFlow::ExitWithCode(if result.is_ok() { 0 } else { 1 });
            }
            Event::UserEvent(NativeEvent::Shell(request)) => {
                if let Some(shell) = &mut shell {
                    let _ = reply_proxy.send_event(NativeEvent::Reply(shell.reply(request)));
                }
            }
            Event::UserEvent(NativeEvent::Print(id)) => {
                let result = view.print();
                let _ = reply_proxy.send_event(NativeEvent::Reply(Reply {
                    id,
                    result: result.as_ref().ok().map(|_| serde_json::json!(true)),
                    error: result.err().map(|e| e.to_string()),
                }));
            }
            Event::UserEvent(NativeEvent::Menu(event)) => {
                let action = event.id.as_ref();
                if action == "quit" {
                    let _=reply_proxy.send_event(NativeEvent::Quit);
                } else if let Some(index)=action.strip_prefix("recent-").and_then(|value|value.parse::<usize>().ok()) {
                    let _=view.evaluate_script(&format!("window.pywebview.api.open_recent({index}).catch(e=>window.mailArchiverNotice(e.message))"));
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
                // Stop WinSparkle callbacks before dropping its DLL and native menu.
                shell.take();
                menu.take();
            }
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
    });
}

#[cfg(test)]
mod tests {
    use super::{trusted_document, trusted_panel};

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
