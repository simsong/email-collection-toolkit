# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Used API of libpff-python 20231205, verified against its C binding."""
from datetime import datetime
from typing import BinaryIO

class record_entry:
    entry_type: int
    data_as_string: str | None
    data_as_integer: int
    data_as_boolean: bool
class record_set:
    number_of_entries: int
    def get_entry(self, index: int) -> record_entry: ...
class item:
    identifier: int
    number_of_record_sets: int
    def get_record_set(self, index: int) -> record_set: ...
class attachment(item):
    size: int
    def read_buffer(self, size: int) -> bytes: ...
class message(item):
    subject: str | None
    sender_name: str | None
    transport_headers: str | None
    plain_text_body: bytes | None
    html_body: bytes | None
    rtf_body: bytes | None
    client_submit_time: datetime | None
    delivery_time: datetime | None
    number_of_attachments: int
    def get_attachment(self, index: int) -> attachment: ...
class folder(item):
    name: str | None
    number_of_sub_folders: int
    number_of_sub_messages: int
    def get_sub_folder(self, index: int) -> folder: ...
    def get_sub_message(self, index: int) -> message: ...
class file:
    root_folder: folder | None
    content_type: int
    def open_file_object(self, file_object: BinaryIO, mode: str = "r") -> None: ...
    def close(self) -> None: ...
    def signal_abort(self) -> None: ...
def get_version() -> str: ...
