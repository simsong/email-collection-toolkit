<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Third-party and separately licensed material

Email Collection Toolkit retains the notices and license terms of material that is not
covered by the repository's `COPYRIGHT` notice. A packaged application must
include this file, `COPYRIGHT`, `LICENSE` (GPLv2), and the complete license texts collected from
the exact runtime environment used to build that application.

## Material stored in this repository

| Component | Location | License and notice |
| --- | --- | --- |
| Tabulator 6.5.2 | `gui/vendor/tabulator/` | MIT; Copyright (c) 2015-2026 Oli Folkerd. The complete upstream text is in `gui/vendor/tabulator/LICENSE`. |
| Envelope Rainbow website theme | `website/themes/envelope-rainbow/` | Project-owned, GPL-2.0-only; Copyright (c) 2026 Simson L. Garfinkel. Covered by `COPYRIGHT`; the complete text is also in `website/themes/envelope-rainbow/LICENSE`. |
| libpff-python 20231205 | Independent `converters/pff` executable only | LGPL-3.0-or-later; Joachim Metz. The GPLv3 converter loads this extension; the GPLv2 application does not. Complete upstream texts are in `converters/pff/libpff-LGPL.txt` because the wheel omits them. Source: https://github.com/libyal/libpff and https://pypi.org/project/libpff-python/20231205/. |
| Public Suffix List snapshot | `src/mailarchiver/public_suffix_list.dat` | MPL-2.0; unchanged 2025-04-07 snapshot formerly supplied by tldextract 5.3.2. Upstream notices remain in the file; full license in `licenses/publicsuffix-MPL-2.0.txt`. Source: https://publicsuffix.org/list/public_suffix_list.dat. |
| proxy_tools 0.1.0 | Runtime dependency | BSD; Copyright (c) 2013 Armin Ronacher and Copyright (c) 2014 Jonathan Tushman. Its wheel metadata incorrectly says MIT and omits the upstream license file, so the reviewed upstream text is retained in `licenses/proxy_tools-BSD.txt`. |

The Tabulator directory is vendored and must remain byte-for-byte identical to
the reviewed upstream files. Minified files are never rewritten to add project
headers.

The upstream website illustration and photograph retain their separate terms
and attribution in `website/static/images/ATTRIBUTION.md`; their source bytes
are excluded from project notice insertion. Shared generated workflow files
are also retained without rewriting their notices.

## Python runtime dependencies

The locked runtime includes permissively licensed packages and the following
LGPL-2.1-or-later compression packages: `inflate64`, `multivolumefile`,
`py7zr`, `pybcj`, and `pyppmd`. LGPL components remain dynamically imported
Python packages; a release must preserve their notices, license texts, and the
rights required by their licenses.

The GPLv2-only application terms align with ClamAV's GPLv2-only grant but do
not resolve every dependency's compatibility. The current application runtime
still includes Apache-2.0 `ftfy`; its compatibility with a GPLv2-only combined
distribution remains unresolved. Google authentication packages, Requests,
requests-file and tldextract have been removed from the runtime;
see the [GNU license compatibility guidance](https://www.gnu.org/licenses/license-list.html#apache2)
and [LGPLv3 guidance](https://www.gnu.org/licenses/license-list.html#LGPLv3).
These upstream terms are unchanged. The audit below checks license evidence,
not compatibility clearance, and the native/Rust dependency closure also needs
review before a public binary release.

The PyObjC framework wheels share the PyObjC MIT terms. Some small framework
wheels omit a duplicate license file; the runtime bundle retains the complete
license shipped by other PyObjC wheels in the same locked family.

`pylint` and `astroid` are GPL/LGPL development tools, and `pytest` and
Playwright are test tools. They are not runtime dependencies and must not be
included in an application bundle.
Requests remains a development-only transitive dependency of pytest-base-url
for Playwright tests; it is not in the application or converter runtime closure.

The separately packaged GPLv3 libpff converter communicates through standard
mboxrd files and does not import the application or libclamav. Its independent
lockfile and license texts live in `converters/pff`. The DMG includes its source
and notices alongside the executable. Conversion is followed by the independent
GPLv2 Rust/libclamav scanner before messages enter the API.

Run `make runtime-license-check` in each platform's production build
environment. Run `make runtime-license-bundle LICENSE_OUTPUT=PATH` to create a
ready-to-package notices directory containing `COPYRIGHT`, this file, a
machine-readable inventory, and complete license files for the exact runtime
closure. The audit rejects unknown licenses, missing license texts, and
development packages in that closure. It does not establish compatibility of
every third-party license; review the exact release dependency closure against
the application's GPLv2 terms. Additional application licenses are available
from the copyright holder and do not replace third-party terms.
Platform-specific dependencies mean that
a macOS audit cannot stand in for the required Windows audit, or vice versa.

This inventory is an engineering control, not legal advice. The copyright
owner or counsel must approve the notices and redistribution terms before a
public binary release.

## Rust importer and PST fixtures

The standalone `pst-importer` uses Microsoft's MIT-licensed `outlook-pst` 1.2.0;
its exact transitive dependency versions/checksums are pinned in `Cargo.lock`.
The current Python runtime-license commands do not audit Rust dependencies.
The macOS installer bundles the Rust PST importer. Its release license inventory
must also cover the compiled Cargo dependency closure and include those complete
license texts; the Python audit alone does not establish that coverage.

The public Microsoft and Aspose PST fixtures retain their upstream MIT licenses
in `rust/mct-importer/tests/fixtures/MICROSOFT-LICENSE.txt` and
`ASPOSE-LICENSE.txt`. Their bytes have not acquired project copyright notices;
see that directory's README for origin revisions and SHA-256 values.
