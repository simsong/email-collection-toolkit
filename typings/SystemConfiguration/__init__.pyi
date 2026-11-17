# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Describe the PyObjC calls used by the desktop network observer.
# The system store supplies scoped keys for current network configuration.
# Callbacks run through its Core Foundation source and receive changed keys.
# The stubs cover the observer's calls and do not replace the native framework.
"""SystemConfiguration bindings used by macOS lifecycle monitoring."""

from collections.abc import Callable, Sequence

kSCDynamicStoreDomainState: str
kSCEntNetIPv4: str
kSCEntNetIPv6: str
kSCEntNetDNS: str

class SCDynamicStoreRef: ...

def SCDynamicStoreCreate(allocator: None, name: str,
                         callback: Callable[[SCDynamicStoreRef, Sequence[str], None], None],
                         context: None) -> SCDynamicStoreRef | None: ...
def SCDynamicStoreKeyCreateNetworkGlobalEntity(allocator: None, domain: str, entity: str) -> str: ...
def SCDynamicStoreCopyValue(store: SCDynamicStoreRef, key: str) -> object | None: ...
def SCDynamicStoreSetNotificationKeys(store: SCDynamicStoreRef, keys: Sequence[str], patterns: None) -> bool: ...
def SCDynamicStoreCreateRunLoopSource(allocator: None, store: SCDynamicStoreRef, order: int) -> object | None: ...
