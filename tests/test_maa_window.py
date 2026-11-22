# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

from types import SimpleNamespace

import pytest

import maa_target


def window(pid, *, visible=True, title="Granblue Fantasy: Relink"):
    return SimpleNamespace(get_attributes=lambda: SimpleNamespace(map_state=2 if visible else 0),
                           get_wm_class=lambda: ("steam_app_881020",),
                           get_wm_name=lambda: title,
                           get_full_property=lambda *args: SimpleNamespace(value=[pid]))


def setup(monkeypatch, tmp_path, windows):
    for pid, executable in ((11, b"explorer.exe"), (12, b"conhost.exe"),
                            (13, b"Z:\\game\\granblue_fantasy_relink.exe")):
        path = tmp_path / str(pid)
        path.mkdir()
        (path / "cmdline").write_bytes(executable + b"\0")
    monkeypatch.setattr(maa_target.gamescope, "walk_windows", lambda display: iter(windows))
    monkeypatch.setattr(maa_target.gamescope, "nearest_gamescope", lambda pid: 7)
    monkeypatch.setattr(maa_target, "process_stamp", lambda pid, proc: "stamp")
    return SimpleNamespace(intern_atom=lambda name: name), SimpleNamespace(appid="881020", pid=7)


def test_hidden_wine_and_visible_console_do_not_win_selection(monkeypatch, tmp_path):
    game = window(13)
    display, target = setup(monkeypatch, tmp_path, [window(11, visible=False), window(12), game])
    assert maa_target.select_game_window(display, target, tmp_path) == (game, 13, "stamp")


@pytest.mark.parametrize("windows", [[window(11)], [window(13, visible=False)],
                                    [window(13, title="Other application")],
                                    [window(13), window(13)]])
def test_unavailable_or_ambiguous_game_window_is_refused(monkeypatch, tmp_path, windows):
    display, target = setup(monkeypatch, tmp_path, windows)
    with pytest.raises(maa_target.ProbeError, match="one visible GBFR"):
        maa_target.select_game_window(display, target, tmp_path)


def test_window_from_another_compositor_is_refused(monkeypatch, tmp_path):
    display, target = setup(monkeypatch, tmp_path, [window(13)])
    monkeypatch.setattr(maa_target.gamescope, "nearest_gamescope", lambda pid: 8)
    with pytest.raises(maa_target.ProbeError, match="one visible GBFR"):
        maa_target.select_game_window(display, target, tmp_path)
