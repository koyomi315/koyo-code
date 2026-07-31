"""state.py 状态对象单测。"""

from __future__ import annotations

import threading
from pathlib import Path

from koyocode.compact.state import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    new_session_context,
)


def test_new_session_context(tmp_path):
    ctx = new_session_context(str(tmp_path))
    ts, sep, hexpart = ctx.session_id.partition("-")
    assert sep == "-"
    assert ts.isdigit()
    assert len(hexpart) == 8
    assert Path(ctx.spill_dir).exists()


def test_new_session_context_unique(tmp_path):
    a = new_session_context(str(tmp_path))
    b = new_session_context(str(tmp_path))
    assert a.session_id != b.session_id


def test_new_session_context_rand_fail_fallback(monkeypatch, tmp_path):
    import koyocode.compact.state as state_mod

    def boom(_n):
        raise RuntimeError("no entropy")

    monkeypatch.setattr(state_mod.secrets, "token_hex", boom)
    ctx = new_session_context(str(tmp_path))  # 不应抛异常
    assert "-" in ctx.session_id


def test_decide_once_freeze_kept():
    s = ContentReplacementState()
    assert s.decide_once("id1", "orig", lambda: ("kept", "")) == "orig"
    # 已 Seen kept：返回原文，decide 不再被调用（即使回调返回 replaced 也不翻转）
    assert s.decide_once("id1", "orig", lambda: ("replaced", "X")) == "orig"
    assert s.is_seen("id1")


def test_decide_once_freeze_replaced():
    s = ContentReplacementState()
    assert s.decide_once("id1", "orig", lambda: ("replaced", "PREVIEW")) == "PREVIEW"
    # 复用账本存量，不重新构造
    assert s.decide_once("id1", "orig", lambda: ("replaced", "OTHER")) == "PREVIEW"


def test_decide_once_skip_does_not_mark():
    s = ContentReplacementState()
    assert s.decide_once("id1", "orig", lambda: ("skip", "")) == "orig"
    assert not s.is_seen("id1")
    # 下一轮仍可决策
    assert s.decide_once("id1", "orig", lambda: ("replaced", "P")) == "P"


def test_recovery_state_snapshot_order():
    s = RecoveryState()
    s.record_file("/a.txt", "a")
    s.record_file("/b.txt", "b")
    s.record_file("/c.txt", "c")
    assert [r.path for r in s.snapshot()] == ["/c.txt", "/b.txt", "/a.txt"]


def test_recovery_state_snapshot_copy():
    s = RecoveryState()
    s.record_file("/a.txt", "a")
    snap = s.snapshot()
    snap.clear()
    assert len(s.snapshot()) == 1  # 修改返回列表不影响内部


def test_recovery_state_concurrent():
    s = RecoveryState()
    n = 50
    barrier = threading.Barrier(n)

    def worker(i):
        barrier.wait()
        for j in range(20):
            s.record_file(f"/f{i}_{j}.txt", "x")
            s.snapshot()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(s.snapshot()) == n * 20


def test_auto_tracking_consecutive_budget():
    cb = CompactCircuitBreaker()
    cb.record_failure()
    assert not cb.tripped()
    cb.record_failure()
    assert not cb.tripped()
    cb.record_success()  # 清零
    cb.record_failure()
    assert not cb.tripped()
    cb.record_failure()
    assert not cb.tripped()
    cb.record_failure()
    assert cb.tripped()


def test_auto_tracking_concurrent():
    cb = CompactCircuitBreaker()
    n = 50
    barrier = threading.Barrier(n)

    def worker():
        barrier.wait()
        for _ in range(10):
            cb.record_failure()
            cb.record_success()
            cb.tripped()

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
