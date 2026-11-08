# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise real future notifications used by owner-thread progress and cancellation.
# Producers publish state first, then wake every subscribed consumer.
# A later consumer must subscribe to a fresh notification rather than an old tick.
# Reentrant delivery must not hold a producer lock around arbitrary callbacks.
# These checks establish ordering without scheduling sleeps or background timers.
# Expiring result waits remain failures, never evidence that work happened.
"""VALIDATION: reusable signals broadcast actual state changes without lost wakeups."""
from mailarchiver.change_signal import ChangeEvent, ChangeSignal


def test_notifications_wake_all_existing_subscribers_and_keep_a_fresh_generation() -> None:
    signal = ChangeSignal()
    first, second = signal.ready, signal.ready
    assert first is second and not first.done()
    signal.notify()
    assert first.result(timeout=1) is None and second.result(timeout=1) is None
    later = signal.ready
    assert later is not first and not later.done()
    signal.notify()
    assert later.result(timeout=1) is None


def test_callbacks_can_publish_the_next_generation_without_a_lock_cycle() -> None:
    signal = ChangeSignal()
    first = signal.ready
    delivered = []

    def changed(_future: object) -> None:
        next_change = signal.ready
        signal.notify()
        delivered.append(next_change.result(timeout=1))

    first.add_done_callback(changed)
    signal.notify()
    assert delivered == [None]


def test_cancellation_flag_is_published_before_its_notification() -> None:
    stop = ChangeEvent()
    ready = stop.changed.ready
    observed = []
    ready.add_done_callback(lambda _future: observed.append(stop.is_set()))
    stop.set()
    assert observed == [True] and stop.is_set()
