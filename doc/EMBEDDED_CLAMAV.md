<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Embedded antivirus and offline definitions

Status: local changes for PR #124. Python/Rust library scanning, infected-only
API headers, definition refresh, About freshness reporting, and DMG bundling
are implemented. Distribution licensing remains unresolved; see below.

## Licensing and distribution

The application is distributed under GPLv3, with additional licenses available
from the copyright holder; see COPYRIGHT and LICENSE. The runtime audit permits
GPL dependencies while retaining dependency and notice checks.

The installed ClamAV 1.5.4 header grants GPLv2 only, and
[upstream tracks GPLv3 dual licensing](https://github.com/Cisco-Talos/clamav/issues/1337)
as a separate request. GPLv3 application licensing does not by itself resolve
direct-link distribution. A separately GPLv2-compatible native helper or
suitable upstream permission is needed before distributing this prototype.
Preserve the exact native dependency licenses, notices and corresponding
source arrangements. Do not describe the prototype as approved for release.

## Engine and API ownership

The prototype uses Python ctypes and Rust libloading bindings to the public
libclamav 1.x ABI, checked against the installed 1.5.4 header. Future ABI changes
require binding review. Package the selected engine and its dependency closure.

One compiled Python engine is shared by concurrent native scan threads;
ctypes releases the GIL during calls. Per-call options and verdict storage are
independent. Short IPC locks never surround a scan. An app-owned temporary
worker process permits termination of stuck native calls and contains crashes;
there is no resident daemon, socket, login service, or on-access scanner.
Native extraction files live in an owned directory removed after worker exit.
Host source workers scan outside the canonical publication lock. MBOX writes
remain serialized. The production timeout is 60 seconds; startup allows 120.

API producers scan messages themselves. Rust scans the reconstructed RFC message
before emission. Only infected messages receive X-ClamAV-Detection,
X-ClamAV-Engine-Version, and X-ClamAV-Definitions-Version headers. Python consumes
those headers and routes infected messages to INFECTED without scanning again.
Clean messages carry no scan provenance. The producer does not compute message
hashes or write scan results to a database. Python owns hashing, deduplication,
and existing processing-result storage; no scan-receipt schema is needed.

API failures are reported to the operator. Re-import the stream and use normal
deduplication; no automatic API restart is added. Explicit no-scan imports retain
their existing visible policy. Rust still preserves its source-level PST
integrity checks and importer diagnostics; these are separate from message scanning.

## Definitions and platform paths

| Platform | Intended bundled baseline | Per-user updates |
| --- | --- | --- |
| macOS | Email Collection Toolkit.app/Contents/Resources/clamav/definitions/ | ~/Library/Application Support/Email Collection Toolkit/clamav/ |
| Windows | installation/resources/clamav/definitions/ | %LOCALAPPDATA%\Email Collection Toolkit\clamav\ |

Windows definitions are local machine data, not roaming APPDATA preferences.
If a future macOS build enables App Sandbox, use its container's Application
Support directory, normally under
~/Library/Containers/net.simson.mailarchiver/Data/. A normal DMG application is
not automatically App Sandboxed merely because it uses Application Support.

The DMG bundle contains the engine, one-shot FreshClam updater, main,
daily and bytecode definition archives, and signature trust material required
by that engine. Builds must verify authenticity, compatibility, hashes and
successful scanning. Keep large definition artifacts in the build cache,
outside Git history. The builder uses the project copy in etc/clamdb.

Run make freshclam to seed etc/clamdb from the installed database when available
and update it in place. On Apple Silicon Homebrew, the seed is normally
/opt/homebrew/var/lib/clamav. Copy the .cvd/.cld files and .sign files together.
Release CI runs make freshclam before make dmg; ordinary local DMG builds use
the existing project copy and do not require a network refresh.

About includes Update virus definitions, also exposed through make clamav-update.
Updates use private configuration and independent staging copies. FreshClam
handles downloads, incremental patches and server cooldown state; no mail is
uploaded. An OS-owned lock serializes update attempts. Native clean/EICAR scans
validate a candidate before an atomic manifest selects an immutable generation.
Failed validation/download leaves the active manifest intact. Previous generations
are retained because an existing import may still use them. The bundle is never
modified. A newer bundled daily database takes precedence over an older update.

Development overrides are MAILARCHIVER_CLAMAV_LIBRARY,
MAILARCHIVER_CLAMAV_DATABASE, MAILARCHIVER_CLAMAV_CERTIFICATES,
MAILARCHIVER_FRESHCLAM and MAILARCHIVER_CLAMAV_UPDATES. Legacy CLAMD settings are
not used. A frozen application resolves resources within its wrapper.

## About and releases

Show the engine version, daily definition publication date, age, component
versions and source (bundled or updated). The daily database header is the
freshness authority; copying files or updating the older main database must not
reset age. Unknown or future dates are explicitly reported.

After the publication timestamp plus three calendar months, display a readable
yellow notice recommending Update virus definitions or downloading a newer app.
Clamp the day at month-end. At the exact threshold there is no age warning;
after it there is. Old definitions continue to work offline.

Release a refreshed application at least quarterly. Refresh and verify bundled
definitions during release preparation; urgent engine fixes may require earlier
releases. Users can request definition updates between application releases.
No release or update automation is scheduled by this change.

## Validation boundaries

Source tests exercise real clean/EICAR and encoded MIME scans, concurrent
requests, infected-only API headers, deadlines/crashes, corrupted-definition rejection,
calendar arithmetic and producer-evidence routing. Real Rust PST CLI tests
verify that only producer scans are used and source bytes remain unchanged.

The About page was rendered and inspected in light and dark themes; its real
update service was tested with an unavailable updater, preserving definitions.

The mounted DMG self-test exercises bundled clean/EICAR scanning with a disposable
update directory and a system-only PATH. Native dependency checks reject external
Homebrew linkage. Still required before release: resolve the license boundary and
complete native license/source collection. Test Windows separately; macOS tests
do not establish Windows packaging.
