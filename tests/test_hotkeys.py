# -*- coding: utf-8 -*-
"""#13 —— 热键回调跑在 pynput 监听线程上，任何逸出的异常都会杀死 Listener。"""

import threading

import main


class Stub:
    """只借 _on_press，不构造 App —— App 需要真的 Tk root。"""

    _on_press = main.App._on_press

    def __init__(self, root):
        self.root = root
        self._hotkey_error_logged = False
        self.f1 = 0
        self.f2 = 0

    def _on_f1(self):
        self.f1 += 1

    def _on_f2(self):
        self.f2 += 1


def test_f1_and_f2_are_marshalled_to_the_tk_thread(fake_root, key, log_file):
    root = fake_root()
    stub = Stub(root)
    stub._on_press(key.f1)
    stub._on_press(key.f2)
    assert (stub.f1, stub.f2) == (1, 1)
    # 关键：动作是经 after() 调度的，不是在监听线程上直接跑的
    assert [c[2].__name__ for c in root.calls] == ["_on_f1", "_on_f2"]


def test_removed_hotkeys_schedule_nothing(fake_root, key, log_file):
    """F3 曾经抛 TypeError 并干掉整套热键。现在它没有任何分支。"""
    root = fake_root()
    stub = Stub(root)
    for k in (key.f3, key.f4, "z"):
        stub._on_press(k)
    assert root.calls == []


def test_a_throwing_handler_never_escapes(fake_root, key, log_file):
    """逸出 == Listener 停止 == 连 F1/F2 一起失效。"""
    stub = Stub(fake_root(explode=True))
    for _ in range(10):
        stub._on_press(key.f1)          # 不抛 == 监听线程存活


def test_repeated_failures_do_not_flood_the_log(fake_root, key, log_file):
    """全局监听会收到用户在任何窗口的每一次按键。"""
    stub = Stub(fake_root(explode=True))
    for _ in range(10):
        stub._on_press(key.f1)
    text = log_file()
    assert text.count("热键处理失败") == 1
    assert text.count("热键处理再次失败") == 9


def test_failures_from_a_worker_thread_are_handled(fake_root, key, log_file):
    stub = Stub(fake_root(explode=True))
    errors = []

    def press():
        try:
            stub._on_press(key.f1)
        except BaseException as exc:      # noqa: BLE001 - 测试要捕获一切
            errors.append(exc)

    t = threading.Thread(target=press, name="listener")
    t.start()
    t.join()
    assert errors == []
