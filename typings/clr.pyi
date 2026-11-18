# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Describe pythonnet's dynamically exported assembly-loading function.
# Pythonnet installs this API after CLR runtime initialization.
# The application passes an assembly name before importing its namespaces.
# The returned System.Reflection.Assembly is unused and remains opaque.
# This development-only interface neither loads nor replaces the CLR runtime.
# Keep the declaration limited to the actual API used by the Windows adapter.
"""Assembly loading exported dynamically by pythonnet's clr module."""


def AddReference(name: str) -> object: ...
