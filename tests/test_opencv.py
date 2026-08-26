# -*- coding: utf-8 -*-
"""opencv.py —— 全仓库唯一与平台无关的模块，按原样测，不打任何桩。"""

import cv2
import numpy as np
import pytest

from opencv import _brief, _cv_read_image, cv_find_template


# 夹具一律用「有纹理」的图案，绝不用纯色块。TM_CCOEFF_NORMED 的分母是两边的
# 方差，纯色对纯色会退化 —— 见 TestUniformRegionsAreDegenerate。


def _texture(size=20, seed=0):
    return np.random.default_rng(seed).integers(0, 255, (size, size, 3), dtype=np.uint8)


@pytest.fixture
def screen():
    """有纹理的背景，保证匹配分数是有意义的。"""
    return np.random.default_rng(99).integers(0, 60, (200, 200, 3), dtype=np.uint8)


def _write_template(path, size=20, seed=0):
    tpl = _texture(size, seed)
    cv2.imwrite(str(path), cv2.cvtColor(tpl, cv2.COLOR_RGB2BGR))
    return tpl


class TestReadFailuresAreLoud:
    """#4 的核心：读不到模板曾经完全静默，于是页面被判成 UNKNOWN。"""

    def test_missing_file_logs_the_path(self, tmp_path, log_file):
        missing = tmp_path / "nope.png"
        assert _cv_read_image(str(missing)) is None
        assert str(missing) in log_file()
        assert "FileNotFoundError" in log_file()

    def test_empty_file_is_distinguished_from_missing(self, tmp_path, log_file):
        empty = tmp_path / "empty.png"
        empty.write_bytes(b"")
        assert _cv_read_image(str(empty)) is None
        assert "图片为空或不存在" in log_file()

    def test_corrupt_file_is_distinguished_from_empty(self, tmp_path, log_file):
        corrupt = tmp_path / "corrupt.png"
        corrupt.write_bytes(b"definitely not a png")
        assert _cv_read_image(str(corrupt)) is None
        assert "解码失败" in log_file()

    def test_unreadable_template_surfaces_through_find(self, tmp_path, screen, log_file):
        missing = tmp_path / "nope.png"
        assert cv_find_template(screen, str(missing)) is None
        # 既要报底层原因，也要报匹配层拒绝
        assert "读取图片失败" in log_file()
        assert "模板匹配输入无效" in log_file()


class TestMatching:
    def test_finds_an_exact_patch(self, tmp_path, screen):
        tpl = _write_template(tmp_path / "t.png")
        screen[50:70, 60:80] = tpl
        result = cv_find_template(screen, str(tmp_path / "t.png"))
        assert result is not None
        x, y, w, h, score = result
        assert (x, y) == (60, 50)
        assert (w, h) == (20, 20)
        assert score == pytest.approx(1.0, abs=1e-3)

    def test_absent_patch_returns_none(self, tmp_path, screen):
        _write_template(tmp_path / "t.png", seed=7)
        assert cv_find_template(screen, str(tmp_path / "t.png")) is None

    def test_threshold_is_honoured(self, tmp_path, screen):
        tpl = _write_template(tmp_path / "t.png")
        screen[50:70, 60:80] = tpl
        assert cv_find_template(screen, str(tmp_path / "t.png"), threshold=1.01) is None


class TestResolutionSensitivity:
    """#12 / 功能 2 的回归基线。

    这些断言的是**当前的缺陷**，不是期望的行为：模板在某个分辨率下截取，画面换了
    分辨率就匹配不上。等多尺度匹配落地后，它们应该翻转成能找到。
    """

    def test_scaled_down_screen_no_longer_matches(self, tmp_path):
        big = np.random.default_rng(5).integers(0, 255, (200, 200, 3), dtype=np.uint8)
        cv2.imwrite(str(tmp_path / "t.png"), cv2.cvtColor(big[50:90, 60:100], cv2.COLOR_RGB2BGR))
        shrunk = cv2.resize(big, (133, 133), interpolation=cv2.INTER_AREA)
        assert cv_find_template(shrunk, str(tmp_path / "t.png")) is None

    def test_scaled_up_screen_no_longer_matches(self, tmp_path):
        base = np.random.default_rng(6).integers(0, 255, (200, 200, 3), dtype=np.uint8)
        cv2.imwrite(str(tmp_path / "t.png"), cv2.cvtColor(base[50:90, 60:100], cv2.COLOR_RGB2BGR))
        grown = cv2.resize(base, (300, 300), interpolation=cv2.INTER_LINEAR)
        assert cv_find_template(grown, str(tmp_path / "t.png")) is None


class TestUniformRegionsAreDegenerate:
    """TM_CCOEFF_NORMED 在无纹理区域上会退化，这不是理论问题。

    归一化相关系数的分母是两边的方差。模板和画面都平坦时，分子分母同时趋零，
    OpenCV 给出 1.0 —— 满分。

    实际后果：PrintWindow 对 D3D 窗口常常返回**全黑**位图（PLANNING.md §0.2）。
    全黑画面配上一个低纹理模板，得到的不是"认不出页面"，而是一次**高置信度的
    误判**。所以截图必须先验空帧，不能指望匹配环节兜住。
    """

    def test_flat_template_on_black_frame_scores_a_perfect_match(self, tmp_path):
        flat = np.zeros((20, 20, 3), dtype=np.uint8)
        flat[:] = (0, 0, 255)
        cv2.imwrite(str(tmp_path / "flat.png"), cv2.cvtColor(flat, cv2.COLOR_RGB2BGR))
        black = np.zeros((200, 200, 3), dtype=np.uint8)

        result = cv_find_template(black, str(tmp_path / "flat.png"))
        assert result is not None, "退化行为消失了 —— 空帧检查的理由要重新评估"
        assert result[4] == pytest.approx(1.0, abs=1e-6)

    def test_textured_template_on_black_frame_scores_zero(self, tmp_path):
        cv2.imwrite(str(tmp_path / "tex.png"), cv2.cvtColor(_texture(seed=3), cv2.COLOR_RGB2BGR))
        black = np.zeros((200, 200, 3), dtype=np.uint8)
        assert cv_find_template(black, str(tmp_path / "tex.png")) is None


def test_brief_keeps_paths_and_collapses_objects():
    assert _brief("template/flag_battle.png") == "template/flag_battle.png"
    assert _brief(np.zeros((2, 2, 3), dtype=np.uint8)) == "ndarray"


# --- #9：我们真正依赖的 cv2 表面 -------------------------------------------

CV2_SURFACE = (
    "imdecode", "imread", "imwrite", "cvtColor", "resize",
    "matchTemplate", "minMaxLoc",
    "IMREAD_COLOR", "INTER_LINEAR", "INTER_AREA", "TM_CCOEFF_NORMED",
    "COLOR_RGB2BGR", "COLOR_BGR2RGB",
)


class TestCv2ApiSurface:
    """本仓库只用到 cv2 的 13 个符号，全是多年未变的核心 API。

    4.14.0.94 和 5.0.0.93 实测行为完全一致（见 requirements.txt 的注释），所以
    pin 版本不影响结果。真正会在升级时炸掉的，是某个符号被挪走或改名 —— 这条
    就守这个，而且不依赖任何具体版本号。

    刻意**不**断言具体的匹配分数：SIMD 路径在不同 CPU 上可能有末位差异，把分数
    钉死会做出一条换台机器就红的测试。
    """

    def test_every_symbol_we_use_still_exists(self):
        import cv2
        missing = [name for name in CV2_SURFACE if not hasattr(cv2, name)]
        assert not missing, f"这个 opencv 版本没有: {missing}"

    def test_the_list_matches_what_the_code_actually_calls(self):
        """列表要是漂了，这条测试就只是在自我安慰。"""
        import re
        import subprocess
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        files = subprocess.run(["git", "ls-files", "*.py"], cwd=root,
                               capture_output=True, text=True).stdout.split()
        used = set()
        for rel in files:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
            used.update(re.findall(r"cv2\.([A-Za-z_0-9]+)", text))
        assert used <= set(CV2_SURFACE), \
            f"代码用了 CV2_SURFACE 里没列的符号: {sorted(used - set(CV2_SURFACE))}"
