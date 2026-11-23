#!/bin/sh
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Remove GBFR 自动刷取 from the application menu, but only when the entry points at this
# bundle: after another version's install.sh, the entry is that version's. Settings,
# history and logs are kept (see the guide for their folders).
set -eu
here=$(dirname "$(readlink -f "$0")")
id=io.github.lowguigui.GbfrAutoLinux
data=${XDG_DATA_HOME:-$HOME/.local/share}
entry=$data/applications/$id.desktop
if [ ! -e "$entry" ]; then
    echo "应用菜单里没有这个程序的菜单项。"
    exit 0
fi
if ! grep -qxF "Exec=\"$here/gbfr-control\"" "$entry"; then
    echo "菜单项指向另一个版本目录，没有删：$entry" >&2
    grep '^Exec=' "$entry" >&2 || true
    exit 1
fi
rm -f "$entry" "$data/icons/hicolor/scalable/apps/$id.svg"
touch "$data/icons/hicolor"  # as install.sh: running programs look again, no stale cache
echo "已从应用菜单删除。设置、历史和日志没有动。"
