# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Public update identity shared by the packaged app and release publisher.

Purpose: Define public update trust, feed and package compatibility identity.

How it works:
Share the pinned feed/key and platform identity metadata between installed clients and release
tooling.

Fundamental dependents:

- ``windows_update_feed``: eligible updates depend on these public trust/identity values.
- ``scripts.update_appcast``: publication must sign and label artifacts for that same identity.
"""

SPARKLE_VERSION = "2.10.0"
SPARKLE_FEED_URL = "https://simsong.github.io/email-collection-toolkit/updates/mac/appcast.xml"
SPARKLE_PUBLIC_KEY = "qkdXdvt9A3YjGYwpENGrEEBY7kp3hU+TIjhCz/b7cjw="
MINIMUM_MACOS_VERSION = "15.0"
PACKAGE_NAMESPACE = "https://simsong.github.io/email-collection-toolkit/updates"
PACKAGE_IDENTITY = "ECT.PythonReader"
