<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Documentation guide

Start with the user manual for workflows and the CLI reference for commands.
Current behavior follows source and current-revision validation; a requirement
or historical test result is not evidence that a feature shipped.

## Users and operators

| Document | Owns |
| --- | --- |
| [USER_MANUAL.md](USER_MANUAL.md) | Desktop workflows, owner rules, search, viewing and archive care |
| [CLI_REFERENCE.md](CLI_REFERENCE.md) | Command names, options, defaults and executable examples |
| [Email Servers.md](Email%20Servers.md) | Acquisition paths and provider-adapter decision |
| [GMAIL.md](GMAIL.md) / [M365.md](M365.md) | Provider-specific acquisition and authorization boundaries |
| [APPLE_MAIL_CACHE.md](APPLE_MAIL_CACHE.md) | Cache safety, completeness and comparison interpretation |
| [RELEASE_NOTES.md](RELEASE_NOTES.md) | Change history; older entries describe their own revisions |

## Architecture and preservation

| Document | Owns |
| --- | --- |
| [requirements.md](requirements.md) | Current contracts by subsystem; future requirements are separate |
| [implementation.md](implementation.md) | Source architecture, storage, recovery and evidence by subsystem |
| [INTEGRITY_CONTROLS.md](INTEGRITY_CONTROLS.md) | Canonical transformations, hashes, manifests and verification |
| [MBOX_READING.md](MBOX_READING.md) | MBOX dialect reading and ambiguity recovery |
| [DATE_HANDLING.md](DATE_HANDLING.md) | Routing-date resolution and fallback order |
| [PLUGINS.md](PLUGINS.md) | Typed provider/processor APIs, dispatch and persistence |
| [MCT_IMPORTER_API.md](MCT_IMPORTER_API.md) | Importer stream and validation contract |
| [PST_IMPORTER.md](PST_IMPORTER.md) | PST/OST helpers, reconstruction limits and importer examples |
| [ON_DISK_MAIL_FORMATS.md](ON_DISK_MAIL_FORMATS.md) | Format/fixture inventory and backend research |
| [EMBEDDED_CLAMAV.md](EMBEDDED_CLAMAV.md) | Scanner isolation, definitions and distribution constraints |

## Development and delivery

| Document | Owns |
| --- | --- |
| [END_TO_END_TESTING.md](END_TO_END_TESTING.md) | Test layers, fixture safety and native acceptance limits |
| [REGRESSION.md](REGRESSION.md) | Corpus comparison, baseline retention and metrics |
| [DEVOPS.md](DEVOPS.md) | Workflow triggers, publication policy and shared feed |
| [MACOS_DISTRIBUTION.md](MACOS_DISTRIBUTION.md) | Python DMG construction and installed acceptance |
| [CERTIFICATE_MANAGEMENT.md](CERTIFICATE_MANAGEMENT.md) | Developer ID/notarization credential lifecycle |
| [WINDOWS.md](WINDOWS.md) | Native Windows development prerequisites and limitations |
| [WINDOWS_MSIX_TEST.md](WINDOWS_MSIX_TEST.md) | Python MSIX structure, signing modes and installed acceptance |
| [WINDOWS_PUBLIC_SIGNING.md](WINDOWS_PUBLIC_SIGNING.md) | Azure/OIDC/public publisher provisioning |
| [SPARKLE_UPDATES.md](SPARKLE_UPDATES.md) | Python update lifecycle and remaining acceptance |

## Plans, research and history

| Document | Status |
| --- | --- |
| [plans/README.md](plans/README.md) | Future acquisition, archive extensions, Contacts/geography and OCR requirements |
| [project_direction.md](project_direction.md) | Current strategy and priorities; feature plans are indexed separately |
| [Name Resolution Record Linkage.md](Name%20Resolution%20Record%20Linkage.md) | Name-resolution research design |
| [research/name-matcher/README.md](research/name-matcher/README.md) | Notebook, experiments, literature and aggregate run log |
| [RUST_GUI_EXPERIMENTS.md](RUST_GUI_EXPERIMENTS.md) | Single retired GUI retrospective |
| [history/README.md](history/README.md) | Dated audits, reconciliation, ecosystem research and superseded release scope |
| [DATA_QUALITY_AUDIT.md](DATA_QUALITY_AUDIT.md) | Current diagnostic tooling; investigation results are archived |
| [PYTHON_WEBVIEW.md](PYTHON_WEBVIEW.md) | Pointer from the integrated comparison experiment to current guides |
| [OAUTH_CLIENT_REGISTRATION.md](OAUTH_CLIENT_REGISTRATION.md) | Absence of a current Google registration/client |

`end-to-end-tests.md` forwards to the maintained testing guide. Historical source
snapshots remain recoverable through Git; obsolete runbooks are not alternative
current instructions.

## Maintaining the structure

- Keep current contracts under `requirements/` and their source/evidence maps
  under `implementation/`. Both overviews remain stable entry points.
- Put unimplemented feature requirements in `plans/`. Keep an implemented
  feature's missing native/release acceptance beside its current implementation.
- Preserve dated audits and superseded decisions in `history/`, including the
  revision and original validation limits. Update incoming links when moving files.
- Keep command syntax in `CLI_REFERENCE.md`, except substantive test recipes and
  specialist importer examples. Link to the owning contract instead of copying it.

A behavior change updates the corresponding contract and implementation together.
New acceptance evidence records the revision and platform; it does not silently
replace an old audit's findings or become a second current feature inventory.
