# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

# Purpose: Identify the packaged physical-mail parser manifest directory.
# How it works:
# Supply package/resource structure only; file recognition and parsing are implemented by
# registered adapters.
# Fundamental dependents:
# - plugin_loader: file-parser registry discovery relies on these packaged manifests.
# - sources: local acquisition depends on the parser registry described here.

"""Packaged physical mailbox parser manifests."""
