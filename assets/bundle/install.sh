#!/bin/sh
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Add GBFR 自动刷取 to this user's application menu, pointing at this bundle. Running
# another bundle's install.sh moves the entry there: that is how versions are switched
# and rolled back. Nothing outside the menu entry and the icon is written.
set -eu
here=$(dirname "$(readlink -f "$0")")
case "$here" in
    *[\"\`\$\\%]*)
        echo "这个文件夹的路径里有 \" \` \$ \\ 或 %，写不进菜单项：$here" >&2
        exit 1;;
esac
id=io.github.lowguigui.GbfrAutoLinux
data=${XDG_DATA_HOME:-$HOME/.local/share}
icons=$data/icons/hicolor/scalable/apps
mkdir -p "$data/applications" "$icons"
cp "$here/icons/$id.svg" "$icons/$id.svg.tmp"
mv "$icons/$id.svg.tmp" "$icons/$id.svg"
entry=$data/applications/$id.desktop
# The file is named after the app ID: GNOME matches the window to its menu entry and
# icon by that ID, and shows the window's notifications only for an app it can find.
cat > "$entry.tmp" <<EOF
[Desktop Entry]
Type=Application
Name=GBFR 自动刷取
Comment=用游戏自带的“再次挑战”连刷素材，前台挂机观看
Exec="$here/gbfr-control"
Icon=$id
Terminal=false
Categories=Game;
StartupNotify=true
EOF
mv "$entry.tmp" "$entry"
echo "已添加到应用菜单：$entry"
echo "指向：$here"
