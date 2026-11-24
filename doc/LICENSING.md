<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Licensing: what we ship, under which terms, and why

This document explains the licenses of Email Collection Toolkit (ECT), the
reasons for them, every third-party component that we embed or bundle, and
which license applies to each executable and package we distribute. It also
records the compatibility problems that are still open.

`THIRD_PARTY_NOTICES.md` remains the notice file that ships inside packages.
This document is the explanation behind it. Where the two disagree, fix both.

**This is an engineering record, not legal advice.** It was assembled from the
repository's license files, lockfiles, and build scripts as of
2026-11-23 (`pyproject.toml` version 1.0.0a73). The copyright holder or counsel
must approve the redistribution terms before a public binary release.

## 1. Summary

| What we distribute | License of our code in it | Main third-party terms inside |
| --- | --- | --- |
| Source repository and Python sdist/wheel (`mailarchiver`) | GPL-2.0-only | Vendored MIT JavaScript; MPL-2.0 Public Suffix List data |
| macOS DMG (`Email Collection Toolkit.app`) | GPL-2.0-only (application, Rust importers); GPL-3.0-only (`pff-converter`) | ClamAV (GPL-2.0-only + OpenSSL exception), OpenSSL 3 (Apache-2.0), CPython (PSF-2.0), Sparkle (MIT), PyInstaller bootloader (GPL-2.0+ with bootloader exception) and runtime hooks (Apache-2.0), LGPL-2.1+ and permissive Python packages, permissive Rust crates, libpff (LGPL-3.0+) in the converter only |
| Windows MSIX (Python preview) | Same as macOS | ClamAV 1.5.4 portable build with its DLLs (including OpenSSL, libcurl, libxml2, UnRAR), CPython 3.12 (PSF-2.0, with its own bundled OpenSSL and others), WinSparkle (MIT), Microsoft Visual C++ runtime DLLs, the same Python packages, Rust crates and converter |
| Website (`website/`) | GPL-2.0-only (including the Envelope Rainbow theme) | One illustration under CC BY-SA 4.0, listed in `website/static/images/ATTRIBUTION.md` |

The application is **GPL-2.0-only**, not just the ClamAV plug-in. Every
project-owned license declaration agrees on this: `LICENSE`, `COPYRIGHT`,
`pyproject.toml` (`license = "GPL-2.0-only"`), every Rust `Cargo.toml`, the
website theme, `README.md`, and `website/content/about.md`. The one deliberate
exception is the standalone PST/OST converter in `converters/pff`, which is
**GPL-3.0-only** (section 2.2).

## 2. Our own licenses and the reasoning

### 2.1 The application: GPL-2.0-only

ECT embeds the ClamAV engine (`libclamav`) and calls it in-process, through
Python `ctypes` (`src/mailarchiver/libclamav.py`, used by the `clamav`
processor plug-in) and through Rust `libloading` (`mcti-scan`). ClamAV 1.5.4 is
licensed under GPL version 2 only. Code that is combined with it and
distributed must be under terms compatible with GPL-2.0-only. GPL-2.0-only is
the simplest choice that satisfies this.

We chose "only" rather than "or later" because "or later" would not change
anything for ClamAV, which stays GPLv2-only. See the GPLv2/LGPLv3 discussion in
`THIRD_PARTY_NOTICES.md`.

`COPYRIGHT` also says that **additional licenses are available from the
copyright holder**. The copyright holder can license project-owned code on
other terms. Such a license cannot cover ClamAV, libpff, or any other
third-party component, so any non-GPL edition would have to ship without
ClamAV and without the GPL/LGPL components below. The project's
contributors are Simson Garfinkel plus automated agents and bots acting for
him, so the copyright holder can still grant additional permissions or
licenses for project-owned code. Accepting outside contributions without a
contributor agreement would end that.

The GPL covers the software, not users' archives. `website/content/rights.md`
states that processing mail with ECT gives the project no rights in that
material.

### 2.2 The PST/OST converter: GPL-3.0-only

`converters/pff` reads Outlook PST/OST files with libpff's Python bindings
(`libpff-python` 20260926), which are LGPL-3.0-or-later. LGPLv3 code cannot be
combined with GPL-2.0-only code. The converter is therefore a separate program
under GPL-3.0-only. It does not import `mailarchiver` or load `libclamav`. It
writes standard mboxrd files that the GPLv2 application then reads and scans.
The DMG and MSIX ship it as its own PyInstaller executable with its own
license files (`converters/pff/LICENSE`, `COPYRIGHT`, `libpff-LGPL.txt`).

This is the project's pattern for any component whose license cannot be
combined with GPL-2.0-only: **a separate program, communicating through files
or pipes with standard formats**. GNU's
[aggregation guidance](https://www.gnu.org/licenses/gpl-faq.html#MereAggregation)
looks at both the mechanism and the meaning of the communication. A process
boundary helps, but it does not settle the question on its own.

### 2.3 Website and documentation

The website, its Envelope Rainbow theme and the documentation are
GPL-2.0-only under `COPYRIGHT`. Third-party images keep their own terms
(`website/static/images/ATTRIBUTION.md`, CC BY-SA 4.0).

## 3. What each shipped package contains

### 3.1 macOS DMG

Built by `make dmg` → `scripts/build_macos.py` (PyInstaller `--onedir`).

| Bundled item | Where in the `.app` | License |
| --- | --- | --- |
| ECT application (`mailarchiver`, GUI, plug-ins) | `Contents/` (frozen) | GPL-2.0-only |
| PyInstaller bootloader (the main executable) and `PyInstaller/loader` | `Contents/MacOS/` | GPL-2.0-or-later with the Bootloader Exception, which allows embedding in programs under any license |
| PyInstaller run-time hooks and `fake-modules` | inside the frozen archive | Apache-2.0 (per PyInstaller's `COPYING.txt`) |
| CPython 3.12 runtime and standard library | `Contents/Frameworks/` | PSF-2.0 (GPL-compatible) |
| Python runtime packages (section 4.1) | `Contents/Frameworks/` | Mostly MIT/BSD; Apache-2.0 `ftfy`; LGPL-2.1+ compression packages |
| Tabulator 6.5.2, Dagre 3.1.1 | `gui/vendor/` | MIT |
| Public Suffix List snapshot | package data | MPL-2.0 |
| `libclamav.dylib`, `freshclam`, and the dylibs they load, copied from the build machine's Homebrew ClamAV 1.5.4 | `Contents/Frameworks/clamav/` | GPL-2.0-only with an OpenSSL exception. Other dylibs in ClamAV's load chain keep their own licenses (section 5.3). |
| OpenSSL 3 (`libssl.3.dylib`, `libcrypto.3.dylib`), from the install ClamAV was linked against | `Contents/Frameworks/` | Apache-2.0 |
| ClamAV signature databases (`main`, `daily`, `bytecode`) and signing certificates | `Contents/Resources/clamav/` | Not recorded in the repository (section 5.5) |
| Sparkle 2.10.0 updater framework | `Contents/Frameworks/Sparkle.framework` | MIT, with notices for Sparkle's own dependencies |
| `pst-importer`, `mcti-scan` (Rust) | `Contents/Frameworks/importers/` | GPL-2.0-only; statically linked crates are permissive (section 4.3) |
| `pff-converter` (separate PyInstaller program) | `Contents/Resources/importers/pff-converter/` | GPL-3.0-only; contains libpff-python (LGPL-3.0+), pydantic (MIT), CPython and PyInstaller |

Notices shipped: `Contents/Resources/Third Party Notices/` holds `LICENSE`,
`COPYRIGHT`, `THIRD_PARTY_NOTICES.md`, `ClamAV-COPYING.txt`,
`OpenSSL-LICENSE.txt`, `Sparkle-LICENSE.txt`, every runtime package's
`.dist-info` license files, and the converter's source and notices. The
mounted-DMG test (`verify_mounted_notices`) fails if the five named files are
missing.

### 3.2 Windows MSIX (Python preview)

Built by `make msix-test` → `scripts/win/build_windows_msix.ps1`. There is no
PyInstaller step. The package carries a private CPython and copies of the
installed packages.

| Bundled item | Where in the package | License |
| --- | --- | --- |
| ECT application | `python/Lib/site-packages/mailarchiver`, `python/Lib/gui` | GPL-2.0-only |
| Private CPython 3.12 x64 (`python.exe`, `pythonw.exe`, `python312.dll`, `Lib`, `DLLs`, `LICENSE.txt`) | `python/` | PSF-2.0. `DLLs` contains CPython's own third-party libraries, including OpenSSL 3 (Apache-2.0), SQLite, libffi, Tcl/Tk, bzip2, xz and zlib. CPython's `LICENSE.txt`, which is copied, lists their terms. |
| Python runtime packages (section 4.1), every installed `site-packages` entry | `python/Lib/site-packages` | As in section 4.1, plus the Windows-only `pythonnet` (MIT), `clr_loader` (MIT) and `pywin32-ctypes` (BSD-3-Clause) |
| Official ClamAV 1.5.4 portable x64 build, pinned by SHA-256 (`scripts/win/prepare_clamav.ps1`), all DLLs and programs except `.pdb/.lib/.exp` | `python/clamav/` | See section 5.3. This includes ClamAV GPL-2.0-only, `libclamunrar*.dll` (UnRAR license, not open source), OpenSSL 3, libcurl, libxml2, json-c, libssh2, nghttp2, PCRE2, bzip2, pthreads4w, PDCurses, and Microsoft C++ runtime DLLs. |
| ClamAV signature databases | `python/clamav/definitions/` | Not recorded (section 5.5) |
| WinSparkle 0.9.4 (`WinSparkle.dll`), pinned by SHA-256 | `python/winsparkle/` | MIT; `COPYING` and `COPYING.expat` copied beside it |
| `pst-importer.exe`, `mcti-scan.exe` plus `vcruntime140*.dll` | `python/importers/` | GPL-2.0-only; Microsoft Visual C++ redistributable terms for the runtime DLLs |
| `pff-converter` | `python/importers/pff-converter/` | GPL-3.0-only, as on macOS |

Notices shipped: `LICENSE`, `COPYRIGHT`, `THIRD_PARTY_NOTICES.md` at the
package root, the repository's `licenses/` directory, `licenses/pff-converter/`,
ClamAV's `COPYING.txt` and `COPYING/` directory, WinSparkle's two license files,
CPython's `LICENSE.txt`, and each package's `.dist-info` license files.
WebView2 is not bundled. The application uses the copy installed with Windows.

### 3.3 Python sdist and wheel

`pyproject.toml` declares `license = "GPL-2.0-only"` and includes `LICENSE`,
`COPYRIGHT`, `THIRD_PARTY_NOTICES.md`, the Tabulator and Dagre licenses,
`licenses/proxy_tools-BSD.txt`, `licenses/publicsuffix-MPL-2.0.txt`, and the
theme license. The wheel contains no native third-party code. Dependencies are
installed separately by the user's installer.

## 4. Third-party inventory

### 4.1 Python runtime dependencies (`uv.lock`, `uv export --no-dev`)

Licenses come from installed package metadata, as reported by
`scripts/check_runtime_licenses.py` (Linux run), and from PyPI metadata for
the macOS-only and Windows-only packages.

| Package | Version | License | Platforms shipped |
| --- | --- | --- | --- |
| annotated-types | 0.8.0 | MIT | all |
| backports.zstd | 1.7.0 | PSF-2.0 (bundles zstd, BSD) | all (Python < 3.14) |
| beautifulsoup4 | 4.15.0 | MIT | all |
| bottle | 0.13.4 | MIT | all |
| brotli | 1.2.1 | MIT | all |
| cffi | 2.1.1 | MIT-0 | Windows |
| charset-normalizer | 3.5.1 | MIT | all |
| clr_loader | 0.3.1 | MIT (label missing from wheel; text verified by hash) | Windows |
| **ftfy** | 6.3.1 | **Apache-2.0** | all |
| **inflate64** | 1.0.4 | **LGPL-2.0-or-later** | all |
| jaraco.classes / .context / .functools | 3.4.0 / 6.1.2 / 4.6.0 | MIT | all |
| keyring | 25.7.0 | MIT | all |
| more-itertools | 11.2.1 | MIT | all |
| **multivolumefile** | 0.2.3 | **LGPL-2.0-or-later** | all |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause (we use BSD-2-Clause) | all |
| proxy_tools | 0.1.0 | BSD (wheel metadata wrongly says MIT; text in `licenses/`) | all |
| psutil | 7.2.2 | BSD-3-Clause | all |
| **py7zr** | 1.1.3 | **LGPL-2.1-or-later** | all |
| **pybcj** | 1.0.8 | **LGPL-2.1-or-later** | all |
| pycparser | 3.0 | BSD-3-Clause | Windows |
| pycryptodomex | 3.23.0 | BSD and public domain | all |
| pydantic / pydantic-core | 2.13.4 / 2.46.4 | MIT (pydantic-core also contains compiled Rust crates) | all |
| pyobjc-core and the Cocoa, Quartz, Security, UniformTypeIdentifiers and WebKit frameworks | 12.2.2 | MIT | macOS |
| **pyppmd** | 1.3.1 | **LGPL-2.1-or-later** | all |
| pythonnet | 3.1.0 | MIT | Windows |
| pywebview | 6.2.1 | BSD-3-Clause | all |
| pywin32-ctypes | 0.2.3 | BSD-3-Clause | Windows |
| PyYAML | 6.0.3 | MIT | all |
| soupsieve | 4.0 | MIT | all |
| striprtf | 0.0.33 | BSD-3-Clause | all |
| tabulate | 0.10.0 | MIT | all |
| texttable | 1.7.0 | MIT | all |
| typing_extensions | 4.16.0 | PSF-2.0 | all |
| typing-inspection | 0.4.4 | MIT | all |
| wcwidth | 0.8.3 | MIT | all |

Linux-only entries in the lock (`cryptography`, `jeepney`, `SecretStorage`) are
not in any shipped package, because we ship no Linux binary.
Development and test tools (pylint, astroid, pytest, Playwright, pyright, ruff,
boto3) are not shipped, and the license audit rejects them in a runtime closure.

The LGPL packages are compatible with GPL-2.0-only. LGPL-2.1 section 3 allows
conversion to GPLv2. As pure or dynamically loaded Python packages, they remain
replaceable by the user.

### 4.2 Vendored and data files in the repository

| Component | Location | License |
| --- | --- | --- |
| Tabulator 6.5.2 | `gui/vendor/tabulator/` | MIT |
| Dagre 3.1.1 (with Graphlib) | `gui/vendor/dagre/` | MIT |
| Public Suffix List, 2025-04-07 snapshot | `src/mailarchiver/public_suffix_list.dat` | MPL-2.0 |
| Microsoft and Aspose PST test fixtures | `rust/mct-importer/tests/fixtures/` | MIT (tests only, not shipped) |
| Website illustration and photograph | `website/static/images/` | CC BY-SA 4.0 and as listed in `ATTRIBUTION.md` (website only) |

MPL-2.0 is file-level copyleft and is compatible with the GPL through its
"Secondary License" clause. We ship the file unchanged.

### 4.3 Rust crates in `pst-importer` and `mcti-scan`

Both binaries come from the `mct-importer` crate. They statically link the
crates below. The licenses come from `cargo tree -p mct-importer -e normal
--target all` over `Cargo.lock`. Proc-macro crates run only at compile time and
are left out.

| License expression | Crates |
| --- | --- |
| GPL-2.0-only | `mct-importer`, `mime-transfer` (ours) |
| MIT | `outlook-pst` 1.2.0 (Microsoft), `data-encoding`, `generic-array`, and others |
| MIT OR Apache-2.0 (we use MIT) | `anyhow`, `base64`, `chrono`, `sha2`, `url`, `idna`, `tempfile`, `libc`, `windows-sys`, and most others |
| Unicode-3.0 | ICU4X crates (`icu_*`, `zerovec`, `yoke`, `litemap`, …) |
| 0BSD | `mailparse`, `quoted_printable` |
| ISC | `libloading` |
| Unlicense OR MIT | `byteorder` |
| (Apache-2.0 OR MIT) AND BSD-3-Clause | `encoding_rs` |
| Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT | `rustix`, `linux-raw-sys` |
| MIT OR Apache-2.0 OR LGPL-2.1-or-later | `r-efi` (UEFI target only, not built for our targets) |

Every crate offers at least one GPLv2-compatible permissive license. The Rust
standard library is MIT OR Apache-2.0. `pst-downloader`, `archive-verifier`,
`mdti-validator`, `mcti-generator` and `xtask` are development tools and are not
shipped.

### 4.4 The PST/OST converter's closure (`converters/pff/uv.lock`)

Runtime: `libpff-python` 20260926 (LGPL-3.0-or-later; upstream
https://github.com/libyal/libpff), `pydantic`/`pydantic-core` (MIT),
`annotated-types`, `typing-extensions`, `typing-inspection`. It is frozen with
PyInstaller 6.22.3, so it also contains the PyInstaller bootloader (GPLv2+
with exception), the Apache-2.0 run-time hooks, and CPython (PSF-2.0). All of
these are compatible with GPL-3.0-only.

## 5. Compatibility analysis and open problems

These are the real problems found in the current tree. Items 5.1 to 5.5 affect
what we ship today.

### 5.1 Apache-2.0 code inside the GPL-2.0-only application (open)

The FSF considers Apache-2.0 incompatible with GPLv2 (it is compatible with
GPLv3). The GPLv2 application process currently contains these Apache-2.0-only
pieces:

* `ftfy` (imported by `message.py` and `encoding.py`).
* OpenSSL 3, used by Python's `ssl` module (IMAP, update checks) and by ClamAV.
  On macOS the build deliberately makes them the same two dylibs.
* PyInstaller's run-time hooks and fake modules (macOS DMG).
* On Windows, `pthreadVC3.dll` (pthreads4w 3.x, Apache-2.0) is loaded by
  `libclamav.dll`.

There are two separate questions here.

1. **Our own code.** Since Simson holds the copyright to the project-owned code, this is
   easy to fix. Add an explicit additional permission to `COPYRIGHT`, like
   ClamAV's own OpenSSL exception, allowing project-owned code to be
   combined with OpenSSL and with Apache-2.0-licensed libraries. This needs
   the copyright holder's decision. It is not done in this change.
2. **ClamAV's code.** ClamAV's `COPYING.txt` grants an exception for linking
   with **OpenSSL**, so OpenSSL is fine for ClamAV too. ClamAV has no
   exception for other Apache-2.0 code. When `libclamav` is loaded into the
   same Python process as `ftfy` and the PyInstaller hooks, there is a
   reasonable argument that the result is one program combining GPLv2-only
   ClamAV with Apache-2.0 code. We cannot grant an exception for ClamAV's code.
   The cleanest fix is architectural: **load `libclamav` only in a process whose
   closure is GPLv2-compatible.** `mcti-scan` already qualifies (GPLv2 plus
   permissive crates, plus OpenSSL under ClamAV's exception). The Python
   `ctypes` scanner (`libclamav.py`, the `clamav` processor) does not, because it runs
   in a frozen Python process that also carries `ftfy` and the PyInstaller hooks.
   Alternatives are replacing `ftfy`, or accepting the argument that loading a
   library with `ctypes` does not make one combined work. GNU does not accept
   that argument.

`THIRD_PARTY_NOTICES.md` already records the `ftfy` and OpenSSL issues as
unresolved. This section adds the PyInstaller hooks, pthreads4w and the
architectural option.

### 5.2 UnRAR in the Windows package (open)

The MSIX copies every DLL from the ClamAV zip, including `libclamunrar.dll` and
`libclamunrar_iface.dll`. UnRAR's license is freeware: it allows free
redistribution but forbids using the code to re-create the RAR compression
algorithm. That is not an open-source license and is not GPL-compatible.
ClamAV keeps it in a separately loaded library for this reason, and Cisco ships
it in its official build. We redistribute it as part of ClamAV. Options: keep it
and keep `COPYING/COPYING.unrar` (current state, and the same as Cisco's own
distribution), or drop the two DLLs so ClamAV cannot look inside RAR archives.
Someone should also check whether the Homebrew ClamAV in the DMG includes
`libclamunrar`. The repository does not record that.

### 5.3 Incomplete native notices (open)

* **Windows ClamAV.** The upstream zip has license texts (`COPYING/`) for
  bzip2, curl, file, getopt, LGPL, LLVM, LZMA, PCRE, regex, UnRAR, YARA and
  zlib. It has none for OpenSSL, libxml2, json-c, libssh2, nghttp2, pthreads4w,
  PDCurses or the Microsoft runtime DLLs (`vcruntime140*`, `msvcp140*`,
  `concrt140`), all of which we ship. Add those texts to the MSIX.
* **macOS ClamAV.** The build bundles ClamAV's whole dylib load chain from
  Homebrew and checks that nothing links outside the app. It copies license
  texts only for ClamAV and OpenSSL. Collect the rest, such as PCRE2, json-c
  and libcurl, from the Homebrew prefixes that supplied them.
* **Rust crates.** Neither package includes the license texts of the
  statically linked crates in section 4.3, and `runtime-license-check` audits
  only Python. MIT, BSD, ISC and Unicode licenses all require that their notice
  is kept. Generate a crate notice file from `Cargo.lock` (for example with
  `cargo about`) and ship it.
* **PyInstaller and CPython on macOS.** The DMG notices do not include
  PyInstaller's `COPYING.txt` (which covers the Apache-2.0 hooks) or CPython's
  `LICENSE`.
* **Compiled code inside Python wheels.** `pydantic-core` (Rust),
  `brotli` and `backports.zstd` contain compiled third-party code. Their wheels'
  own license files are what we ship, and the audit trusts them.

### 5.4 Corresponding source for binaries we redistribute (open)

GPLv2 §3 requires us to give recipients of object code the corresponding source,
or a written offer valid for three years. Linking to the upstream project's
download page is not one of the options §3 gives. This applies to ClamAV 1.5.4
(both platforms), to libpff (LGPL-3.0 §4 to §6), and to our own GPL code (a
tagged GitHub release next to the binaries is enough for that). The release
process does not yet publish ClamAV and libpff source archives next to each DMG
and MSIX. `doc/EMBEDDED_CLAMAV.md` lists this as still required.

### 5.5 ClamAV signature databases (unverified)

Both packages bundle Cisco Talos `main`, `daily` and `bytecode` databases. The
repository does not record their redistribution terms. Check Cisco's current
terms for the official databases before a public release.

### 5.6 Inconsistencies found

* **The scope of GPLv2.** The project is GPL-2.0-only as a whole, not only the
  ClamAV plug-in. The exception is the GPL-3.0-only converter.
* `website/content/rights.md` said only "GNU General Public License (GPL)",
  without a version. This change corrects it to "version 2 only" to match
  everything else.
* `COPYRIGHT` and every file header say "All Rights Reserved" next to a GPL
  grant. That phrase is a historical formality with no legal effect, and it
  does not take back the GPL. It can confuse readers, though. "Licensed under
  GPL-2.0-only" would be clearer.
* The 2026-11-23 comments on #159 and #19 say "AGPL is not a blocker, because
  the project is GPLv2." The conclusion is fine but the reason is not. GPLv2-only
  and AGPLv3 cannot be combined. AGPL is acceptable only as a **separate
  program**. A worker process that runs our GPLv2-only `mailarchiver` modules
  and imports an AGPL library (for example PyMuPDF) is not a separate program,
  even if it is a separate OS process. See section 6.

## 6. Planned attachment-extraction tools (not shipped yet)

These are the tools in the agreed plan (#159, #163, #19). None of them are in
`uv.lock` or any build script today. The "Integration" column gives the rule
that keeps each one compatible with a GPL-2.0-only application.

| Tool | License | Integration rule |
| --- | --- | --- |
| python-docx, openpyxl, python-pptx | MIT | In-process is fine. They depend on lxml (BSD-3-Clause), which bundles libxml2 and libxslt (MIT). |
| xlrd | BSD-3-Clause | In-process is fine. |
| pypdfium2 5.x | Bindings Apache-2.0 OR BSD-3-Clause. The bundled PDFium binary includes **Apache-2.0 code (abseil and highway, as the wheel's `BUILD_LICENSES/` lists them)** plus FreeType (FTL), ICU, libjpeg-turbo, OpenJPEG, lcms, libpng, libtiff and zlib. | Same problem as section 5.1. Either the additional permission described there, or a separate extractor program. Ship the wheel's `BUILD_LICENSES/`. |
| Tesseract | Apache-2.0 | Run the `tesseract` binary as a separate program (fine), or add the 5.1 permission before linking it through `tesserocr` (MIT bindings, but they link libtesseract). Ship its language-data licenses (Apache-2.0). |
| OCRmyPDF 17 | MPL-2.0 | MPL itself is GPLv2-compatible. It depends on pikepdf (MPL-2.0), which links **qpdf (Apache-2.0)**. Run it as the `ocrmypdf` command (the planned `--output-type none --sidecar` mode), not as an imported library, unless the 5.1 permission exists. |
| Ghostscript | AGPL-3.0 | Separate program only, never linked. The plan's text-only OCRmyPDF mode avoids it entirely. |
| antiword | GPL-2.0-or-later (upstream; confirm from the exact package we bundle) | Separate program. Compatible either way. |
| catdoc / catppt | GPL-2.0-or-later (upstream; confirm from the exact package we bundle) | Separate program. Compatible either way. |
| docling (optional plug-in) | MIT, but it pulls in a large closure that includes PyTorch and models with their own terms | Audit its whole closure before shipping. Prefer a separately installed plug-in. |
| PyMuPDF (excluded at first, now allowed) | AGPL-3.0 | Only in a separate program that does not import `mailarchiver`, for example a converter like `converters/pff` licensed AGPL-3.0, exchanging plain files or JSON. |

**Rule of thumb.** In-process with the GPLv2 application: GPLv2-compatible
licenses only (MIT, BSD, ISC, zlib, PSF, LGPL-2.1, MPL-2.0, GPLv2). Apache-2.0,
GPLv3-only, LGPLv3 and AGPLv3 components belong in separate programs with their
own license, following the `converters/pff` pattern. The exception is
Apache-2.0, which becomes acceptable in-process for project-owned code if the
permission in 5.1 is added. ClamAV still requires that `libclamav` not share a
process with Apache-2.0 code.

## 7. Keeping this document correct

* `make runtime-license-check` on each platform's build machine audits the
  Python closure. `make runtime-license-bundle LICENSE_OUTPUT=…` collects
  its texts. A macOS run does not stand in for Windows.
* `cargo tree -p mct-importer -e normal --target all --prefix none -f '{p} | {l}'`
  lists the Rust closure.
* `scripts/win/prepare_clamav.ps1` (Windows) and `clamav_bundle_arguments` /
  `copy_native_notices` in `scripts/build_macos.py` (macOS) define the native
  ClamAV payload.
* Update sections 3 and 4 when a dependency, bundled binary or build script
  changes. Update section 6 when an extraction tool is added.
