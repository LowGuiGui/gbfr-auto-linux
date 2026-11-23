#!/bin/sh
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Remove GBFR 自动刷取 from the application menu, but only while the menu starts this bundle
# (install.sh's link points here) or a bundle that no longer exists: after another version's
# install.sh, the menu is that version's. Settings, history and logs are kept (see the guide
# for their folders).
set -eu
here=$(dirname "$(readlink -f "$0")")
id=io.github.lowguigui.GbfrAutoLinux
data=${XDG_DATA_HOME:-$HOME/.local/share}
link=$data/gbfr-auto-linux/current
if [ ! -L "$link" ]; then
    echo "没有 $link，没有删任何东西。"
    exit 0
fi
if [ -e "$link" ] && [ "$(readlink -f "$link")" != "$here" ]; then
    echo "菜单项现在启动另一个版本目录，没有删：$(readlink "$link")" >&2
    exit 1
fi
rm -f "$data/applications/$id.desktop" "$data/icons/hicolor/scalable/apps/$id.svg" "$link"
rmdir "${link%/*}" 2>/dev/null || true
# As install.sh: running programs look again, and no stale cache hides anything. -c: if the
# folder is gone, never leave a file named hicolor where a later install needs the folder.
touch -c "$data/icons/hicolor"
echo "已从应用菜单删除。设置、历史和日志没有动。"
