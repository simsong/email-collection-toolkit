# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Packaged source plug-in manifests.

Purpose: Identify the packaged source-provider manifest directory.

How it works:
Supply package/resource structure only; providers implement discovery and acquisition outside
this initializer.

Fundamental dependents:

- ``plugin_loader``: source registry discovery relies on these packaged manifests.
- ``__main__``: local/remote selection depends on that available provider inventory.
"""
