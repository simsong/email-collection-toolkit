// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Promote the reader's registered export tokens to real Cocoa file drags.
// Extend this application's Wry view class on its GUI thread before gestures start.
// Preserve WebKit's gesture, drag image and source while replacing export writers.
// Each replacement advertises only public.file-url, never application text or links.
// Tokens resolve through the owning reader's registry, not a dragged pathname.
// Unregistered ordinary WebKit drags pass through unchanged; failed exports abort.
use anyhow::{ensure, Context, Result};
use objc2::{
    msg_send,
    rc::Retained,
    runtime::{AnyClass, AnyObject, Bool, ClassBuilder, ProtocolObject, Sel},
    sel, AnyThread,
};
use objc2_app_kit::{
    NSDraggingItem, NSDraggingSession, NSDraggingSource, NSEvent, NSEventTrackingRunLoopMode,
    NSImage, NSPasteboard, NSPasteboardItem, NSPasteboardNameDrag, NSPasteboardTypeFileURL,
    NSPasteboardTypeString, NSWorkspace,
};
use objc2_foundation::{NSArray, NSPoint, NSRunLoopCommonModes, NSSize, NSString};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    OnceLock,
};
use wry::{WebView, WebViewExtMacOS};

static INSTALLED: AtomicBool = AtomicBool::new(false);
static HOST: OnceLock<&'static AnyClass> = OnceLock::new();
pub fn available() -> bool {
    INSTALLED.load(Ordering::Acquire)
}

fn writer(token: &str) -> Result<Option<(Retained<NSPasteboardItem>, std::path::PathBuf)>> {
    let Some(path) = super::resolve(token) else {
        ensure!(
            !token.starts_with("mailarchiver-export:"),
            "Drag export expired; prepare it again"
        );
        return Ok(None);
    };
    let url = url::Url::from_file_path(&path)
        .map_err(|_| anyhow::anyhow!("Invalid drag export pathname"))?;
    let writer = NSPasteboardItem::new();
    // Cocoa owns the static pasteboard type and the writer copies the URL string.
    ensure!(
        writer.setString_forType(&NSString::from_str(url.as_str()), unsafe {
            NSPasteboardTypeFileURL
        }),
        "Could not prepare file drag writer"
    );
    Ok(Some((writer, path)))
}
fn token(object: &AnyObject) -> Option<String> {
    let selector = sel!(stringForType:);
    if !object.class().responds_to(selector) {
        return None;
    }
    // The selector is an Objective-C pasteboard API returning an optional NSString.
    let string: Option<Retained<NSString>> =
        unsafe { msg_send![object, stringForType: NSPasteboardTypeString] };
    // WebKit may retain the legacy plain-text type on the named drag board.
    let string = string.or_else(|| unsafe {
        msg_send![object, stringForType: &*NSString::from_str("NSStringPboardType")]
    });
    string.map(|value| value.to_string())
}
fn items(
    original: &NSArray<NSDraggingItem>,
    board: Option<&NSPasteboard>,
) -> Result<Retained<NSArray<NSDraggingItem>>> {
    let mut replacements = vec![];
    for item in original {
        let replacement = match token(&item.item())
            .or_else(|| {
                (original.count() == 1)
                    .then(|| board.and_then(|board| token(board)))
                    .flatten()
            })
            .map(|token| writer(&token))
            .transpose()?
            .flatten()
        {
            Some((writer, path)) => {
                let replacement = NSDraggingItem::initWithPasteboardWriter(
                    NSDraggingItem::alloc(),
                    ProtocolObject::from_ref(&*writer),
                );
                let icon = NSWorkspace::sharedWorkspace()
                    .iconForFile(&NSString::from_str(&path.to_string_lossy()));
                // Preserve the live WebKit item's frame and use Cocoa's file icon.
                unsafe {
                    replacement.setDraggingFrame_contents(item.draggingFrame(), Some(&icon));
                }
                replacement
            }
            None => item,
        };
        replacements.push(replacement);
    }
    Ok(NSArray::from_retained_slice(&replacements))
}
unsafe extern "C-unwind" fn begin(
    object: &AnyObject,
    _selector: Sel,
    original: &NSArray<NSDraggingItem>,
    event: &NSEvent,
    source: &ProtocolObject<dyn NSDraggingSource>,
) -> *mut NSDraggingSession {
    let board = unsafe { NSPasteboard::pasteboardWithName(NSPasteboardNameDrag) };
    let value = token(&board);
    let replacements = match items(original, Some(&board)) {
        Ok(items) => items,
        Err(error) => {
            eprintln!("File drag failed: {error}");
            return std::ptr::null_mut();
        }
    };
    // Start lookup above the Wry class extended by install, preserving WebKit's
    // original implementation and return ownership convention without recursion.
    let session = unsafe {
        msg_send![super(object, HOST.get().unwrap().superclass().unwrap()), beginDraggingSessionWithItems: &*replacements, event: event, source: source]
    };
    if let Some(value) = value.filter(|value| super::resolve(value).is_some()) {
        // Modern WebKit restores legacy text AFTER this callback returns. Repair
        // only the same registered drag on the next GUI run-loop iteration,
        // including event-tracking mode while the pointer gesture is active.
        unsafe {
            let modes = NSArray::from_slice(&[NSRunLoopCommonModes, NSEventTrackingRunLoopMode]);
            let _: () = msg_send![object, performSelector: sel!(ectRestoreFileDrag:), withObject: &*NSString::from_str(&value), afterDelay: 0.0f64, inModes: &*modes];
        }
    }
    session
}
fn restore(board: &NSPasteboard, value: &str) -> Result<()> {
    if token(board).as_deref() == Some(value) {
        if let Some((writer, _)) = writer(value)? {
            board.clearContents();
            ensure!(
                board.writeObjects(&NSArray::from_slice(&[ProtocolObject::from_ref(&*writer)])),
                "Could not restore file drag pasteboard"
            );
        }
    }
    Ok(())
}
unsafe extern "C-unwind" fn restore_drag(_object: &AnyObject, _selector: Sel, value: &NSString) {
    let board = unsafe { NSPasteboard::pasteboardWithName(NSPasteboardNameDrag) };
    if let Err(error) = restore(&board, &value.to_string()) {
        eprintln!("File drag failed: {error}");
    }
}
#[allow(clippy::too_many_arguments)]
unsafe extern "C-unwind" fn legacy(
    object: &AnyObject,
    _selector: Sel,
    image: &NSImage,
    point: NSPoint,
    offset: NSSize,
    event: &NSEvent,
    pasteboard: &NSPasteboard,
    source: &AnyObject,
    slide: Bool,
) {
    if let Some(token) = token(pasteboard) {
        match writer(&token) {
            Ok(Some((writer, _))) => {
                pasteboard.clearContents();
                if !pasteboard
                    .writeObjects(&NSArray::from_slice(&[ProtocolObject::from_ref(&*writer)]))
                {
                    return;
                }
            }
            Ok(None) => (),
            Err(error) => {
                eprintln!("File drag failed: {error}");
                return;
            }
        }
    }
    unsafe {
        let _: () = msg_send![super(object, HOST.get().unwrap().superclass().unwrap()), dragImage: image, at: point, offset: offset, event: event, pasteboard: pasteboard, source: source, slideBack: slide];
    }
}
pub fn install(view: &WebView) -> Result<()> {
    let native = view.webview();
    let object: &AnyObject = &native;
    let host = object.class();
    if INSTALLED.load(Ordering::Acquire) {
        ensure!(HOST.get() == Some(&host), "Unexpected WebKit host class");
        return Ok(());
    }
    // Register signatures on an unused helper class, then add only these two
    // methods to Wry. Do not alter a WKWebView's isa or replace inherited IMPs.
    let mut builder =
        ClassBuilder::new(c"ECTFileDragMethods", host).context("Register file drag signatures")?;
    unsafe {
        builder.add_method(
            sel!(beginDraggingSessionWithItems:event:source:),
            begin as unsafe extern "C-unwind" fn(_, _, _, _, _) -> _,
        );
        builder.add_method(
            sel!(dragImage:at:offset:event:pasteboard:source:slideBack:),
            legacy as unsafe extern "C-unwind" fn(_, _, _, _, _, _, _, _, _),
        );
        builder.add_method(
            sel!(ectRestoreFileDrag:),
            restore_drag as unsafe extern "C-unwind" fn(_, _, _),
        );
    }
    let signatures = builder.register();
    HOST.set(host)
        .map_err(|_| anyhow::anyhow!("File drag host already registered"))?;
    for selector in [
        sel!(beginDraggingSessionWithItems:event:source:),
        sel!(dragImage:at:offset:event:pasteboard:source:slideBack:),
        sel!(ectRestoreFileDrag:),
    ] {
        let method = signatures
            .instance_method(selector)
            .context("Missing drag signature")?;
        // GUI-thread initialization: these APIs are inherited by Wry. Adding a
        // compatible override keeps its instance/class metadata and destructor intact.
        ensure!(
            unsafe {
                objc2::ffi::class_addMethod(
                    host as *const AnyClass as *mut AnyClass,
                    selector,
                    method.implementation(),
                    objc2::ffi::method_getTypeEncoding(method),
                )
                .as_bool()
            },
            "Wry already defines this file drag method"
        );
    }
    INSTALLED.store(true, Ordering::Release);
    Ok(())
}

#[cfg(feature = "native-smoke")]
pub fn inspect(value: &str) -> Result<()> {
    // Native acceptance: real pasteboard writers must transfer the registered
    // file URL alone, removing WebKit's string/link representations.
    use objc2_app_kit::NSPasteboardTypeURL;
    use objc2_foundation::{NSPoint, NSRect, NSSize};
    let original = NSPasteboardItem::new();
    unsafe {
        ensure!(
            original.setString_forType(&NSString::from_str(value), NSPasteboardTypeString),
            "Create drag token writer"
        );
        ensure!(
            original.setString_forType(
                &NSString::from_str("https://example.test/"),
                NSPasteboardTypeURL
            ),
            "Create WebKit link representation"
        );
    }
    let item = NSDraggingItem::initWithPasteboardWriter(
        NSDraggingItem::alloc(),
        ProtocolObject::from_ref(&*original),
    );
    let frame = NSRect::new(NSPoint::new(12., 18.), NSSize::new(24., 24.));
    item.setDraggingFrame(frame);
    let replacements = items(&NSArray::from_retained_slice(&[item]), None)?;
    let item = replacements.objectAtIndex(0);
    ensure!(item.draggingFrame() == frame, "Drag frame changed");
    let object = item.item();
    let writer = object
        .downcast_ref::<NSPasteboardItem>()
        .context("Drag writer is not a pasteboard item")?;
    let types = writer.types();
    ensure!(
        types.count() == 1 && &*types.objectAtIndex(0) == unsafe { NSPasteboardTypeFileURL },
        "Drag retained text/link types"
    );
    let url = writer
        .stringForType(unsafe { NSPasteboardTypeFileURL })
        .context("Missing file URL")?;
    ensure!(
        url::Url::parse(&url.to_string())?.to_file_path().ok() == super::resolve(value),
        "Drag URL does not point to the registered file"
    );
    ensure!(
        super::resolve("/etc/passwd").is_none() && super::resolve("file:///etc/passwd").is_none(),
        "Arbitrary path resolved as export"
    );
    // Exercise WebKit's public.data placeholder and its later legacy restoration,
    // on an isolated real Cocoa pasteboard, never the user's clipboard.
    let board = NSPasteboard::pasteboardWithUniqueName();
    let legacy_type = NSString::from_str("NSStringPboardType");
    ensure!(
        board.setString_forType(&NSString::from_str(value), &legacy_type),
        "Create legacy drag token"
    );
    let placeholder = NSPasteboardItem::new();
    ensure!(
        placeholder.setData_forType(
            &objc2_foundation::NSData::new(),
            &NSString::from_str("public.data")
        ),
        "Create WebKit placeholder"
    );
    let item = NSDraggingItem::initWithPasteboardWriter(
        NSDraggingItem::alloc(),
        ProtocolObject::from_ref(&*placeholder),
    );
    item.setDraggingFrame(frame);
    let replacements = items(&NSArray::from_retained_slice(&[item]), Some(&board))?;
    let object = replacements.objectAtIndex(0).item();
    let replacement = object
        .downcast_ref::<NSPasteboardItem>()
        .context("Placeholder was not replaced")?;
    ensure!(
        replacement.stringForType(unsafe { NSPasteboardTypeFileURL }) == Some(url.clone()),
        "Placeholder file URL missing"
    );
    // AppKit clears/populates the board; WebKit then restores its old text.
    board.clearContents();
    ensure!(
        board.setString_forType(&NSString::from_str(value), &legacy_type),
        "Restore WebKit legacy data"
    );
    restore(&board, value)?;
    ensure!(
        board.types().is_some_and(
            |types| types.containsObject(unsafe { NSPasteboardTypeFileURL })
                && !types.containsObject(unsafe { NSPasteboardTypeString })
                && !types.containsObject(&legacy_type)
        ),
        "Restored drag retained text: {:?}",
        board.types().map(|types| types
            .iter()
            .map(|value| value.to_string())
            .collect::<Vec<_>>())
    );
    ensure!(
        board.stringForType(unsafe { NSPasteboardTypeFileURL }) == Some(url),
        "Restored drag URL changed"
    );
    board.clearContents();
    ensure!(
        board.setString_forType(&NSString::from_str("ordinary text"), &legacy_type),
        "Create unrelated drag"
    );
    restore(&board, value)?;
    ensure!(
        token(&board).as_deref() == Some("ordinary text"),
        "Modified a later unrelated drag"
    );
    board.clearContents();
    println!(
        "Native drag export: {}",
        super::resolve(value)
            .context("Drag export disappeared")?
            .display()
    );
    Ok(())
}
