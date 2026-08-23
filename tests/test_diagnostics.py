# -*- coding: utf-8 -*-
"""检查点 1 的诊断能力：空帧守卫、匹配分数、异常帧、空跑。"""

import numpy as np
import pytest
from PIL import Image

import config
import main
from opencv import BLANK_FRAME_STD, cv_best_match, is_blank_frame


class TestBlankFrameGuard:
    """PLANNING.md §0.2 —— PrintWindow 对 D3D 窗口常常"成功"返回全黑位图。"""

    def test_all_black_ndarray_is_blank(self):
        assert is_blank_frame(np.zeros((100, 100, 3), dtype=np.uint8))

    def test_solid_colour_is_blank(self):
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        frame[:] = (17, 42, 99)
        assert is_blank_frame(frame)

    def test_all_black_pil_image_is_blank(self):
        assert is_blank_frame(Image.new("RGB", (64, 64), (0, 0, 0)))

    def test_none_is_blank(self):
        assert is_blank_frame(None)

    def test_real_content_is_not_blank(self):
        noise = np.random.default_rng(1).integers(0, 255, (100, 100, 3), dtype=np.uint8)
        assert not is_blank_frame(noise)

    def test_faint_content_is_not_blank(self):
        """几乎全黑但有真实内容的画面不能被误杀。"""
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        frame[40:60, 40:60] = 40
        assert not is_blank_frame(frame)

    def test_tolerance_is_the_documented_constant(self):
        frame = np.zeros((50, 50, 3), dtype=np.uint8)
        frame[0, 0] = 255
        assert is_blank_frame(frame, tolerance=BLANK_FRAME_STD) is (
            float(frame.mean(axis=2).std()) < BLANK_FRAME_STD
        )

    def test_the_guard_is_what_stops_the_false_positive(self, tmp_path):
        """守卫存在的理由：黑帧 + 低纹理模板 = 1.0 满分误判。"""
        import cv2
        flat = np.zeros((20, 20, 3), dtype=np.uint8)
        flat[:] = (0, 0, 255)
        cv2.imwrite(str(tmp_path / "flat.png"), flat)
        black = np.zeros((200, 200, 3), dtype=np.uint8)

        assert cv_best_match(black, str(tmp_path / "flat.png"))[4] == pytest.approx(1.0)
        assert is_blank_frame(black), "匹配环节救不了，只能靠截图自己验"


class TestBestMatchAlwaysReportsAScore:
    def test_score_is_returned_below_threshold(self, tmp_path):
        import cv2
        tpl = np.random.default_rng(2).integers(0, 255, (20, 20, 3), dtype=np.uint8)
        cv2.imwrite(str(tmp_path / "t.png"), tpl)
        screen = np.random.default_rng(3).integers(0, 255, (200, 200, 3), dtype=np.uint8)

        result = cv_best_match(screen, str(tmp_path / "t.png"))
        assert result is not None, "分数必须交出来，否则调阈值只能靠猜"
        assert 0.0 <= result[4] < 0.8

    def test_unreadable_template_still_returns_none(self, tmp_path, log_file):
        screen = np.zeros((50, 50, 3), dtype=np.uint8)
        assert cv_best_match(screen, str(tmp_path / "missing.png")) is None


class _Matcher:
    """借用 App._matches / _save_anomaly_frame。"""

    _matches = main.App._matches
    _save_anomaly_frame = main.App._save_anomaly_frame

    def __init__(self, screen, templates, overrides=None):
        self.cfg = config.Config(config._merged(overrides or {}))
        self.screen = screen
        self.temp_dir = templates
        self._anomalies_saved = 0


@pytest.fixture
def matcher(tmp_path):
    import cv2
    tpl = np.random.default_rng(4).integers(0, 255, (20, 20, 3), dtype=np.uint8)
    path = tmp_path / "flag.png"
    cv2.imwrite(str(path), tpl)
    screen = np.random.default_rng(8).integers(0, 60, (200, 200, 3), dtype=np.uint8)
    screen[30:50, 70:90] = cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)

    def build(overrides=None, hit=True):
        frame = screen if hit else np.random.default_rng(9).integers(
            0, 60, (200, 200, 3), dtype=np.uint8
        )
        return _Matcher(frame, {"flag": str(path)}, overrides)
    return build


class TestScoreLogging:
    def test_scores_are_silent_by_default(self, matcher, log_file):
        matcher()._matches("flag")
        assert "匹配得分" not in log_file()

    def test_scores_are_logged_when_enabled(self, matcher, log_file):
        assert matcher({"detect": {"log_scores": True}})._matches("flag") is True
        assert "匹配得分" in log_file()
        assert "flag" in log_file()

    def test_a_miss_still_logs_its_score(self, matcher, log_file):
        m = matcher({"detect": {"log_scores": True}}, hit=False)
        assert m._matches("flag") is False
        assert "匹配得分" in log_file(), "落空时的分数才是要调的那个"

    def test_threshold_comes_from_config(self, matcher, log_file):
        assert matcher({"detect": {"threshold": 0.99}})._matches("flag") is True
        assert matcher({"detect": {"threshold": 1.01}})._matches("flag") is False


class TestAnomalyFrames:
    def test_disabled_by_default(self, matcher, tmp_path, monkeypatch, log_file):
        monkeypatch.setattr(main, "exe_dir", lambda: str(tmp_path))
        matcher()._save_anomaly_frame()
        assert not (tmp_path / "anomalies").exists()

    def test_saves_a_frame_when_enabled(self, tmp_path, monkeypatch, log_file):
        monkeypatch.setattr(main, "exe_dir", lambda: str(tmp_path))
        m = _Matcher(
            Image.new("RGB", (32, 32), (10, 20, 30)), {},
            {"detect": {"save_anomaly_frames": True}},
        )
        m._save_anomaly_frame()
        saved = list((tmp_path / "anomalies").glob("unknown-*.png"))
        assert len(saved) == 1
        assert "已保存异常帧" in log_file()

    def test_stops_at_the_configured_limit(self, tmp_path, monkeypatch, log_file):
        """不设上限的话，一次通宵挂机能把磁盘塞满。"""
        monkeypatch.setattr(main, "exe_dir", lambda: str(tmp_path))
        m = _Matcher(
            Image.new("RGB", (8, 8), (1, 2, 3)), {},
            {"detect": {"save_anomaly_frames": True, "max_anomaly_frames": 3}},
        )
        for _ in range(10):
            m._save_anomaly_frame()
        assert len(list((tmp_path / "anomalies").glob("*.png"))) == 3

    def test_failure_to_save_is_reported_not_raised(self, tmp_path, monkeypatch, log_file):
        monkeypatch.setattr(main, "exe_dir", lambda: str(tmp_path))
        m = _Matcher(None, {}, {"detect": {"save_anomaly_frames": True}})
        m._save_anomaly_frame()          # screen 是 None
        assert "保存异常帧失败" in log_file()
