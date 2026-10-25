<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Customizable Default Lists

Use **File → Customizable Default Lists…** to select a list, edit its YAML,
and save or reset it. The same validated service supplies the GUI and processors.
The editor shows the source, revision and exact application/personal file paths.
Invalid values and stale revisions are rejected; Reload discards unsaved text.
Save before changing lists or scope. Comments and formatting are normalized on save.

## Templates and archive settings

Each list has a bundled YAML template in `src/mailarchiver/` (inside the installed
application for packaged builds). Personal defaults are complete replacements in
`default-lists/<identifier>.yaml` beside the application's `preferences.json`:

- macOS: `~/Library/Application Support/Email Collection Toolkit/default-lists/`.
- Windows: `%APPDATA%/Email Collection Toolkit/default-lists/`.
- Linux: `$XDG_CONFIG_HOME/mailarchiver/default-lists/`, or `~/.config/mailarchiver/default-lists/`.

Existing macOS/Windows settings under the former `Mail Archiver` name retain that
location when selected by application preference discovery. Trust the path shown
in the editor. No additional environment variables are introduced.

**Personal defaults** edits create your own template without modifying the bundled
file. Reset removes the override and restores the current application template.
You may also edit these YAML files directly, or edit bundled files in a source
checkout to change the application template. Installed application updates may
replace bundled files; prefer personal overrides for lasting customization.

**This archive** edits write a snapshot to `processing.sqlite3.processing_settings`.
Processing pins archive-capable lists on first use. Existing snapshots keep their
values when personal defaults or application templates change. Before a snapshot
exists, reading uses the current template without creating a database. The archive
reset button copies the current personal/default template into the archive; it
does not erase history. Editing requires the archive writer lease and is rejected
while another writer owns it. Windows stay bound to the archive that opened them.

Existing archive `config.yaml` owner rules and `contact_filters.yaml` remain
supported and authoritative. The service mirrors their resolved values into the
database when processing, and editor saves update an existing authoritative file
(owner edits create `config.yaml`). Legacy `owner-names.txt` seeds owner rules and
is never rewritten. Contact YAML `mode: extend` still extends the current template.
Back up the entire archive, including these files and `processing.sqlite3`, and
back up personal templates separately. Search-index rebuilding cannot recover edits.

## Available lists

| Identifier | Bundled file | Contents | Scope |
| --- | --- | --- | --- |
| `owner-rules` | `owner_rules.yaml` | Sent-mail owner/sender include and exclude globs | Personal and archive |
| `reply-addresses` | `reply_addresses.yaml` | Provider domains, routing regexes, families and name extraction | Personal and archive |
| `contact-filters` | `contact_filters.yaml` | Mailing-list/service/address exclusions | Personal and archive |
| `provider-domains` | `provider_domains.yaml` | Public providers excluded from institution inference | Personal and archive |
| `entity-rules` | `entity_rules.yaml` | Person/organization name overrides and service mailbox regexes | Personal and archive |
| `display-policy` | `configuration.yaml` | Search-highlight display color | Personal application defaults |
| `message-patterns` | `message_patterns.yaml` | Quoted-message date recovery regexes | Personal application defaults |
| `local-source-rules` | `local_source_rules.yaml` | Local source probe limits and exclusions | Personal application defaults |

Display and local-source settings are application-wide because they are used
before an archive is selected. Quoted-date patterns also apply application-wide.
Reopen affected windows for display edits; use new processing/imports for parsing
and source discovery edits. Existing source bytes and filed messages remain intact.
Archive processing-policy changes affect future tasks without invalidating completed
results. Choose **File → Reprocess Saved Mail…** and confirm to apply them throughout
saved mail; this may take a very long time. Saving alone does not run processing.
Owner edits affect future imports and do not reclassify previously filed messages.

The owner template deliberately starts empty. On startup that needs a new archive,
the app opens this editor if personal owner includes remain empty. Set, for example:

```yaml
include: [alice@example.org, 'alice@*']
exclude: ['alice@lists.example.org']
```

Closing the editor defers setup; import confirmation still presents owner rules.
Explicit archive rules take precedence over personal defaults. See the
[user manual](USER_MANUAL.md#identify-the-archive-owner) for owner glob semantics.

## Reply-address recognition

Rules run in order; the first matching rule wins. Exact domains limit each regex
to its provider, and the regex matches the whole address case-insensitively.
Addresses observed in From, To, Cc, Bcc and Reply-To retain their header role.
The bundled examples cover GitHub reply tokens and numbered Musicmatch responses.
A custom rule can extract a name embedded in a routing address:

```yaml
version: 1
rules:
  - id: example-crm
    domains: [crm.example.org]
    address_pattern: '^(?P<name>[a-z._]+)\+(?P<token>[0-9a-f]+)@crm\.example\.org$'
    headers: [from, reply-to]
    family: 'example-crm:{domain}'
    name_source: capture
    name_group: name
    name_separators: '._'
```

For `Alice.Smith+abc123@crm.example.org`, this yields `Alice Smith`. Alternatively,
`name_source: display-name` uses the header display name, and `fixed` uses a
required `fallback_name`. Display-name rules may supply a fallback for missing
names. `family` can use declared named captures, `{domain}`, `{local}` or `{rule_id}`;
attribute access, conversions and format specifications are rejected. Domains are
exact names, not URL/glob patterns; include subdomains explicitly. IDs are unique.

Matches are stored as `routing-address` evidence with the observed endpoint,
header role, extracted name, family, rule ID, service classification and SHA-256
policy revision. Immutable settings history retains the corresponding policy.
A name can fill an otherwise unnamed automatic identity. Manual names are retained.
Recognizing a routing family does **not** accept a person alias or merge its
endpoints: a CRM or shared service may represent many people. Original messages
remain available for checking attribution.

Entity overrides are evaluated in this order: explicit person name patterns,
explicit organization name patterns, service-local-part patterns, then conservative
SBook classification. Unknown names remain visible. Regular expressions are local
trusted configuration and must be valid Python regexes; avoid expensive ambiguous
patterns. Date-recovery regexes require exactly one captured date.

## Service and verification

`custom_lists.DefaultList` resolves and validates typed Pydantic schemas; the closed
`list_catalog` registry supplies editor descriptors and consumers. Atomic personal
writes use the preferences lock. Archive updates require `WriterLease`, compare
the displayed semantic revision, and preserve policy history under
`customizable-list-history:<identifier>:<sha256>` alongside current
`customizable-list:<identifier>` records. Reads do not create settings files.
New list types register a schema, bundled file, supported scopes and optional
legacy adapter instead of adding another preference-store implementation.

Run `make test-customizable-lists` for schema, precedence, pinning, conflict,
legacy-owner, real-ingest routing and browser-editor tests against isolated data.
