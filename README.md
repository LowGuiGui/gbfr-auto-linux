<!--
SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
SPDX-License-Identifier: GPL-2.0-or-later
-->

简体中文 | [English](README.en.md)

# GBFR Auto（Linux 版）

[![Project Status: WIP – Initial development is in progress, but there has not yet been a stable, usable release suitable for the public.](https://www.repostatus.org/badges/latest/wip.svg)](https://www.repostatus.org/#wip)

> [!IMPORTANT]
> **筹备中，目前还不能对游戏运行。**
>
> - 这是 Windows 版 [LowGuiGui/gbfr_auto](https://github.com/LowGuiGui/gbfr_auto) 的 Linux 后继项目。Windows 版已暂停并归档。
> - 仓库里目前只有从 Windows 版继承来的、与平台无关的核心。Linux 平台层（截图、输入、热键）还没有写：要先用探测工具量清楚游戏在 gamescope 下的表现。

> [!WARNING]
> **AI 编写声明。** 本仓库由 AI 编程助手（Anthropic 的 Claude Code）在仓库所有者的指示下编写，**没有经过人工逐行审查**。
>
> - 这里的代码还没有在 Linux 上对游戏运行过；核心部分只有自动化测试。
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
| 与平台无关的核心：模板匹配、页面判定树、配置、日志、输入动作词汇、恢复规则 | 🧪 | 173 个自动化测试，在 CI 的 Linux 上运行 |
| Linux 探测工具：找到游戏所在的 gamescope 显示、截图、发送输入、判断失焦暂停 | 🧪 | 已写好（[tools/linux_probe.py](tools/README.md)）。除了单元测试，还在 headless 模式的 gamescope 里对一个替身窗口干跑过；还没有对游戏运行过 |
| 游戏窗口在后台时，能否从 gamescope 里截到画面 | ❓ | Linux 上尚未测量 |
| 能否只把输入送进 gamescope 里的游戏，而不碰桌面 | ❓ | Linux 上尚未测量 |
| 窗口失去焦点时游戏会不会暂停 | ❓ | Windows 上会暂停；在游戏进程内伪装焦点可以阻止它（2026-08-26 两次实测）。在 gamescope 下，游戏跑在 gamescope 自己的 X 服务器里，可能根本察觉不到宿主桌面的焦点变化。Linux 上尚未测量 |
| 应用本体：战斗循环、界面、热键 | ⏳ | Windows 版的应用层保存在本仓库的历史里，等探测结果确定做法后再移植 |

## 计划

游戏在 Proton 下运行，外面套着一层嵌套的 gamescope。这可能让 Windows 版的三个阻碍直接消失，而不必把当时的变通办法移植过来：失焦暂停（[gbfr_auto#45](https://github.com/LowGuiGui/gbfr_auto/issues/45)）、虚拟手柄的输入漏进别的程序（[gbfr_auto#53](https://github.com/LowGuiGui/gbfr_auto/issues/53)）、ViGEmBus 停止维护（[gbfr_auto#49](https://github.com/LowGuiGui/gbfr_auto/issues/49)）。这只是推断，所以在写任何平台代码之前，先由[探测工具](tools/README.md)测量：

1. 从游戏进程的环境变量里找到它所在的 gamescope 显示。
2. 在 gamescope 窗口失去焦点或被遮住时截图。
3. 检查发给 gamescope 的 X 服务器的输入，能否只驱动游戏而不碰桌面。
4. 检查 gamescope 窗口失去焦点时游戏会不会暂停。
5. 仅当第 4 步发现会暂停时：检查旧的焦点伪装做成 `.asi`、由 Reloaded-II 的 ASI 加载器加载后，在 Proton 下是否仍然有效。

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
  - Linux 上有 236 个自动化测试（核心 173 个，探测工具 63 个），每个 pull request 都会在 CI 里运行。
  - 还没有任何代码在 Linux 上对游戏运行过。测试覆盖不到截图、输入，以及任何需要游戏本身的行为。
- **风险**：脚本会向游戏发送输入；探测计划的第 5 步还可能把一个库加载进游戏进程。GPL 不提供任何担保（GPL-2.0 第 11、12 条）。
- **版权**：AI 生成的内容能否受版权保护，目前在法律上尚无定论；在受保护的范围内，适用 [COPYRIGHT](COPYRIGHT) 中的许可。AI 的输出也可能与其训练数据相似。
- **请勿**把这些代码提交给禁止 AI 生成内容的项目（例如 Gentoo、NetBSD、QEMU）。
- 本说明同样由这个 AI 起草。

## 注意事项

- 本工具仅用于学习交流，请勿用于商业用途。
- 这是非官方项目，与 Cygames 无关，也未经其认可。碧蓝幻想：Relink 及其美术素材归 Cygames, Inc. 所有。

## 许可证

GPL-2.0-or-later，全文见 [LICENSES/GPL-2.0-or-later.txt](LICENSES/GPL-2.0-or-later.txt)。上游作品归上游作者所有；`template/` 里的图片是游戏的美术素材，不在 GPL 之下；谁拥有什么，见 [COPYRIGHT](COPYRIGHT)。
