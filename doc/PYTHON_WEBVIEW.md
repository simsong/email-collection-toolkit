<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Python webview comparison

This branch starts from main `251b77a` and selectively reuses reader and MSIX
work from `work-rust-gui` at `2bbedc3`. It is a separate experiment, not a merge
or replacement of PR #153. Existing linked worktrees and their edits are preserved.

## Scope

Both platforms use `gui_app.py`, the existing document controller, Python search,
completion, MIME rendering, exports, and shared HTML/CSS/JavaScript. macOS retains
its Python packaging and native integration. Windows uses x64 CPython, pythonnet,
WinForms and WebView2; ARM64 is not a package target. WebView2 remains external.

Windows adds a reader welcome page, Open/Recent/Close/Exit and About menus,
Ctrl+O/Ctrl+W, Ctrl-based message selection/find, Unicode clipboard copying,
and explicit attachment/URL opening. The standard Windows Open dialog selects a file inside a collection;
the shared controller opens its enclosing archive. New/Import, processing and identity controls now use the
common engine with Windows directory handles/locks and byte-preserving MBOX I/O.
Windows native file drag remains unimplemented. WinSparkle now uses the shared
update service, signed-feed verification, Python package filtering and writer
reservation. Source checks are manual and cannot launch installers.
Save Message and Save Attachment provide explicit exports.

The reused Rust-branch Python changes cover canonical-hash attachment tags,
organization-name matching, saved-filter process locking, and deferred POSIX
configuration imports. No Rust GUI, search implementation, or worker protocol
is included. The existing Rust importer tools on main remain independent.

## Build and checks

Use Python 3.12+ and locked dependencies through uv. The Windows package builder
selects x64 CPython 3.12 and a separate runtime-only venv. On Windows, put GNU Make
and Git's `usr/bin` on the process PATH. With the GnuWin32 installation under
Program Files, invoke `make MAKE=make 'SHELL=C:/Program Files/Git/usr/bin/bash.exe'`
before the following targets; this avoids its unquoted recursive executable path.

- `check-python-reader-static`: repository-wide Ruff, then Pylint, ty and Pyright
  on all changed Python reader files, in that order.
- `test-python-reader`: synthetic reader preservation, whole-collection paging
  beyond 2,000 results, concurrent saved-filter writers, existing organization
  names and canonical-hash attachment tags.
- `test-python-reader-native`: native search/HTML-find promise bridge, normal
  application windows, six sort orders, Windows clipboard/menu checks and a
  screenshot. Every collection file is hashed before and after operations.
- `test-python-writer`: real creation, import, stop/resume, writer locking and
  configuration checks. Windows symlink cases require the OS symlink privilege.
- `test-updater-supervisor`: real command diagnostics and timeout containment.
- `test-windows-scanner`: actual isolated DLL loading, concurrent clean/EICAR
  verdicts, input preservation, deadlines and owner-death cleanup.
- `test-windows-updater`: signed metadata/filtering and actual SDK initialization.
- `check-windows-update-feed`: real SDK discovery through the signed public feed.
- `prepare-windows-updater`: checksum-pinned WinSparkle DLL and license staging.
- `prepare-windows-clamav`: explicitly download the checksum-pinned upstream
  x64 portable runtime into `.tmp/clamav-x64`, without installing a service.
  `freshclam` obtains and validates baseline definitions before MSIX assembly.
- `msix-test MSIX_ARGS='-CreateUpgradeTest'`: build new base and upgrade fixtures.
- `test-msix MSIX_PACKAGE=... MSIX_EVIDENCE=...`: extract into a new directory and
  exercise its isolated private interpreter and native application. The deliberately
  invalid `PYTHONPATH` confirms independence from a developer checkout.
- `msix-bundle MSIX_PAYLOADS=...`: bundle and sign payloads under `x64/`, including
  the separate `x64/upgrade/` fixture. Never distribute the upgrade fixture.

The package identity is `ECT.PythonReader`, distinct from the Rust test package.
Start-menu activation uses private `pythonw.exe -I -m mailarchiver.desktop_entry`.
The private `_pth` file excludes user packages and checkout imports. GUI resources
are bundled with the private runtime. Versions come from the shared release
mapper, not constants in packaging. The persistent public certificate and
`MSIX_TEST_CERT_PFX_BASE64` signing contract are reused without generating a key.
The installer README and certificate helper are preserved with x64 instructions.

`.github/workflows/python-reader.yml` prepares x64 payload/relocation checks,
pinned-key signing, registered Start-menu activation, upgrade/uninstall, and a
separate macOS native-reader check. Dispatch/reusable calls or an explicit
`[msix-ci]`/`[release-ci]` branch commit opt into Windows packaging. No workflow,
release, certificate installation or package installation has been performed
by this local implementation task.

## Local evidence and remaining gates

Windows evidence lives under `.tmp/native-reader-3/` and
`dist/candidate-validation/`. The candidate is under `dist/python-reader-candidate/`.
Seven reader tests passed. Source and relocated native checks passed, including
unchanged collection-file inventories. The captured Windows reader was visually
inspected. These small synthetic checks are not a large-archive performance
benchmark or proof of physical keyboard/drag behavior.

`make ruff` passed with zero diagnostics. The focused reader Pylint, ty and
Pyright checks passed with zero diagnostics. Full `make check` stops at five
Windows Pylint errors for POSIX-only `fcntl`/`termios` imports in unchanged writer
code and tests. Full ty/Pyright also report POSIX signal/OS API and scanner pipe
types unavailable on Windows. No exclusions, rule suppressions, or Windows writer
substitutions were added to hide those failures.

`make website-screenshots` stops at the intentionally disabled Windows writer;
the canonical website images still need regeneration and homepage/Searching
inspection on macOS. Native macOS checks and the DMG build have not run here.
The MSIX is unsigned: the pinned private key is unavailable in this process and
the local certificate store. Its prepared signing/install workflow still needs
an authorized publication/run. Prior Rust MSIX evidence does not validate this
Python package's installed activation, signature, upgrade or uninstall.

## Repairs following Windows use

The initial build omitted ClamAV and exposed a broken update action. Native
resource discovery and job-object supervision are implemented; downloading the
runtime and real clean/EICAR/definition-update tests remain pending approval.
The builder now fails if the runtime or baseline definitions are absent instead
of emitting another scanner-free package. Earlier MSIX evidence predates these
repairs and must not be treated as acceptance of the current source.

The custom archive picker previously tested in `.tmp/native-picker-fix` and
`.tmp/native-import-enabled` has been removed in favor of the standard system
Open dialog. Those results do not validate the replacement dialog. Regression
tests cover archive-member selection and unchanged collection bytes; physical
selection/cancellation in the replacement system dialog requires user acceptance. Creation/import preservation covers CRLF, missing dates with the
existing year-directory fallback, invalid UTF-8, malformed MIME, duplicate IDs,
autosave exclusion, source idempotence and full archive fixity. The existing
600-message stop/resume test also passed on Windows. The full writer target now
reports 42 passes, five failures because Windows denies symlink creation
(WinError 1314), and one existing filesystem-spelling skip. No failures are
suppressed. The broader MBOX/source/recovery suite passes all 63 tests after
adapting derived exports and fixed source fixtures to preserve bytes on Windows.
Changed-source Ruff, Pylint, ty and Pyright pass without diagnostics.

`.tmp/native-import-acceptance` additionally validates the real desktop's New/
Import menu entries, window creation, shared GUI import service, archive fixity,
and unchanged synthetic source bytes. It does not automate the native Save
destination dialog or owner-rule confirmation. The repaired app was reopened
from source; earlier unsigned MSIX files remain unchanged and out of date.

The storage adapter retains file flushing and uses Windows write-through rename;
Windows has no POSIX directory fsync. Power-loss durability is not established
by process interruption tests. Native New/Import dialog acceptance, real scanner
acceptance, current-package relocation/install and macOS checks remain open.
The latest `website-screenshots` attempt reaches ingestion but stops because
ClamAV definitions are absent; website images and page inspection remain pending.

Standard Open repair: `make check-python-reader-static test-python-reader`
passes Ruff, Pylint, ty, Pyright and all ten reader regression cases. The tests
select root and nested members in suffixed and legacy archives, reject unrelated
files and damaged nearer archives, and compare collection bytes before/after.
`make ruff test-python-reader-native ARGS="--output .tmp/native-standard-open"`
also passes, including opening `archive.sqlite3` through the real desktop host,
rendering/search/export and synthetic creation/import. This validates the Python
selection handler; it does not automate the standard shell dialog itself.

Mapped-drive repair: `sqlite_paths.sqlite_uri` keeps UNC server names in the
filename path rather than the URI authority and escapes reserved characters.
All GUI database validation, queries and attachments use the shared helper.
`make check-archive-open` with the user-selected mapped-drive archive passes
selection resolution for the archive root, BagIt file and both databases, schema checks,
search and verified message rendering through both mapped and canonical paths.
This diagnostic is read-only and does not ingest or recover database journals.
The app was relaunched through the archive's `bagit.txt`; its responsive reader
window displays the collection's message count. Diagnostics print no message contents.
Fourteen focused reader/URI tests passed; they also reject writes through
read-only database connections and attachments and check reserved path characters.
Older search/GUI fixtures now use the byte-preserving MBOX writer on Windows;
rendered CLI text expects host line endings, while MIME assertions remain exact.

The search suite passes all 84 cases. The GUI/completion run now passes all 77
cases after fixing Windows menus, preserving source-machine provenance paths
and serializing concurrent export replacement. Native file dragging remains
unimplemented.

Native Windows reader acceptance also passes in `.tmp/native-network-paths`,
including archive-member opening, rendering/search/export and synthetic import.

Antivirus/update acceptance: real ClamAV clean/EICAR checks initialize a verified
generation in application-specific user storage outside archives. A private
worker prevents the Python/ClamAV OpenSSL DLL collision. Memory-based EICAR health
checks avoid host Defender quarantine without changing Defender configuration.
The CDN currently refuses downloads until its cooldown expires; that state is
preserved, and a local baseline was copied and validated without source writes.
WinSparkle's real public-feed check succeeds; package identity filtering excludes
the historical Rust installer. A signed installed Python upgrade remains pending.

An unsigned MSIX containing both native runtimes passes isolated native reader
and scanned-import acceptance after removing redundant build copies to free
ClamAV temporary space. Low disk caused the first packaged scan to fail; the
unchanged payload passes with sufficient space. Later source validation also
checks completed import status and retries transient Windows reader sharing
conflicts during atomic publication. Final packaging/signing CI must include
those subsequent source fixes. Updated synthetic website images were generated
with installed Edge and visually inspected; built website page inspection still
needs Zola. The complete cross-platform CI and macOS native acceptance remain
required before handoff.
