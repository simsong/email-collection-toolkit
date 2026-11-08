# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Describe the two native WebKit constants used by the Cocoa reply adapter.
# PyObjC publishes these constants from framework metadata at runtime.
# The adapter distinguishes an unsupported immediate return from execution errors.
# Promise values still arrive through pywebview's separate asynchronous callback.
# This stub has no runtime behavior and does not replace the framework.
WKErrorDomain: str
WKErrorJavaScriptResultTypeIsUnsupported: int
