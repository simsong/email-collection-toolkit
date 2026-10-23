# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Expose the shared list catalog through a small, typed desktop editor bridge.
# A window remains bound to its original archive, never the currently active one.
# Personal edits use atomic template writes; archive edits acquire the writer lease.
# Every save carries the revision shown to the user and rejects stale overwrites.
# Injected personal directories keep tests and previews away from real preferences.
"""Generic customizable-default-list editor bridge."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from .custom_lists import Scope
from .list_catalog import default_list, registered_lists
from .writer_lock import WriterLease


class ListSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: str
    scope: Scope = 'personal'


class ListEdit(ListSelection):
    text: str
    revision: str
    reset: bool = False


class ListDescriptor(BaseModel):
    key: str
    title: str
    archive_supported: bool


class DefaultListsApi:
    def __init__(self, archive: Path | None = None, *, identity: str = '', version: str = '',
                 directory: Path | None = None,
                 writer_activity: Callable[[],AbstractContextManager[None]] = nullcontext) -> None:
        self.archive,self.identity,self.version,self.directory=archive,identity,version,directory
        self.writer_activity=writer_activity
        self.window: Any = None

    def catalog(self) -> list[dict[str,Any]]:
        return [ListDescriptor(key=item.key,title=item.title,archive_supported=item.archive_supported and self.archive is not None)
                .model_dump(mode='json') for item in registered_lists()]

    def load(self, selection: dict[str,Any]) -> dict[str,Any]:
        request=ListSelection.model_validate(selection)
        return default_list(request.key).state(request.scope,self.archive,self.directory).model_dump(mode='json')

    def update(self, edit: dict[str,Any]) -> dict[str,Any]:
        request=ListEdit.model_validate(edit)
        definition=default_list(request.key)
        with self.writer_activity():
            if request.scope=='personal':
                state=definition.save_personal(request.text,request.revision,self.directory,reset=request.reset)
            else:
                if self.archive is None:
                    raise ValueError('Open an archive before editing archive lists')
                with WriterLease.acquire(self.archive,self.identity,'Customize default list',uuid4().hex,self.version) as lease:
                    state=definition.save_archive(self.archive,request.text,request.revision,lease,self.directory,reset=request.reset)
        return state.model_dump(mode='json')
