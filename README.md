<!--
SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
SPDX-License-Identifier: GPL-2.0-or-later
-->

简体中文 | [English](README.en.md)

# GBFR Auto（Linux 版）

[![Project Status: WIP – Initial development is in progress, but there has not yet been a stable, usable release suitable for the public.](https://www.repostatus.org/badges/latest/wip.svg)](https://www.repostatus.org/#wip)

> [!IMPORTANT]
> **筹备中，自动战斗还不能对游戏运行。**
>
> - 这是 Windows 版 [LowGuiGui/gbfr_auto](https://github.com/LowGuiGui/gbfr_auto) 的 Linux 后继项目。Windows 版已暂停并归档。
> - 仓库里目前有从 Windows 版继承来的、与平台无关的核心，以及一个探测工具。Linux 平台层（截图、输入、热键）还没有写；探测工具已经对游戏跑过第一轮，结果见下面的进度表。

> [!WARNING]
> **AI 编写声明。** 本仓库由 AI 编程助手（Anthropic 的 Claude Code）在仓库所有者的指示下编写，**没有经过人工逐行审查**。
>
> - 在 Linux 上对游戏运行过的只有探测工具（2026-10-05，每一步各一次）；核心部分只有自动化测试。
> - 运行前请自行审阅，风险自负。
>
> 详见[关于 AI 编写](#关于-ai-编写)。

碧蓝幻想：Relink（Granblue Fantasy: Relink）自动战斗脚本：用模板匹配识别游戏画面，用模拟输入操作游戏，循环刷同一个副本。

本中文说明为准，[英文版](README.en.md)是它的翻译。

## 进度

图例：

- ✅ 在所有者的真机上运行过
- 🧪 只有自动化测试
- ⏳ 尚未完成
- ❓ 有待测量

| 部分 | 状态 | 依据 |
|---|---|---|
| 与平台无关的核心：模板匹配、页面判定树、配置、日志、输入动作词汇、恢复规则 | 🧪 | 180 个自动化测试，在 CI 的 Linux 上运行 |
| Linux 探测工具：找到游戏所在的 gamescope 显示、截图、发送输入、判断失焦暂停 | ✅ | [tools/linux_probe.py](tools/README.md)。2026-10-05 在所有者的机器上对游戏完整跑过一次（L1 到 L4）。在那之前有单元测试，以及在 headless 模式的 gamescope 里对一个替身窗口的干跑 |
| 游戏窗口在后台时，能否从 gamescope 里截到画面 | ✅ | 能，用 `gamescopectl screenshot`（2026-10-05，一次实测）：游戏有焦点、失焦、被遮住时都截到了真实画面，每帧 1.1 到 1.3 秒。截图是 gamescope 输出画面的尺寸，和游戏自己的分辨率不一定一样，所以模板和点击坐标要按比例换算。用 X11 截游戏窗口，每次都是全黑（游戏用 Vulkan 渲染）。PipeWire 一帧也没拿到：连接一直停在格式协商阶段，原因还在查。只在菜单之间操作的话，每秒一帧左右就够；要对战斗做出反应，得每秒 10 帧上下，这几种办法里只有 PipeWire 有可能做到 |
| 能否只把输入送进 gamescope 里的游戏，而不碰桌面 | ✅ | 能（2026-10-05，一次实测）：用 XTest 发给 gamescope 嵌套 X 服务器的 Escape 送到了游戏窗口，画面随之变化（变化量 89.6，按键前静置时最多 1.7），宿主桌面上的终端没有收到 Escape |
| 窗口失去焦点时游戏会不会暂停 | ✅ | 不会（2026-10-05，一次实测）：失焦时画面的变化量是有焦点时的 132%（城镇里的环境动画），游戏窗口没有收到焦点事件，gamescope 根窗口上的属性也没有变。看来游戏跑在 gamescope 自己的 X 服务器里，察觉不到宿主桌面的焦点变化。Windows 上会暂停，要在游戏进程内伪装焦点才能阻止（2026-08-26 两次实测）；照这次的结果，Linux 上用不着这个伪装。还要靠更长时间的挂机来确认 |
| Linux 平台层：找到游戏（`gamescope.py`），经 XTest 发输入（`xtest_input.py`） | 🧪 | 106 个自动化测试。输入模块的每道关（只认 gamescope 的嵌套 X、焦点和点下去的位置都要在游戏窗口上、只发配置里的键、不发修饰键也不发要按 Shift 才打得出的键、不明确要求就只空跑）和循环截图的每道关（截图目录只有本用户能进、切掉黑边、收到停止就不再等这一帧）故意弄坏时，对应的测试都会变红。还没有对着游戏跑过，也还没在 headless 的 gamescope 里跑过。下一步是战斗循环 |
| 应用本体：战斗循环、界面、热键 | ⏳ | Windows 版的应用层保存在本仓库的历史里。Linux 平台层正在写（见上一行），之后是战斗循环和它的停止、暂停命令 |

## 计划

游戏在 Proton 下运行，外面套着一层嵌套的 gamescope。这可能让 Windows 版的三个阻碍直接消失，而不必把当时的变通办法移植过来：失焦暂停（[gbfr_auto#45](https://github.com/LowGuiGui/gbfr_auto/issues/45)）、虚拟手柄的输入漏进别的程序（[gbfr_auto#53](https://github.com/LowGuiGui/gbfr_auto/issues/53)）、ViGEmBus 停止维护（[gbfr_auto#49](https://github.com/LowGuiGui/gbfr_auto/issues/49)）。这只是推断，所以在写任何平台代码之前，先由[探测工具](tools/README.md)测量：

1. 从游戏进程的环境变量里找到它所在的 gamescope 显示。
2. 在 gamescope 窗口失去焦点或被遮住时截图。
3. 检查发给 gamescope 的 X 服务器的输入，能否只驱动游戏而不碰桌面。
4. 检查 gamescope 窗口失去焦点时游戏会不会暂停。
5. 仅当第 4 步发现会暂停时：检查旧的焦点伪装做成 `.asi`、由 Reloaded-II 的 ASI 加载器加载后，在 Proton 下是否仍然有效。

第一轮测量（2026-10-05，每一步各一次）支持这个推断：失焦不暂停，XTest 输入只进游戏，gamescopectl 在后台也截得到图，所以第 5 步眼下用不上。结论还要靠更长时间的挂机来确认。

热键打算改用 GNOME 自定义快捷键：快捷键调用一个小命令行工具，由它通过 Unix 套接字通知脚本。原因是在 GNOME 的 Wayland 会话里，全局键盘监听只能收到 XWayland 窗口里的按键。

## 开发

需要 Python 3.12 或更新版本；CI 和本地环境用的是 3.13。

    uv venv --python 3.13
    uv pip install -r requirements.txt -r requirements-dev.txt
    .venv/bin/pytest
    .venv/bin/ruff check .
    .venv/bin/reuse lint

- 每个改动单独开分支，通过 pull request 合入 `dev`。
- 合入 `dev` 之前，CI 的检查必须通过：`ruff`、`pytest`，以及检查每个文件是否写明版权人和许可证的 `reuse`。
- [tests/README.md](tests/README.md) 说明测试覆盖了什么、覆盖不到什么。

## 项目结构

| 文件 | 作用 |
|---|---|
| `opencv.py` | 模板匹配，以及对空白帧的防护 |
| `pages.py` | 按有序规则树判断当前是哪个画面的引擎 |
| `pagetree.py` | 游戏实际的判定树：用哪些模板、有哪些画面、每个画面做什么 |
| `framediff.py` | 两帧之间变化了多少；探测工具靠它区分游戏是暂停了还是在运行 |
| `geometry.py` | 窗口、客户区与中心点的换算 |
| `backend.py` | 输入动作词汇（前进、开打、再来一次、确认、全部松开），键鼠和手柄各一套实现 |
| `xusb.py` | Xbox 手柄的报告格式和按钮位 |
| `supervisor.py` | 何时切换输入方式、暂停或恢复的纯函数判定 |
| `config.py` | TOML 配置 |
| `applog.py` | 日志 |
| `gamescope.py` | 找到游戏所在的 gamescope（进程、嵌套 X 显示、游戏窗口），并用 `gamescopectl` 截图；给循环用时还会切掉 gamescope 的黑边，缩放回游戏自己的分辨率。探测工具和平台层都用它 |
| `xtest_input.py` | 经 gamescope 嵌套 X 的 XTest 给游戏发键盘和鼠标输入：只发给游戏窗口，只发配置里的键，不明确要求就只空跑 |
| `template/` | 拿来和画面比对的参考图 |
| `tools/linux_probe.py` | Linux 探测工具，见 [tools/README.md](tools/README.md) |
| `docs/provenance/` | 本仓库的历史是怎样从归档仓库生成的 |

## 历史

本仓库的历史从 Windows 版开始。前 84 个提交是从归档仓库机械筛选出来的；[docs/provenance](docs/provenance/README.md) 记录了做法、新旧提交的对照，以及怎样取回暂存在历史里的应用层。

较早的提交说明里，issue 编号写作 `gbfr_auto#NN`；代码注释里的编号不带前缀（如 `#45`）。两者指的都是[归档仓库的 issue](https://github.com/LowGuiGui/gbfr_auto/issues)，不是本仓库的。

## 关于 AI 编写

- **工具**：Anthropic 的 Claude Code（Claude Opus 5 / 5.5 模型），在仓库所有者的指示下工作。
- **范围**：
  - 除上游作者的原始代码以外的全部内容：从归档仓库带过来的 fork 改动（2026-08-23 至 2026-10-02），以及此后在本仓库所做的一切。
  - 除上游的 6 个提交和 2026-08-23 的 3 个初始配置提交（ruff 与 pre-commit 配置、依赖版本、恢复 LICENSE）外，每个非合并提交都带有 `Co-Authored-By: Claude` 标注。
- **审查**：**没有人工逐行审查。** 所有者负责方向，以及需要真实游戏的测试。
- **验证**：
  - Linux 上有 397 个自动化测试（核心 180 个，Linux 平台层 106 个，探测工具 111 个），每个 pull request 都会在 CI 里运行。
  - 在 Linux 上对游戏运行过的只有探测工具：2026-10-05，每一步各一次，结果见[进度](#进度)。脚本本身还没有。测试覆盖不到截图、输入，以及任何需要游戏本身的行为。
- **风险**：脚本会向游戏发送输入；探测计划的第 5 步还可能把一个库加载进游戏进程。GPL 不提供任何担保（GPL-2.0 第 11、12 条）。
- **版权**：AI 生成的内容能否受版权保护，目前在法律上尚无定论；在受保护的范围内，适用 [COPYRIGHT](COPYRIGHT) 中的许可。AI 的输出也可能与其训练数据相似。
- **请勿**把这些代码提交给禁止 AI 生成内容的项目（例如 Gentoo、NetBSD、QEMU）。
- 本说明同样由这个 AI 起草。

## 注意事项

- 这是非官方项目，与 Cygames 无关，也未经其认可。碧蓝幻想：Relink 及其美术素材归 Cygames, Inc. 所有。

## 许可证

GPL-2.0-or-later：GPL 第 2 版，或者（由你选择）任何更新的版本。`opencv.py`、`pagetree.py` 和 `.gitignore` 原先含有上游按 GPL-2.0-only 授权的代码，2026-10-05 已经重写，上游的代码现在只留在 git 历史里。许可证全文在 [LICENSES/](LICENSES/)。`template/` 里的图片是游戏的美术素材，不在 GPL 之下。谁拥有什么、为什么这样划分，见 [COPYRIGHT](COPYRIGHT)。
