// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Host the existing ECT HTML/CSS/JavaScript interface in a Rust-owned window.
// Embedded application assets are the only files exposed to the webview protocol.
// JSON requests go to one bounded worker; SQLite never runs on the window thread.
// Replies return through the native event loop without waiting on JS callbacks.
// Closing exits the event loop without joining any read-only worker operation.
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

fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.as_slice() {
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
        [flag, path] if flag == "--archive" => native(PathBuf::from(path), None),
        #[cfg(feature = "native-smoke")]
        [flag, path, output] if flag == "--native-smoke" => {
            native(PathBuf::from(path), Some(PathBuf::from(output)))
        }
        _ => bail!(
            "Usage: mailsearch-webview --archive DIRECTORY (or --rpc DIRECTORY for headless tests)"
        ),
    }
}

#[cfg(not(target_os = "macos"))]
fn native(_path: PathBuf, _smoke_output: Option<PathBuf>) -> Result<()> {
    bail!("The initial native shell is enabled on macOS only; RPC and core tests are portable")
}

#[cfg(target_os = "macos")]
fn native(path: PathBuf, smoke_output: Option<PathBuf>) -> Result<()> {
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
        Quit,
        #[cfg(feature = "native-smoke")]
        Snapshot,
        #[cfg(feature = "native-smoke")]
        SmokeFinished(Result<()>),
    }
    let events = EventLoopBuilder::<NativeEvent>::with_user_event().build();
    let window = WindowBuilder::new()
        .with_title(format!(
            "Email Collection Toolkit — {} · Rust",
            path.display()
        ))
        .with_inner_size(tao::dpi::LogicalSize::new(1250.0, 850.0))
        .build(&events)?;
    let proxy = events.create_proxy();
    let (sender, receiver) = mpsc::sync_channel::<Request>(64);
    thread::Builder::new()
        .name("archive-web-reader".into())
        .spawn(move || {
            let mut bridge = Bridge::open(&path).map_err(|e| format!("{e:#}"));
            while let Ok(request) = receiver.recv() {
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
    let ipc_proxy = events.create_proxy();
    let smoke_enabled = smoke_output.is_some();
    let builder = WebViewBuilder::new()
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
        .with_navigation_handler(|url| url == "ect://localhost/index.html")
        .with_ipc_handler(move |request| {
            if request.uri() != "ect://localhost/index.html" {
                return;
            }
            if let Ok(message) = serde_json::from_str::<Request>(request.body()) {
                #[cfg(feature = "native-smoke")]
                if smoke_enabled {
                    match message.method.as_str() {
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
        builder.with_initialization_script(include_str!("../../native-smoke.js"))
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
    events.run(move |event, _, flow| {
        *flow = ControlFlow::Wait;
        match event {
            Event::WindowEvent {
                event: WindowEvent::CloseRequested,
                ..
            }
            | Event::UserEvent(NativeEvent::Quit) => {
                *flow = ControlFlow::ExitWithCode(if smoke_enabled { 1 } else { 0 })
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
