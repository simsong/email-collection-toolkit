<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Native type interfaces

These development-only stubs describe dynamic native APIs used by the desktop
application, packaging scripts, and native probes. PyObjC and pythonnet expose
these APIs dynamically, so they are absent from their Python source interfaces. Both ty and
Pyright load this directory; it is not installed into the runtime package.

`pypff` describes only the libpff-python 20260926 native APIs used by the
in-process Outlook reader. Signatures are checked against the upstream C binding
and exercised with genuine PST/OST fixtures by `make test-pff`.

`clr` declares only the Windows adapter's `AddReference(name: str)` call.
The pinned [pythonnet 3.1.0 implementation](https://github.com/pythonnet/pythonnet/blob/v3.1.0/src/runtime/Types/ClrModule.cs#L88-L122)
accepts a string and returns `System.Reflection.Assembly`. That unused return
value is an opaque `object` here; the stub grants no unchecked CLR members.
`make test-desktop-lifecycle` checks ty and Pyright against both host and Windows
platform definitions; native Windows execution remains a separate CI gate.

Keep parameter and return types precise. Cocoa `id` values whose concrete class
depends on the installed window or delegate use `Any` at that native boundary.
Do not add a catch-all module or class `__getattr__` to hide missing APIs. Extend
the relevant selector declaration when native usage changes, and validate native
behavior separately from static checks.
