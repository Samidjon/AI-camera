import threading
import time

import numpy as np
import pytest

from aci_agent.video.connection_monitor import ConnectionMonitor
from aci_agent.video.frame_buffer import Frame, FrameQueue, LatestFrameBuffer


def _frame(seq: int) -> Frame:
    return Frame(seq, time.time(), np.zeros((2, 2, 3), np.uint8))


def test_queue_is_bounded_and_drops_oldest():
    q = FrameQueue(3)
    for i in range(1, 11):
        q.put(_frame(i))
    assert len(q) == 3
    assert q.dropped == 7
    assert [q.get(0).seq for _ in range(3)] == [8, 9, 10]
    assert q.get(timeout=0.01) is None


def test_queue_rejects_zero_size():
    with pytest.raises(ValueError):
        FrameQueue(0)


def test_latest_buffer_keeps_only_newest_and_notifies():
    buf = LatestFrameBuffer()
    got: list[int] = []

    def waiter():
        f = buf.wait_newer(0, 2)
        got.append(f.seq if f else -1)

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.05)
    buf.publish(_frame(1))
    buf.publish(_frame(2))
    t.join(2)
    assert got and got[0] in (1, 2)
    assert buf.latest().seq == 2
    assert buf.wait_newer(2, 0.05) is None  # nothing newer


def test_jpeg_cache_is_tied_to_frame():
    buf = LatestFrameBuffer()
    buf.publish(_frame(5))
    buf.store_jpeg(5, 640, b"jpeg")
    assert buf.cached_jpeg(5, 640) == b"jpeg"
    assert buf.cached_jpeg(5, 320) is None
    buf.publish(_frame(6))
    buf.store_jpeg(5, 640, b"stale")  # stale encode for an older frame is ignored
    assert buf.cached_jpeg(6, 640) is None


def test_close_wakes_waiters():
    buf = LatestFrameBuffer()
    threading.Timer(0.05, buf.close).start()
    started = time.monotonic()
    assert buf.wait_newer(0, 3) is None
    assert time.monotonic() - started < 1


def test_connection_monitor_fps_and_staleness():
    m = ConnectionMonitor(stale_after_s=1.0, smoothing=1.0)
    assert m.fps is None and m.is_stale(0)
    for i in range(5):
        m.on_frame(10 + i * 0.1)
    assert m.fps == pytest.approx(10.0, rel=0.01)
    assert not m.is_stale(10.5)
    assert m.is_stale(12.0)
