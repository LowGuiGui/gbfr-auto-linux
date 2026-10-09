<!--
SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
SPDX-License-Identifier: GPL-2.0-or-later
-->

简体中文 | [English](README.en.md)

# GBFR Auto（Linux 版）

[![Project Status: WIP – Initial development is in progress, but there has not yet been a stable, usable release suitable for the public.](https://www.repostatus.org/badges/latest/wip.svg)](https://www.repostatus.org/#wip)

> [!IMPORTANT]
> **实验中，完整刷关循环尚未通过真实游戏验证。**
>
> - 这是 Windows 版 [LowGuiGui/gbfr_auto](https://github.com/LowGuiGui/gbfr_auto) 的 Linux 后继项目。Windows 版已暂停并归档。
> - 仓库已有 Linux 命令行、gamescope/XTest 战斗循环与诊断工具。

> [!WARNING]
> **AI 编写声明。** 本仓库由 AI 编程助手（Claude Code 和 OpenAI Codex）在仓库所有者的指示下编写，**没有经过人工逐行审查**。
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
| 与平台无关的核心：模板匹配、页面判定树、配置、日志、输入动作词汇、恢复规则 | 🧪 | 自动化单元测试；当前覆盖范围见 [tests](tests/README.md) |
| Linux 探测工具：找到游戏所在的 gamescope 显示、截图、发送输入、判断失焦暂停 | ✅ | [tools/linux_probe.py](tools/README.md)。2026-10-05 在所有者的机器上对游戏完整跑过一次（L1 到 L4）。在那之前有单元测试，以及在 headless 模式的 gamescope 里对一个替身窗口的干跑 |
| 游戏窗口在后台时，能否从 gamescope 里截到画面 | ✅ | 能，用 `gamescopectl screenshot`（2026-10-05，一次实测）：游戏有焦点、失焦、被遮住时都截到了真实画面，每帧 1.1 到 1.3 秒。截图是 gamescope 输出画面的尺寸，和游戏自己的分辨率不一定一样，所以模板和点击坐标要按比例换算。用 X11 截游戏窗口，每次都是全黑（游戏用 Vulkan 渲染）。PipeWire 一帧也没拿到：连接一直停在格式协商阶段，原因还在查。只在菜单之间操作的话，每秒一帧左右就够；要对战斗做出反应，得每秒 10 帧上下，这几种办法里只有 PipeWire 有可能做到 |
| 能否只把输入送进 gamescope 里的游戏，而不碰桌面 | ✅ | 能（2026-10-05，一次实测）：用 XTest 发给 gamescope 嵌套 X 服务器的 Escape 送到了游戏窗口，画面随之变化（变化量 89.6，按键前静置时最多 1.7），宿主桌面上的终端没有收到 Escape |
| 窗口失去焦点时游戏会不会暂停 | ✅ | 不会（2026-10-05，一次实测）：失焦时画面的变化量是有焦点时的 132%（城镇里的环境动画），游戏窗口没有收到焦点事件，gamescope 根窗口上的属性也没有变。看来游戏跑在 gamescope 自己的 X 服务器里，察觉不到宿主桌面的焦点变化。Windows 上会暂停，要在游戏进程内伪装焦点才能阻止（2026-08-26 两次实测）；照这次的结果，Linux 上用不着这个伪装。还要靠更长时间的挂机来确认 |
| Linux 平台层：找到游戏（`gamescope.py`），经 XTest 发输入（`xtest_input.py`） | 🧪 | 自动化测试覆盖输入模块的每道关（只认 gamescope 的嵌套 X；焦点和点下去的位置都要在游戏窗口上，指针移过去以后再看一次；只发配置里的键，不发要按 Shift 才打得出的键和 Num Lock 会改掉的小键盘键，修饰键或者这个键本身正被别处按着、或者键盘换到了别的布局时不发；每次按下之前重新读键盘和指针映射；按住的键每一轮再查一次；不明确要求就只空跑）和循环截图的每道关（截图目录只有本用户能进、每次一个新文件名、切掉黑边、收到停止就不再等这一帧，gamescopectl 卡住了也一样）故意弄坏时，对应的测试都会变红。还没有对着游戏跑过，也还没在 headless 的 gamescope 里跑过。把它们接起来的命令行见下一行 |
| 应用本体：战斗循环（`farm.py`）、停下和暂停（`control.py`）、命令行（`gbfr_auto.py`） | 🧪 | 自动化测试覆盖停机条件（游戏窗口没了、连着三次截不到画面、同一页待得太久都会停下，连着三次按不下去就暂停），开打以后每一轮都会在输入层复查按着的键，松开了就重新开打，每一条出路都先把按着的全部松开；停下和暂停只认本用户，同一时间只跑一个循环。这几条故意弄坏时，对应的测试都会变红。还没有对着游戏跑过：下一步是空跑（见[使用](#使用)）。没有图形界面；Windows 版的界面和热键保存在本仓库的历史里 |

## 计划

游戏在 Proton 下运行，外面套着一层嵌套的 gamescope。这可能让 Windows 版的三个阻碍直接消失，而不必把当时的变通办法移植过来：失焦暂停（[gbfr_auto#45](https://github.com/LowGuiGui/gbfr_auto/issues/45)）、虚拟手柄的输入漏进别的程序（[gbfr_auto#53](https://github.com/LowGuiGui/gbfr_auto/issues/53)）、ViGEmBus 停止维护（[gbfr_auto#49](https://github.com/LowGuiGui/gbfr_auto/issues/49)）。[探测工具](tools/README.md)负责测量这些假设：

1. 从游戏进程的环境变量里找到它所在的 gamescope 显示。
2. 在 gamescope 窗口失去焦点或被遮住时截图。
3. 检查发给 gamescope 的 X 服务器的输入，能否只驱动游戏而不碰桌面。
4. 检查 gamescope 窗口失去焦点时游戏会不会暂停。

第一轮测量（2026-10-05，每一步各一次）支持这个推断：失焦不暂停，XTest 输入只进游戏，gamescopectl 在后台也截得到图，因此 Linux 构建不包含 Windows 焦点伪装与虚拟手柄路径。结论还要靠更长时间的挂机来确认。

停下和暂停用 GNOME 自定义快捷键：快捷键调用 `gbfr_auto.py stop` 或 `pause`，它们经一个 Unix 套接字通知正在跑的循环（见[使用](#使用)）。原因是在 GNOME 的 Wayland 会话里，全局键盘监听只能收到 XWayland 窗口里的按键。

## 使用

完整战斗循环尚未通过真实游戏验证（见[进度](#进度)），所以先空跑。游戏得在 gamescope 里开着，从 Steam 启动；脚本从不启动或关闭游戏。按[开发](#开发)里的步骤装好环境，然后在仓库目录里：

    .venv/bin/python gbfr_auto.py run                          # 空跑：认页面、记日志，一个键都不发
    .venv/bin/python gbfr_auto.py run --live                   # 真的给游戏发输入
    .venv/bin/python gbfr_auto.py run --live --repeats 10      # 打完十场就停
    .venv/bin/python gbfr_auto.py status                       # 正在跑的循环在做什么
    .venv/bin/python gbfr_auto.py pause                        # 暂停；resume 接着跑，stop 停下
    .venv/bin/python gbfr_auto.py release                      # 松开 W 和鼠标中键

- **找游戏。** 它在 gamescope 里找游戏，一个都没有、或者不止一个，都不开始。
- **停下。** 在它的终端里按 Ctrl+C，或者在哪里都可以运行 `gbfr_auto.py stop`。GNOME 上可以把停下和暂停绑到按键上：设置、键盘、查看及自定义快捷键、自定义快捷键，加一个，命令写 `<仓库路径>/.venv/bin/python <仓库路径>/gbfr_auto.py stop`，再加一个写 `pause`。同一时间只会跑一个循环。
- **自己停下。** 宁可停下也不瞎猜：游戏窗口没了、连着三次截不到画面、同一页待得超过上限（`loop.max_battle_s`、`loop.max_page_s`），都会停下。连着三次按键没能送到（比如焦点在一个对话框上），就暂停，用 `resume` 接着跑。完成 `--repeats` 或者收到停止，退出码是 0；因为别的原因停下，是 1。 第一次截图失败或空白就先松开输入，连续三次失败仍会停下。只有取得新的可用画面并确认旧按键已松开才会再次按下；松开未确认时暂停重试。暂停和停止都会取消正在等待的截图。
- **脚本崩溃以后。** `release` 尝试松开配置里的每个键和鼠标中键。它先取得同一把会话锁，再查找游戏；有其他会话占用时拒绝运行。跳过或发送失败的松开请求会返回退出码 1；成功只表示请求已送到 X，仍应检查游戏中的按键状态。关闭游戏应通过游戏菜单完成。
- **文件。** `gbfr_auto.toml`（第一次运行时写出来：按键、阈值、上限；出了界的值，循环不肯开始）、`logs/`，以及 `anomalies/`（认不出的画面，只有 `detect.save_anomaly_frames = true` 时才存）。三样都不进版本库。
- **旧配置。** 请从已有 `gbfr_auto.toml` 中删除退役的 `[input]`、`[pad]` 和 `[inject]` 段落。程序会在查找游戏前拒绝这些段落，不改写原文件；`[keys]`、`[loop]`、`[detect]`、`[log]` 可保留。`run` 默认空跑，只有 `run --live` 才发送输入；旧 `input.dry_run` 不再是输入保护。新文件写为 UTF-8，已有 BOM 或本地编码文件仍兼容读取。
- **认不出页面时。** 模板是在一个没人记下来的分辨率下截的。空跑时打开 `detect.log_scores = true` 和 `detect.save_anomaly_frames = true`，日志里的得分能看出每张模板差多少，`detect.template_scale` 可以缩放模板。

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
| `backend.py` | 输入动作词汇（前进、开打、再来一次、确认、全部松开），Linux 键鼠实现 |
| `supervisor.py` | 窗口消失、替换或输入不可用时停下的纯函数判定 |
| `config.py` | TOML 配置 |
| `applog.py` | 日志 |
| `gamescope.py` | 找到游戏所在的 gamescope（进程、嵌套 X 显示、游戏窗口），并用 `gamescopectl` 截图；给循环用时还会切掉 gamescope 的黑边，缩放回游戏自己的分辨率。探测工具和平台层都用它 |
| `xtest_input.py` | 经 gamescope 嵌套 X 的 XTest 给游戏发键盘和鼠标输入：只发给游戏窗口，只发配置里的键，不明确要求就只空跑 |
| `farm.py` | 战斗循环：截图、认页面、动作，宁可停下也不瞎猜；不碰平台 |
| `control.py` | 停下、暂停、接着跑：循环看的开关，以及拨动它们的那条本地私有套接字 |
| `gbfr_auto.py` | 命令行：`run`（不加 `--live` 只空跑）、`stop`、`pause`、`resume`、`status`、`release` |
| `template/` | 拿来和画面比对的参考图 |
| `tools/linux_probe.py` | Linux 探测工具，见 [tools/README.md](tools/README.md) |
| `docs/provenance/` | 本仓库的历史是怎样从归档仓库生成的 |

## 历史

本仓库的历史从 Windows 版开始。前 84 个提交是从归档仓库机械筛选出来的；[docs/provenance](docs/provenance/README.md) 记录了做法、新旧提交的对照，以及怎样取回暂存在历史里的应用层。

较早的提交说明里，issue 编号写作 `gbfr_auto#NN`；代码注释里的编号不带前缀（如 `#45`）。两者指的都是[归档仓库的 issue](https://github.com/LowGuiGui/gbfr_auto/issues)，不是本仓库的。

## 关于 AI 编写

- **工具**：Claude Code 和 OpenAI Codex，在仓库所有者的指示下工作。
- **范围**：
  - 除上游作者的原始代码以外的全部内容：从归档仓库带过来的 fork 改动（2026-08-23 至 2026-10-02），以及此后在本仓库所做的一切。
  - AI 辅助提交通过 `Co-Authored-By` 标注实际参与的助手；旧提交标注 Claude，本次 Linux 清理标注 Codex。上游的 6 个提交和 2026-08-23 的 3 个初始配置提交没有这类标注。
- **审查**：**没有人工逐行审查。** 所有者负责方向，以及需要真实游戏的测试。
- **验证**：
  - Linux 自动化测试，以及 Ruff 和 REUSE 检查。当前覆盖范围和真实游戏验证的限制见 [tests/README.md](tests/README.md)。
  - 在 Linux 上对游戏运行过的只有探测工具：2026-10-05，每一步各一次，结果见[进度](#进度)。脚本本身还没有。测试覆盖不到截图、输入，以及任何需要游戏本身的行为。
- **风险**：脚本会向游戏发送输入。GPL 不提供任何担保（GPL-2.0 第 11、12 条）。
- **版权**：AI 生成的内容能否受版权保护，目前在法律上尚无定论；在受保护的范围内，适用 [COPYRIGHT](COPYRIGHT) 中的许可。AI 的输出也可能与其训练数据相似。
- **请勿**把这些代码提交给禁止 AI 生成内容的项目（例如 Gentoo、NetBSD、QEMU）。
- 本说明也由 AI 辅助维护。

## 注意事项

- 这是非官方项目，与 Cygames 无关，也未经其认可。碧蓝幻想：Relink 及其美术素材归 Cygames, Inc. 所有。

## 许可证

GPL-2.0-or-later：GPL 第 2 版，或者（由你选择）任何更新的版本。`opencv.py`、`pagetree.py` 和 `.gitignore` 原先含有上游按 GPL-2.0-only 授权的代码，2026-10-05 已经重写，上游的代码现在只留在 git 历史里。许可证全文在 [LICENSES/](LICENSES/)。`template/` 里的图片是游戏的美术素材，不在 GPL 之下。谁拥有什么、为什么这样划分，见 [COPYRIGHT](COPYRIGHT)。
