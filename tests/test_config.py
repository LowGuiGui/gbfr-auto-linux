# -*- coding: utf-8 -*-
"""config.py —— 纯标准库，与平台无关。配置坏掉绝不能让程序起不来。"""

import tomllib

import pytest

import config


def test_generates_a_commented_default_on_first_run(tmp_path, log_file):
    cfg = config.load(str(tmp_path))
    written = (tmp_path / config.CONFIG_FILENAME)
    assert written.exists()
    text = written.read_text(encoding="utf-8")
    assert "#" in text, "默认配置必须带注释，否则没法手改"
    assert cfg.get("loop.poll_interval_ms") == 3000


def test_the_generated_default_parses_and_matches_DEFAULTS(tmp_path, log_file):
    """带注释的模板和 DEFAULTS 是两份东西，必须证明它们没有漂移。

    注意用 _decode 而不是直接 tomllib.load —— 生成的文件带 BOM（记事本要它），
    而 tomllib 不认 BOM。这条测试曾经因此变红，那正是它该做的事。
    """
    config.load(str(tmp_path))
    raw = (tmp_path / config.CONFIG_FILENAME).read_bytes()
    text, note = config._decode(raw)
    assert note is None
    assert tomllib.loads(text) == config.DEFAULTS


def test_the_file_we_write_is_a_file_we_can_read(tmp_path, log_file):
    """最要紧的往返：自己生成的配置，自己必须读得回来。"""
    config.load(str(tmp_path))
    reloaded = config.load(str(tmp_path))
    assert reloaded.as_dict() == config.DEFAULTS
    assert "格式错误" not in log_file()
    assert "不是 UTF-8" not in log_file()


def test_existing_file_is_not_overwritten(tmp_path, log_file):
    path = tmp_path / config.CONFIG_FILENAME
    path.write_text("[loop]\npoll_interval_ms = 1234\n", encoding="utf-8")
    cfg = config.load(str(tmp_path))
    assert cfg.get("loop.poll_interval_ms") == 1234
    assert path.read_text(encoding="utf-8") == "[loop]\npoll_interval_ms = 1234\n"


def test_partial_file_merges_over_defaults(tmp_path, log_file):
    (tmp_path / config.CONFIG_FILENAME).write_text(
        "[detect]\nthreshold = 0.95\n", encoding="utf-8"
    )
    cfg = config.load(str(tmp_path))
    assert cfg.get("detect.threshold") == 0.95
    assert cfg.get("detect.log_scores") is False       # 未提及的键保持默认
    assert cfg.get("keys.move") == "w"                 # 未提及的段落也是


class TestBadInputNeverCrashes:
    def test_malformed_toml_falls_back(self, tmp_path, log_file):
        (tmp_path / config.CONFIG_FILENAME).write_text("[loop\nbroken", encoding="utf-8")
        cfg = config.load(str(tmp_path))
        assert cfg.get("loop.poll_interval_ms") == 3000
        assert "格式错误" in log_file()

    def test_unknown_section_is_reported_and_dropped(self, tmp_path, log_file):
        (tmp_path / config.CONFIG_FILENAME).write_text(
            "[nonsense]\nfoo = 1\n", encoding="utf-8"
        )
        cfg = config.load(str(tmp_path))
        assert "nonsense" not in cfg.as_dict()
        assert "未知的段落" in log_file()

    def test_unknown_key_is_reported_and_dropped(self, tmp_path, log_file):
        (tmp_path / config.CONFIG_FILENAME).write_text(
            "[loop]\nnot_a_real_key = 1\n", encoding="utf-8"
        )
        config.load(str(tmp_path))
        assert "未知的键 loop.not_a_real_key" in log_file()

    def test_wrong_type_falls_back_and_says_so(self, tmp_path, log_file):
        (tmp_path / config.CONFIG_FILENAME).write_text(
            '[loop]\npoll_interval_ms = "fast"\n', encoding="utf-8"
        )
        cfg = config.load(str(tmp_path))
        assert cfg.get("loop.poll_interval_ms") == 3000
        assert "类型应为 int" in log_file()

    def test_bool_is_not_accepted_where_an_int_is_expected(self, tmp_path, log_file):
        """bool 是 int 的子类；不特判的话 true 会被当成合法整数塞进去。"""
        (tmp_path / config.CONFIG_FILENAME).write_text(
            "[loop]\npoll_interval_ms = true\n", encoding="utf-8"
        )
        cfg = config.load(str(tmp_path))
        assert cfg.get("loop.poll_interval_ms") == 3000

    def test_int_is_not_accepted_where_a_bool_is_expected(self, tmp_path, log_file):
        (tmp_path / config.CONFIG_FILENAME).write_text(
            "[detect]\nlog_scores = 1\n", encoding="utf-8"
        )
        cfg = config.load(str(tmp_path))
        assert cfg.get("detect.log_scores") is False

    def test_unwritable_directory_still_yields_defaults(self, tmp_path, log_file):
        cfg = config.load(str(tmp_path / "does" / "not" / "exist"))
        assert cfg.get("detect.threshold") == 0.8
        assert "写入默认配置失败" in log_file()


class TestAccess:
    def test_missing_key_raises_without_a_default(self, tmp_path, log_file):
        cfg = config.load(str(tmp_path))
        with pytest.raises(KeyError):
            cfg.get("detect.nope")

    def test_missing_key_returns_the_given_default(self, tmp_path, log_file):
        cfg = config.load(str(tmp_path))
        assert cfg.get("detect.nope", 42) == 42

    def test_section_returns_a_copy(self, tmp_path, log_file):
        cfg = config.load(str(tmp_path))
        section = cfg.section("keys")
        section["move"] = "TAMPERED"
        assert cfg.get("keys.move") == "w"


class TestWhatNotepadDoesToTheFile:
    """配置文件是给人用记事本改的，它存盘的方式会把 tomllib 直接干掉。

    两种都曾经导致同一个最糟的结果：退回默认值，于是用户改了半天配置一点不生效。
    """

    def test_a_bom_does_not_defeat_the_parser(self, tmp_path, log_file):
        """记事本存 UTF-8 默认加 BOM，而 tomllib 不认 BOM。"""
        (tmp_path / config.CONFIG_FILENAME).write_bytes(
            b"\xef\xbb\xbf" + b"[loop]\npoll_interval_ms = 1234\n"
        )
        cfg = config.load(str(tmp_path))
        assert cfg.get("loop.poll_interval_ms") == 1234, "带 BOM 的设置必须生效"

    def test_ansi_saved_file_is_recovered_and_flagged(self, tmp_path, log_file, monkeypatch):
        """记事本的 ANSI 存盘：能救就救，但必须说出来。"""
        monkeypatch.setattr("locale.getpreferredencoding", lambda *a: "gbk")
        (tmp_path / config.CONFIG_FILENAME).write_bytes(
            "[keys]\nmove = \"z\"  # 前进键\n".encode("gbk")
        )
        cfg = config.load(str(tmp_path))
        assert cfg.get("keys.move") == "z"
        assert "不是 UTF-8" in log_file()
        assert "另存为" in log_file()

    def test_undecodable_bytes_fall_back_loudly(self, tmp_path, log_file, monkeypatch):
        monkeypatch.setattr("locale.getpreferredencoding", lambda *a: "ascii")
        (tmp_path / config.CONFIG_FILENAME).write_bytes(b"[loop]\n\xff\xfe\x00bad\n")
        cfg = config.load(str(tmp_path))
        assert cfg.get("loop.poll_interval_ms") == 3000
        assert "既不是 UTF-8" in log_file()

    def test_the_generated_file_carries_a_bom(self, tmp_path, log_file):
        """没有 BOM 的话，记事本会按本地代码页解释，中文注释全是乱码。"""
        config.load(str(tmp_path))
        assert (tmp_path / config.CONFIG_FILENAME).read_bytes()[:3] == b"\xef\xbb\xbf"

    def test_the_header_says_which_encoding_to_use(self, tmp_path, log_file):
        config.load(str(tmp_path))
        text = (tmp_path / config.CONFIG_FILENAME).read_text(encoding="utf-8-sig")
        assert "UTF-8" in text
        assert "Save this file as UTF-8" in text, "英文系统的用户也得看得懂这句"
