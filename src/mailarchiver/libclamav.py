# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Small ctypes binding to the public libclamav 1.x C ABI, hosted off the UI process."""
from __future__ import annotations

import ctypes as c
import os
from functools import lru_cache
from pathlib import Path

from .clamav_definitions import DefinitionSet, certificates_path
from .processing.contracts import ScanEvidence, ScanStatus


CL_DB_STDOPT = 0x200A
CL_DB_OFFICIAL_ONLY = 0x1000
CL_ENGINE_CVDCERTSDIR = 37
CL_SCAN_GENERAL_HEURISTICS = 0x4
CL_SCAN_HEURISTIC_INCOMPLETE = 0xC4


@lru_cache(maxsize=4)
def engine_version(library: Path) -> str:
    native = c.CDLL(str(library))
    native.cl_retver.restype = c.c_char_p
    return native.cl_retver().decode("ascii")


class ScanOptions(c.Structure):
    """clamav.h 1.x cl_scan_options (five uint32_t bitfields)."""
    _fields_ = [(name, c.c_uint32) for name in ("general", "parse", "heuristic", "mail", "dev")]


class Engine:
    """Own one compiled engine; never turn a native error into a clean verdict."""

    def __init__(self, library: Path, definitions: DefinitionSet, temporary_directory: Path) -> None:
        self.lib = c.CDLL(str(library))
        self.definitions = definitions
        self.engine = None
        self.lib.cl_retver.restype = c.c_char_p
        self.version: str = self.lib.cl_retver().decode("ascii")
        if self.version.split(".")[0] != "1":
            raise RuntimeError(f"Unsupported libclamav ABI: {self.version}; expected 1.x")
        self.lib.cl_init.argtypes = [c.c_uint]
        self.lib.cl_strerror.argtypes = [c.c_int]
        self.lib.cl_strerror.restype = c.c_char_p
        self.lib.cl_engine_new.restype = c.c_void_p
        self.lib.cl_engine_free.argtypes = [c.c_void_p]
        self.lib.cl_engine_compile.argtypes = [c.c_void_p]
        self.lib.cl_load.argtypes = [c.c_char_p, c.c_void_p, c.POINTER(c.c_uint), c.c_uint]
        self.lib.cl_scanfile.argtypes = [c.c_char_p, c.POINTER(c.c_char_p), c.POINTER(c.c_ulong), c.c_void_p, c.POINTER(ScanOptions)]
        self._check(self.lib.cl_init(0))
        self.engine = self.lib.cl_engine_new()
        if not self.engine:
            raise RuntimeError("libclamav could not allocate an engine")
        try:
            self.lib.cl_engine_set_str.argtypes = [c.c_void_p, c.c_int, c.c_char_p]
            self._check(self.lib.cl_engine_set_str(self.engine, 13, os.fsencode(temporary_directory)))
            if certs := certificates_path():
                self._check(self.lib.cl_engine_set_str(self.engine, CL_ENGINE_CVDCERTSDIR, os.fsencode(certs)))
            signatures = c.c_uint()
            for definition in definitions.files:
                # Official signed databases only, phishing, URL and bytecode support.
                self._check(self.lib.cl_load(os.fsencode(definition.path), self.engine, c.byref(signatures), CL_DB_STDOPT | CL_DB_OFFICIAL_ONLY))
            if not signatures.value:
                raise RuntimeError("ClamAV loaded no signatures")
            self._check(self.lib.cl_engine_compile(self.engine))
        except BaseException:
            self.close()
            raise

    def _check(self, result: int) -> None:
        if result:
            raise RuntimeError(self.lib.cl_strerror(result).decode("utf-8", "replace"))

    def scan(self, path: Path) -> ScanEvidence:
        name = c.c_char_p()
        # All parsers; encrypted and over-limit inputs must not be reported clean.
        options = ScanOptions(CL_SCAN_GENERAL_HEURISTICS, 0xFFFFFFFF, CL_SCAN_HEURISTIC_INCOMPLETE, 0, 0)
        result = self.lib.cl_scanfile(os.fsencode(path), c.byref(name), None, self.engine, c.byref(options))
        detail = name.value.decode("utf-8", "replace") if name.value else self.lib.cl_strerror(result).decode("utf-8", "replace")
        status: ScanStatus = "clean" if result == 0 else "infected" if result == 1 else "scanner-error"
        if result == 1 and detail.startswith(("Heuristics.Limits.Exceeded", "Heuristics.Encrypted")):
            status = "unscannable"
        return ScanEvidence(status=status, detail=detail, engine_version=self.version, signature_version=self.definitions.versions)

    def close(self) -> None:
        if self.engine:
            self.lib.cl_engine_free(self.engine)
            self.engine = None
