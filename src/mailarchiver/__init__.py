# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

# Purpose: Define the importable mailarchiver package and its preservation scope.
# How it works:
# Keep package initialization free of archive work; behavior lives in explicit submodules.
# Fundamental dependents:
# - __main__: the installed CLI resolves its application modules within this package.
# - gui_app: desktop services share this package namespace and resources.

"""Package byte-preserving tools for building and reading canonical MBOX archives."""
