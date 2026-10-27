# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Resolve typed default lists without changing packaged resources or source mail.
# Each list starts in a packaged YAML template and may have a personal template.
# Archive snapshots live in processing_settings and require the archive writer lease.
# Legacy file adapters retain existing editable-file contracts where necessary.
# Revision checks and atomic personal writes prevent silent concurrent overwrites.
# Reading a list never initializes a database or writes user configuration.
"""Shared service for customizable application defaults and archive list instances."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from collections.abc import Callable
from contextlib import closing
from functools import lru_cache
from pathlib import Path
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel
from yaml import YAMLError, safe_dump, safe_load

from .catalog import PROCESSING_DATABASE
from .mailbox_tree import preferences_lock
from .sqlite_paths import sqlite_uri
from .storage_sync import sync_directory
from .writer_lock import WriterLease

T = TypeVar('T',bound=BaseModel)
Scope = Literal['personal','archive']
PREFIX = 'customizable-list:'
HISTORY_PREFIX = 'customizable-list-history:'


class StoredList(BaseModel):
    version: Literal[1] = 1
    value_json: str
    origin: str
    template_revision: str


class ListState(BaseModel):
    key: str
    title: str
    description: str
    scope: Scope
    text: str
    revision: str
    origin: str
    packaged_path: str
    personal_path: str
    archive_supported: bool
    needs_customization: bool = False


def personal_lists_directory() -> Path:
    from .application import application_preferences_path
    return application_preferences_path().parent/'default-lists'


def revision(value: BaseModel) -> str:
    return hashlib.sha256(value.model_dump_json().encode()).hexdigest()


@lru_cache(maxsize=128)
def _parse(model: type[T], text: str, source: str) -> T:
    try:
        return model.model_validate(safe_load(text))
    except (ValueError,YAMLError) as error:
        raise ValueError(f'Invalid customizable list {source}: {error}') from error


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    descriptor,name=tempfile.mkstemp(prefix='.default-list-',dir=path.parent)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name,path)
        sync_directory(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


class DefaultList(Generic[T]):
    """Behavior and validation for one registered, typed list."""

    def __init__(self, key: str, title: str, description: str, model: type[T], packaged: Path,
                 *, archive_supported: bool = True, legacy: Callable[[Path,Path|None],T|None] | None = None,
                 legacy_write: Callable[[Path,T],None] | None = None,
                 requires_customization: Callable[[T],bool] | None = None) -> None:
        self.key,self.title,self.description,self.model,self.packaged=key,title,description,model,packaged
        self.archive_supported=archive_supported
        self.legacy,self.legacy_write=legacy,legacy_write
        self.requires_customization=requires_customization

    def personal_path(self, directory: Path | None = None) -> Path:
        return (directory or personal_lists_directory())/(self.key+'.yaml')

    def decode(self, text: str, source: str = 'editor') -> T:
        return _parse(self.model,text,source).model_copy(deep=True)

    def template(self, directory: Path | None = None, *, bundled: bool = False) -> T:
        personal=self.personal_path(directory)
        path=self.packaged if bundled or not personal.is_file() else personal
        return self.decode(path.read_text(encoding='utf-8'),str(path))

    def read(self, archive: Path | None = None, directory: Path | None = None) -> T:
        if archive is not None and self.archive_supported:
            # Existing editable archive files remain authoritative through adapters.
            if self.legacy is not None and (legacy:=self.legacy(archive,directory)) is not None:
                return legacy
            path=archive/PROCESSING_DATABASE
            if path.is_file():
                with closing(sqlite3.connect(sqlite_uri(path),uri=True)) as database:
                    row=database.execute('SELECT value FROM processing_settings WHERE name=?',(PREFIX+self.key,)).fetchone()
                if row:
                    record=StoredList.model_validate_json(row[0])
                    return self.model.model_validate_json(record.value_json)
        return self.template(directory)

    def state(self, scope: Scope, archive: Path | None = None, directory: Path | None = None) -> ListState:
        if scope=='archive' and (archive is None or not self.archive_supported):
            raise ValueError('This list requires personal scope or an open archive')
        value=self.read(archive,directory) if scope=='archive' else self.template(directory)
        origin='personal template' if self.personal_path(directory).is_file() else 'application template'
        if scope=='archive' and archive is not None:
            path=archive/PROCESSING_DATABASE
            if path.is_file():
                with closing(sqlite3.connect(sqlite_uri(path),uri=True)) as database:
                    if database.execute('SELECT 1 FROM processing_settings WHERE name=?',(PREFIX+self.key,)).fetchone():
                        origin='archive database (with any existing legacy-file override)'
            if self.legacy is not None and self.legacy(archive,directory) is not None:
                origin='archive configuration file'
        return ListState(key=self.key,title=self.title,description=self.description,scope=scope,
            text=safe_dump(value.model_dump(mode='json'),sort_keys=False,allow_unicode=True),revision=revision(value),origin=origin,
            packaged_path=str(self.packaged),personal_path=str(self.personal_path(directory)),archive_supported=self.archive_supported,
            needs_customization=bool(self.requires_customization and self.requires_customization(value)))

    def save_personal(self, text: str, expected: str, directory: Path | None = None, *, reset: bool = False) -> ListState:
        path=self.personal_path(directory)
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with preferences_lock(path):
            if revision(self.template(directory))!=expected:
                raise ValueError('This template changed in another editor; reload before saving')
            if reset:
                path.unlink(missing_ok=True)
                sync_directory(path.parent)
            else:
                value=self.decode(text)
                atomic_text(path,safe_dump(value.model_dump(mode='json'),sort_keys=False,allow_unicode=True))
        return self.state('personal',directory=directory)

    def store(self, database: sqlite3.Connection, value: T, directory: Path | None = None, *, origin: str = 'archive edit') -> None:
        record=StoredList(value_json=value.model_dump_json(),origin=origin,template_revision=revision(self.template(directory)))
        database.execute('INSERT OR REPLACE INTO processing_settings VALUES(?,?)',(PREFIX+self.key,record.model_dump_json()))
        database.execute('INSERT OR IGNORE INTO processing_settings VALUES(?,?)',
            (HISTORY_PREFIX+self.key+':'+revision(value),record.model_dump_json()))

    def snapshot(self, database: sqlite3.Connection, archive: Path, directory: Path | None = None) -> T:
        value=self.read(archive,directory)
        row=database.execute('SELECT value FROM processing_settings WHERE name=?',(PREFIX+self.key,)).fetchone()
        if row is None or StoredList.model_validate_json(row[0]).value_json!=value.model_dump_json():
            self.store(database,value,directory,origin='resolved template or archive configuration')
        return value

    def save_archive(self, archive: Path, text: str, expected: str, lease: WriterLease,
                     directory: Path | None = None, *, reset: bool = False) -> ListState:
        if not self.archive_supported or not lease.acquired or lease.lock_path.parent.parent.resolve()!=archive.resolve():
            raise ValueError('An active writer lease for this archive is required')
        if revision(self.read(archive,directory))!=expected:
            raise ValueError('This archive list changed in another editor; reload before saving')
        value=self.template(directory) if reset else self.decode(text)
        # Adapters retain legacy authoritative files; a crash before mirroring the
        # database still reads the successfully published legacy value.
        if self.legacy_write is not None:
            self.legacy_write(archive,value)
        from .processing.store import connect
        with closing(connect(archive,create=not (archive/PROCESSING_DATABASE).exists(),production=True)) as database, database:
            self.store(database,value,directory)
        return self.state('archive',archive,directory)
