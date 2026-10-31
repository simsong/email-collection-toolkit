<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Email Collection Toolkit

Email Collection Toolkit turns scattered email into a durable archive that you
can inspect with ordinary tools decades from now. It preserves messages in
standard MBOX files inside a native BagIt 1.0 / Mailbag 1.0 package, records
where each message came from, and uses SHA-256 hashes to check integrity.
Its local search index can be discarded and rebuilt.

**This program is under active development. Do not use it operationally until
version 1.0 ships.**

## Desktop application

We build traditional desktop applications for Windows and macOS. Both include
an embedded Python runtime, so you do not need to install Python. The macOS
application also includes compiled Rust-based PST import tools; packaging these
tools for Windows remains unfinished.

The toolkit imports email from local files and downloads email from IMAP servers
without changing the source mail. Messages are stored in MBOX files, typically
two per year: one for messages sent by the archive owner and one for messages
received. Large collections roll over into additional numbered files.

Program updates use Sparkle on macOS and WinSparkle on Windows to check for new
versions and download updates. The embedded ClamAV antivirus engine scans
imported mail; its virus definitions can be updated when the system is online.
See [Versioning and updating](#versioning-and-updating) for update choices.

## Goals

`mailarchiver` is preservation infrastructure for personal and research email
collections, not a mail client or a compliance appliance. Its goals are to:

* harvest email from backup drives and active sources into one persistent,
  deduplicated Mailbag whose canonical MBOX and integrity files can be
  maintained with ordinary, independently implemented tools;
* provide one local search interface across decades of mail, regardless of the
  programs and providers that originally stored it;
* support policy-driven redacted derivatives for confidentiality, donor,
  privacy, and public-release requirements without modifying the canonical
  messages;
* maintain structured, rebuildable metadata suitable for reproducible digital
  humanities reports about correspondents, chronology, threads, attachments,
  entities, and provenance; and
* interoperate with heterogeneous backups, including Outlook `.pst` and `.ost`
  stores, Eudora backups, working IMAP client-cache directories, MBOX, EML,
  Maildir, Apple Mail, Gmail exports, and live read-only IMAP accounts.

“Plain-text archive” describes the standard, inspectable RFC 5322/MBOX
container. Binary attachments remain MIME-encoded so their original bytes are
preserved. Redaction and analysis outputs are derived products: they must be
recreatable and must never silently replace or rewrite canonical mail.

## Current status

Available now:

- Local import from MBOX, Emacs RMAIL Babyl, EML, Maildir, and complete Apple Mail
  `.emlx` messages.
- Outlook PST import through the Rust reader and OST import through a separate
  converter on macOS. These readers report partial extraction and reconstruction
  limits; see [PST/OST reader limits](doc/PST_IMPORTER.md).
- Read-only IMAP downloads on macOS and Windows through the standalone CLI,
  including Gmail accounts that support password-based IMAP access.
- Local search and message viewing, duplicate detection, source provenance,
  integrity verification, and interrupted-import recovery.
- Embedded antivirus scanning and online virus-definition updates.

Still unfinished or planned:

- Packaging the PST/OST import tools for Windows.
- Eudora-specific and general working IMAP-cache adapters, direct Gmail API/OAuth
  acquisition, and Microsoft 365 OAuth acquisition.
- A desktop account-setup interface and scheduled IMAP downloads.
- PDF/Office attachment text extraction, redacted derivatives, richer research
  reports, and archive sorting/repacking.

The [user manual](doc/USER_MANUAL.md) covers importing, verification, search,
and archive care. The [documentation guide](doc/README.md) links to detailed
format support and the roadmap. Name-matching experiments live in
[`research/name-matcher`](research/name-matcher), with their
[research notebook](doc/research/name-matcher/README.md).

## Install

Download the Windows or macOS application from the
[project website](https://simsong.github.io/email-collection-toolkit/) or
[GitHub releases](https://github.com/simsong/email-collection-toolkit/releases).
Current installers support x64 Windows and Apple Silicon macOS. Windows also
requires the Microsoft Edge WebView2 Runtime.

For source installation, build prerequisites, and testing, see
[For developers](doc/FOR_DEVELOPERS.md).

## Apache Tika

We plan to use Apache Tika for optional text extraction from PDF and Office
attachments so their contents can be searched. Extracted text will be derived
data; original messages and attachments will remain preserved.

Tika is not connected to ingest or search yet. Today, `--index-attachments`
indexes text attachments only. Ordinary importing and searching do not require
Java. Developer preparation for Tika is described in
[For developers](doc/FOR_DEVELOPERS.md#apache-tika-preparation).

## CLI and archive workflows

The [CLI reference](doc/CLI_REFERENCE.md) covers local import, IMAP downloads,
message listing/search, processing, verification, reports, and diagnostics.
The [user manual](doc/USER_MANUAL.md) explains the workflows and desktop interface.

### Search matching

Search ignores case and accents and treats `ß`/`ẞ` as `ss`, without stemming.
Existing indexes need no rebuild. Quoted symbols remain significant: `"CACM-"`
requires the hyphen and `"Ham+Sandwhich"` requires the plus. Word boundaries apply.
See [search and message display](doc/CLI_REFERENCE.md#search-and-message-display).

### Standalone IMAP downloads

The IMAP CLI configures an account interactively, synchronizes once, and exits.
Run it again to resume saved progress and fetch new mail. Passwords use the
native macOS or Windows secret store. Downloads leave source messages and flags
unchanged. Gmail downloads use All Mail, excluding messages found only in Spam
or Trash. See [IMAP downloads](doc/CLI_REFERENCE.md#imap-downloads) for account
requirements and commands.

### Archive care

Use **File → Customizable Default Lists…** for owner/sender defaults,
reply-address patterns, and contact filters. See
[Customizable Default Lists](doc/CUSTOMIZABLE_DEFAULT_LISTS.md).

Back up the entire archive. Search indexes can be rebuilt, but manual identities,
tags, configuration, and operational history cannot be recovered from MBOX alone.
Interrupted imports retain completed checkpoints. Investigate integrity failures
before further use or copying.

## Environment variables

Installed applications normally need no environment-variable configuration.
These overrides are available for CLI use and troubleshooting; never put
passwords in environment variables.

| Variable | Purpose |
| --- | --- |
| `MAIL_ARCHIVE_DIR` | Default archive for local ingest/search; explicit `--archive` wins |
| `MAILARCHIVER_CLAMAV_LIBRARY` | Embedded native antivirus engine override |
| `MAILARCHIVER_CLAMAV_DATABASE` | Pinned baseline definition directory; disables automatic refresh |
| `MAILARCHIVER_CLAMAV_UPDATES` | Per-user definition update storage |
| `MAILARCHIVER_FRESHCLAM` | One-shot definition updater executable |
| `MAILARCHIVER_CLAMAV_CERTIFICATES` | Antivirus signature-verification certificates |
| `SSL_CERT_FILE`, `SSL_CERT_DIR` | TLS trust overrides for private IMAP servers |

Legacy `MAILARCHIVER_CLAMD*` variables are unused. Scanning needs no persistent
ClamAV daemon. Build, test, signing, and research settings are listed below for
reference; their workflows are in [For developers](doc/FOR_DEVELOPERS.md).

| Variable / Make parameter | Purpose |
| --- | --- |
| `CARGO`, `RUST_TARGET_DIR`, `CARGO_TARGET_DIR` | Cargo executable and build output; Make uses `RUST_TARGET_DIR`, direct Cargo uses `CARGO_TARGET_DIR` |
| `RUSTUP_TOOLCHAIN` | Override the compiler selected by `rust-toolchain.toml` |
| `CARGO_BUILD_JOBS` | Limit native Rust compilation parallelism |
| `OPENSSL_DIR`, `OPENSSL_LIB_DIR`, `OPENSSL_INCLUDE_DIR` | Select the OpenSSL installation for Rust dependencies |
| `OPENSSL_NO_VENDOR`, `PKG_CONFIG_PATH` | Control vendored OpenSSL and package metadata lookup |
| `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`, `UV_PYTHON_BIN_DIR` | Locations for uv's cache, managed Python, and interpreter links |
| `UV_PROJECT_ENVIRONMENT`, `PYTHONPATH` | Isolated packaging environment and repository-local test import path |
| `FQDN` | Disposable IMAP tests use `localhost` to avoid reverse DNS |
| `MAILARCHIVER_NATIVE_APPLICATION_E2E`, `MAILARCHIVER_NATIVE_GUI_E2E` | Opt into visible native application or GUI tests |
| `GITHUB_ENV`, `RUNNER_TEMP` | GitHub Actions environment handoff file and temporary area |
| `GITHUB_REPOSITORY`, `GH_TOKEN`, `GITHUB_REF_NAME` | Release repository, API authorization, and tag being validated |
| `SIGNING_IDENTITY` | Local macOS Developer ID signing identity |
| `SPARKLE_ED25519_PRIVATE_KEY_BASE64` | Protected key used to sign installers and update feeds |
| `APPLE_CERTIFICATE_P12_BASE64`, `APPLE_CERTIFICATE_PASSWORD` | Protected Developer ID certificate export and its password |
| `APPLE_NOTARY_KEY_ID`, `APPLE_NOTARY_ISSUER_ID`, `APPLE_NOTARY_PRIVATE_KEY_BASE64` | Protected Apple notarization credentials |
| `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` | Windows signing OIDC identity and subscription |
| `AZURE_SIGNING_ENDPOINT`, `AZURE_SIGNING_ACCOUNT`, `AZURE_SIGNING_PROFILE` | Microsoft public signing service selection |
| `MSIX_PUBLISHER`, `PUBLIC_SIGNING` | Approved Windows publisher and workflow signing-mode selector |
| `MSIX_TEST_CERT_PFX_BASE64` | Protected persistent test-signing key; never used for public releases |
| `DEVELOPER_DIR`, `TOOLCHAINS`, `SDKROOT`, `CODESIGN_ALLOCATE` | Apple toolchain overrides; discarded during historical-feed trust checks |
| `__PYVENV_LAUNCHER__`, `PYTHONEXECUTABLE` | Interpreter selection handled internally by the macOS development launcher |
| `DEFINITIONS_DIRECTORY`, `SEED_DIRECTORY` | New definition-download destination and existing baseline to copy |
| `ARGS`, `ARCHIVE`, `IMAP_ARGS` | Makefile command arguments and archive selection; never password channels |
| `RELEASE_TAG`, `APPCAST`, `WINDOWS_ARCHIVE`, `WINDOWS_RELEASE_URL` | Candidate release tag, feed path, and Windows signing inputs |
| `MSIX_PACKAGE`, `MSIX_EVIDENCE`, `MSIX_PAYLOADS` | Windows package, relocation-test evidence, and bundle inputs |
| `TIKA_VERSION` | Optional Tika distribution version |
| `LICENSE_OUTPUT` | Destination for a generated runtime license bundle |
| `NAME_RESEARCH_ENV`, `EVIDENCE`, `OUTPUT`, `DATABASE`, `LABELS` | Research environment, observations, private outputs, frozen experiment, and owner-review labels |

## Versioning and updating

### Version numbers

Versions follow the semantic-versioning pattern **MAJOR.MINOR.PATCH**:

- **MAJOR** changes indicate incompatible changes.
- **MINOR** changes add compatible functionality.
- **PATCH** changes fix defects while preserving compatibility.

During development, a numbered `a` or `b` suffix marks an alpha or beta, such as
`1.0.0a1` or `1.0.0b1`. Higher numbers advance that preview series; beta follows
alpha, and the unsuffixed `1.0.0` is the stable release. Previews are for testing
and may change before the stable release.

### Program updates

Sparkle on macOS and WinSparkle on Windows can check for newer versions,
download them, and offer installation. In Preferences, choose **Release only**
or **Alpha / beta / development and release**. Automatic checks can be enabled
or disabled; manual checks remain available. Program downloads are checked
against their signed update information.

About shows the application version and build commit. See the
[release notes](doc/RELEASE_NOTES.md) for changes. Windows update support is
implemented, but a complete updater-driven Windows upgrade still needs
end-to-end validation.

### Antivirus updates

ClamAV virus definitions update separately from the program. Use **Check for
Anti-Virus Updates** to request an update. Before scanning, definitions more than
24 hours old are automatically refreshed when the update service is reachable.
Offline or failed updates retain the existing definitions. An explicitly pinned
definition directory disables automatic refresh.

## Other resources

Interested in email preservation? Check out:

* [Digital Preservation Coalition](https://www.dpconline.org/) established in 2002 as a collaboration between a number of agencies   operating in the UK and Ireland, building a welcoming and inclusive global community, working together to bring about a sustainable future for our digital assets.
* [code{4{lib](https://code4lib.org/) a volunteer-driven collective of hackers, designers, architects, curators, catalogers, artists and instigators from around the world, who largely work for and with libraries, archives and museums on technology “stuff.”
* [DigiPres.org](https://www.digipres.org/) a gateway to all of the wonderful community-owned and community-oriented resources dedicated to digital preservation!
* [The Digital Library Federation](https://www.diglib.org/), a community of practitioners who advance research, learning, social justice, and the public good through the creative design and wise application of digital library technologies.

For project announcements and feature requests, visit the
[Community Forum](https://github.com/simsong/email-collection-toolkit/discussions).

## Copyright and licenses

Email Collection Toolkit is distributed under the [GNU GPL version 2 only](LICENSE).
Additional licenses are available from the copyright holder.

Original project material is covered by [COPYRIGHT](COPYRIGHT). Vendored and
separately licensed components are identified in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
