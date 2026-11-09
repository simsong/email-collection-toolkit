<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Documentation

Choose the guide for the task you want to perform. User instructions describe
shipped behavior; developer references explain implementation and validation.
Plans and dated investigations are separate from instructions to run today.

## Use the toolkit

- [User manual](USER_MANUAL.md): install and use the desktop, import mail,
  search, view messages and care for an archive.
- [Command-line guide](CLI_REFERENCE.md): install the CLI, create an archive,
  import a directory or IMAP account, search and verify.
- [Gmail](GMAIL.md), [Microsoft 365](M365.md), and [Apple Mail caches](APPLE_MAIL_CACHE.md):
  supported acquisition paths and their completeness limits.
- [Customizable lists](CUSTOMIZABLE_DEFAULT_LISTS.md): owner rules and contact filters.
- [Release notes](RELEASE_NOTES.md): version history.

## Develop and maintain the toolkit

- [For developers](FOR_DEVELOPERS.md): source setup, builds, tests and platform prerequisites.
- [Developer commands](DEVELOPER_COMMANDS.md): Make targets, specialist tools,
  signing and release recipes.
- [Testing](END_TO_END_TESTING.md) and [regression policy](REGRESSION.md): fixture
  safety, acceptance layers and corpus comparisons.
- [DevOps](DEVOPS.md): CI, release and website workflow triggers and gates.
- [macOS distribution](MACOS_DISTRIBUTION.md) and [certificates](CERTIFICATE_MANAGEMENT.md).
- [Windows development](WINDOWS.md), [MSIX packaging](WINDOWS_MSIX_TEST.md),
  [public signing](WINDOWS_PUBLIC_SIGNING.md), and [application updates](SPARKLE_UPDATES.md).
- [Antivirus](EMBEDDED_CLAMAV.md), [plugins](PLUGINS.md), and [data-quality tools](DATA_QUALITY_AUDIT.md).

## Understand the archive and implementation

- [Requirements](requirements.md) and [implementation](implementation.md):
  subsystem indexes and shared invariants.
- [Integrity format](INTEGRITY_CONTROLS.md): canonical message transformations,
  hashes, manifests and independent verification.
- [MBOX handling](MBOX_READING.md) and [date handling](DATE_HANDLING.md).
- [PST/OST importers](PST_IMPORTER.md) and [importer API](MCT_IMPORTER_API.md).
- [Threading](THREADING.md), [timing audit](TIMING_AUDIT.md), and [toolkit audit](TOOLKIT_AUDIT.md):
  implementation reviews and their validation boundaries.

## Plans, research and history

- [Plans](plans/README.md): unimplemented acquisition, archive, Contacts and OCR extensions.
- [Name-linkage research](research/name-matcher/README.md) and
  [research design](Name%20Resolution%20Record%20Linkage.md): methods and experiments.
- [Format/backend research](ON_DISK_MAIL_FORMATS.md) and
  [historical project direction](project_direction.md): research snapshots, not current feature inventories.
- [History](history/README.md): dated audits, comparisons and superseded decisions.
- [Retired Rust GUI](RUST_GUI_EXPERIMENTS.md): archive pointers only; maintained
  Rust importer and verification tools remain supported.

Keep each command's syntax in the command-line guide and use links from workflow
and specialist documents. Build/test recipes belong in developer documentation.
Behavior changes update the owning requirement, implementation and validation
records together. Historical evidence must retain its date and scope.
