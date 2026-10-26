<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Windows installer acceptance checklist

Use the candidate's base MSIX bundle, not the CI-only upgrade fixture. Record
its filename, SHA-256, Windows version, and the version/Git commit shown in About.
Use a disposable archive and a test IMAP account; source mail must remain unchanged.
Automated branch CI separately checks native WebView2 rendering, Preferences Close,
real ClamAV scanning, relocated packaging, installation, upgrade and uninstall.
A successful build does not establish these manual checks.

- Install and launch without Python or a checkout. Confirm About shows the new
  version and commit, the application icon/name, and the registered processors.
  Open the processor graph and inspect MIME extraction's possible output types.
- Open Preferences from Help. Change Half CPUs, All CPUs and Custom; close with
  the title-bar X while editing a valid count, reopen and confirm persistence.
  Reject zero, fractions and more than twice the CPUs. For a separate source/CLI
  launch with an explicit worker flag, confirm greyed worker controls and the
  command-line note without overwriting the saved preference.
- Check the preview/release choice and daily-check preference survive restart.
  An unavailable updater must explain why; an available installed backend must
  permit a manual check. Decline any proposed installation for this initial test.
- Import three distinctive messages from a test IMAP inbox. Find and display all
  three. Confirm the server's messages and read/unread flags remain untouched.
  Repeating the same import must not add duplicate archived messages.
- Suspend a larger import; keep the app open and search the saved messages.
  Continue and verify progress resumes past saved UIDs without downloading them
  again. Suspend, Quit, relaunch, and choose Continue Import; then repeat using
  Leave import suspended. The incomplete-work prompt appears once per opened
  search window, not whenever a window regains focus.
- During saved-mail work, check Messages requiring processing, completed tasks,
  and distinct per-worker totals. Messages leave the remaining count after their
  final task. Download/import activity has separate labels and counters; the
  removed Workers/Peak workers cards must stay absent.
- Include an encrypted attachment and confirm the original is archived with an
  unscannable result and the import continues. A definition check/update should
  show startup activity without a temporary red failure. Scanner errors must
  retain mail and continue; an actual positive routes to INFECTED1.mbox.
- Open original folders and verify spaces/brackets display as readable labels.
  Open Names and addresses while importing: show loading feedback before results;
  click an evidence count and confirm the matching messages open. Rename an
  identity, restart, and confirm the manual decision survives.
- Open a second search window for the same archive, close the first, and confirm
  import/search remain responsive. Closing the last importing search window must
  explain that Quit suspends import. Quit must dim windows with Shutting down,
  retain resumable work, and exit without a beach ball or hung process.
- Choose File > Reprocess Saved Mail: cancel the long-duration confirmation and
  verify nothing starts. On a disposable archive, confirm a deliberate reprocess
  works. Ordinary app upgrades must not automatically replay completed work.

Windows uses Ctrl shortcuts and its own title-bar controls. macOS Command-Tab
branding and traffic lights are platform-specific. Existing Windows limitations
remain native Explorer drag-out, bundled PST/OST conversion, and native ARM64
packages (the x64 package can run under emulation). The authoritative matcher is
not connected on either platform; loading/navigation fixes do not enable it.
A complete updater-driven replacement remains a separate acceptance exercise.
