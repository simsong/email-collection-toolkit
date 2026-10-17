# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Packaged trusted ingest plug-ins.

Purpose: Define the package containing trusted built-in plugin manifests and adapters.

How it works:
Keep initialization inert; explicit loaders validate manifests and construct
source/file/processor registries.

Fundamental dependents:

- ``plugin_loader``: packaged acquisition discovery relies on this resource layout.
- ``processing.registry``: built-in processor discovery uses this manifest tree.
"""
