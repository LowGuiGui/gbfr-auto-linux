#!/bin/sh
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Add GBFR 自动刷取 to this user's application menu. The menu entry starts the bundle that
# the link $data/gbfr-auto-linux/current points at, and this script points the link here:
# running another bundle's install.sh switches versions and rolls back by moving the link
# alone. The entry itself stays the same, because GNOME Shell's app grid keeps launching the
# command an entry had when its icon was made. Nothing outside the link, the menu entry and
# the icon is written.
set -eu
here=$(dirname "$(readlink -f "$0")")
id=io.github.lowguigui.GbfrAutoLinux
data=${XDG_DATA_HOME:-$HOME/.local/share}
link=$data/gbfr-auto-linux/current
case "$link" in
    *[\"\`\$\\%]*)
        echo "菜单项要写的路径里有 \" \` \$ \\ 或 %，写不进菜单项：$link" >&2
        exit 1;;
esac
# A build that stopped part-way leaves this script without the runner or the manifest the
# build writes last; pointing the menu at it would replace a working version with one that
# cannot run.
for file in bundle.json .venv/bin/python "icons/$id.svg"; do
    if [ ! -f "$here/$file" ]; then
        echo "这个版本目录不完整（缺少 $file），可能是构建中途停了，没有安装。请重新构建一个版本目录。" >&2
        exit 1
    fi
done
icons=$data/icons/hicolor/scalable/apps
mkdir -p "$data/applications" "$icons" "${link%/*}"
cp "$here/icons/$id.svg" "$icons/$id.svg.tmp"
mv "$icons/$id.svg.tmp" "$icons/$id.svg"
# GTK and GNOME Shell trust an icon-theme.cache that is not older than the theme folder, and
# a new file in scalable/apps leaves that folder's time alone: an old cache would hide the
# icon (a gear in the app grid). A newer folder retires the cache, and running programs look
# again within seconds.
touch -c "$data/icons/hicolor"
entry=$data/applications/$id.desktop
# The file is named after the app ID: GNOME matches the window to its menu entry and
# icon by that ID, and shows the window's notifications only for an app it can find.
cat > "$entry.tmp" <<EOF
[Desktop Entry]
Type=Application
Name=GBFR 自动刷取
Comment=用游戏自带的“再次挑战”连刷素材，前台挂机观看
Exec="$link/gbfr-control"
Icon=$id
Terminal=false
Categories=Game;
StartupNotify=true
EOF
mv "$entry.tmp" "$entry"
# The link moves last, so a step that fails above leaves the menu on the version it had.
# One rename replaces the link, so a launch never finds it missing.
ln -sfn "$here" "$link.tmp"
mv -T "$link.tmp" "$link"
echo "已添加到应用菜单：$entry"
echo "菜单项通过 $link 启动，它现在指向：$here"
