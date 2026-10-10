// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Provide a minimal native Rust search/select/read experiment for ECT archives.
// egui owns only editable fields, results, and the displayed message.
// Every archive operation travels through a single read-only worker channel.
// Busy controls prevent overlapping requests and stale message selections.
// A headless smoke option drives that same worker without opening any windows.
// No Python bridge, browser callbacks, import jobs, or shutdown locks are used.
use anyhow::{bail, Context, Result};
use eframe::egui;
use mailsearch_rust::{
    demo,
    worker::{Content, Request, Worker},
    Message, Row, PAGE_SIZE,
};
use std::{path::PathBuf, time::Duration};

struct Reader {
    worker: Worker,
    path: String,
    query: String,
    rows: Vec<Row>,
    row_labels: Vec<String>,
    selected: Option<i64>,
    message: Option<Message>,
    busy: bool,
    opened: bool,
    opening: bool,
    status: String,
}

impl Reader {
    fn new(worker: Worker, path: String) -> Self {
        let mut app = Self {
            worker,
            path,
            query: String::new(),
            rows: Vec::new(),
            row_labels: Vec::new(),
            selected: None,
            message: None,
            busy: false,
            opened: false,
            opening: false,
            status: "Open an existing archive directory to begin.".into(),
        };
        if !app.path.is_empty() {
            app.open();
        }
        app
    }
    fn submit(&mut self, request: Request) {
        match self.worker.requests.try_send(request) {
            Ok(()) => {
                self.busy = true;
                self.status = "Reading…".into();
            }
            Err(error) => {
                self.status = format!("Reader unavailable: {error}");
            }
        }
    }
    fn open(&mut self) {
        self.opened = false;
        self.opening = true;
        self.rows.clear();
        self.message = None;
        self.selected = None;
        self.query.clear();
        self.submit(Request::Open(PathBuf::from(&self.path)));
    }
    fn receive(&mut self) {
        let received = self.worker.replies.try_recv();
        if matches!(received, Err(std::sync::mpsc::TryRecvError::Disconnected)) {
            self.busy = false;
            self.status = "The archive reader stopped. Restart the experiment to continue.".into();
        }
        if let Ok(reply) = received {
            self.busy = false;
            match reply {
                Ok(Content::Rows(rows)) => {
                    self.opened = true;
                    self.status = if rows.len() == PAGE_SIZE {
                        format!(
                            "Newest {PAGE_SIZE} matches. Narrow the search to see other messages."
                        )
                    } else {
                        format!("{} messages", rows.len())
                    };
                    self.row_labels = rows
                        .iter()
                        .map(|row| {
                            format!(
                                "{}\n{} · {}",
                                if row.subject.is_empty() {
                                    "(No subject)"
                                } else {
                                    &row.subject
                                },
                                row.sender,
                                row.date
                            )
                        })
                        .collect();
                    self.rows = rows;
                }
                Ok(Content::Message(message)) => {
                    self.status = "Message verified against its SHA-256.".into();
                    self.message = Some(message);
                }
                Err(error) => {
                    if self.opening {
                        self.opened = false;
                    }
                    self.status = format!("Unable to read: {error}");
                }
            }
            self.opening = false;
        }
    }
    fn show(&mut self, ui: &mut egui::Ui) {
        self.receive();
        ui.heading("Email Collection Toolkit · Rust experiment");
        ui.label("Read-only archive browser");
        ui.add_enabled_ui(!self.busy, |ui| {
            ui.horizontal(|ui| {
                let label = ui.label("Archive directory");
                let edit = ui
                    .add(egui::TextEdit::singleline(&mut self.path).desired_width(600.0))
                    .labelled_by(label.id);
                if edit.changed() {
                    self.opened = false;
                    self.rows.clear();
                    self.message = None;
                    self.selected = None;
                }
                if ui.button("Open").clicked()
                    || edit.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter))
                {
                    self.open();
                }
            });
        });
        ui.add_enabled_ui(self.opened && !self.busy, |ui| {
            ui.horizontal(|ui| {
                let label = ui.label("Search words");
                let edit = ui
                    .add(
                        egui::TextEdit::singleline(&mut self.query)
                            .hint_text("Words in indexed headers and body")
                            .desired_width(600.0),
                    )
                    .labelled_by(label.id);
                if ui.button("Search").clicked()
                    || edit.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter))
                {
                    self.rows.clear();
                    self.message = None;
                    self.selected = None;
                    self.submit(Request::Search(self.query.clone()));
                }
            });
        });
        ui.horizontal(|ui| {
            if self.busy {
                ui.spinner();
            }
            ui.label(&self.status);
        });
        ui.separator();
        ui.columns(2, |columns| {
            columns[0].heading("Messages");
            let mut selection = None;
            egui::ScrollArea::vertical()
                .id_salt("results")
                .show(&mut columns[0], |ui| {
                    ui.add_enabled_ui(!self.busy, |ui| {
                        for (row, text) in self.rows.iter().zip(&self.row_labels) {
                            if ui
                                .selectable_label(self.selected == Some(row.id), text)
                                .clicked()
                            {
                                selection = Some(row.id);
                            }
                            ui.separator();
                        }
                    });
                });
            if let Some(id) = selection {
                self.selected = Some(id);
                self.message = None;
                self.submit(Request::Select(id));
            }
            columns[1].heading("Message");
            egui::ScrollArea::vertical()
                .id_salt(("message", self.selected))
                .show(&mut columns[1], |ui| {
                    if let Some(message) = &self.message {
                        ui.add(egui::Label::new(&message.headers).selectable(true));
                        ui.separator();
                        ui.add(egui::Label::new(&message.body).selectable(true));
                        ui.separator();
                        ui.small(format!("SHA-256: {}", message.sha256));
                    } else {
                        ui.label("Select a message to read it.");
                    }
                });
        });
    }
}
impl eframe::App for Reader {
    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        if ui.input(|i| i.modifiers.command && i.key_pressed(egui::Key::Q)) {
            ui.ctx().send_viewport_cmd(egui::ViewportCommand::Close);
        }
        egui::CentralPanel::default().show(ui, |ui| self.show(ui));
    }
}

fn main() -> Result<()> {
    let mut args: Vec<String> = std::env::args().skip(1).collect();
    match args.as_slice() {
        [] => "",
        [flag,path] if flag=="--archive" => path.as_str(),
        [flag,path] if flag=="--create-demo" => { demo::create(&PathBuf::from(path))?; println!("Created synthetic reader fixture: {path}"); return Ok(()); }
        [flag,path,query] if flag=="--smoke" => { return smoke(path,query); }
        _ => bail!("Usage: mailsearch-rust [--archive DIRECTORY | --create-demo NEW_DIRECTORY | --smoke DIRECTORY QUERY]"),
    };
    let path = args.pop().unwrap_or_default();
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default().with_inner_size([1150.0, 780.0]),
        ..Default::default()
    };
    eframe::run_native(
        "ECT Rust Reader",
        options,
        Box::new(move |cc| {
            let ctx = cc.egui_ctx.clone();
            Ok(Box::new(Reader::new(
                Worker::start(move || ctx.request_repaint())?,
                path,
            )))
        }),
    )
    .map_err(|e| anyhow::anyhow!("{e}"))
}

fn smoke(path: &str, query: &str) -> Result<()> {
    let worker = Worker::start(|| {})?;
    let ask = |request| -> Result<Content> {
        worker.requests.send(request)?;
        worker
            .replies
            .recv_timeout(Duration::from_secs(10))?
            .map_err(anyhow::Error::msg)
    };
    ask(Request::Open(path.into()))?;
    let Content::Rows(rows) = ask(Request::Search(query.into()))? else {
        bail!("Unexpected response")
    };
    let row = rows.first().context("No matching messages")?;
    let Content::Message(message) = ask(Request::Select(row.id))? else {
        bail!("Unexpected response")
    };
    println!(
        "{} matches; selected {}\n{}\n\n{}\nSHA-256: {}",
        rows.len(),
        row.id,
        message.headers,
        message.body,
        message.sha256
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use egui_kittest::{kittest::Queryable, Harness};
    use std::time::Instant;

    fn settle(harness: &mut Harness<'_, Reader>) {
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            harness.step();
            if !harness.state().busy {
                harness.step();
                return;
            }
            assert!(
                Instant::now() < deadline,
                "UI worker did not finish: {}",
                harness.state().status
            );
            std::thread::sleep(Duration::from_millis(5));
        }
    }

    #[test]
    fn headless_ui_search_click_selection_and_display() {
        // Reader requirement: exercise real widgets and worker, with no native window.
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("demo");
        demo::create(&path).unwrap();
        let app = Reader::new(Worker::start(|| {}).unwrap(), path.display().to_string());
        let mut harness = Harness::builder()
            .with_size([1150.0, 780.0])
            .build_ui_state(|ui, app| app.show(ui), app);
        settle(&mut harness);
        assert_eq!(harness.state().rows.len(), 3);
        harness.get_by_label("Search words").focus();
        harness.step();
        harness.get_by_label("Search words").type_text("roses");
        harness.step();
        assert_eq!(harness.state().query, "roses");
        harness.get_by_label("Search").click();
        harness.run_steps(3);
        settle(&mut harness);
        assert_eq!(harness.state().rows.len(), 1);
        harness
            .get_by_label("Garden update\ncarol@example.test · 2024-01-02T10:00:00Z")
            .click();
        harness.run_steps(3);
        settle(&mut harness);
        assert_eq!(harness.state().selected, Some(3));
        let body = &harness.state().message.as_ref().unwrap().body;
        assert!(body.contains("roses are blooming"));
        harness.get_by_label(body);
        assert!(harness.state().status.contains("verified"));
    }
}
