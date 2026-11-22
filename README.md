<!--
SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
SPDX-License-Identifier: GPL-2.0-or-later
-->

简体中文 | [English](README.en.md)

# GBFR Auto（Linux 版）

[![Project Status: WIP – Initial development is in progress, but there has not yet been a stable, usable release suitable for the public.](https://www.repostatus.org/badges/latest/wip.svg)](https://www.repostatus.org/#wip)

> [!IMPORTANT]
> **Maa 版 0.3.0 开发中：在任务柜台打开任意任务，软件用游戏自带的“再次挑战”连刷，第 10 次确认时选“是”接着刷。上一版 0.2.0-rc.1 已通过第一次真实游戏验收（巨爪岩鳄连续 12 轮、本轮后停止、立即停止）。**
>
> 回滚用的上一版是 0.2.0-rc.1（两个素材预设，自己走菜单接任务）和 0.1.0-rc.2（每轮回城重新接任务）。使用现有 MXU 客户端、
> MaaFramework 原生 Linux 控制器和本项目的游戏资源。安装、使用和停止方式见 [Maa 使用说明](docs/maa.md)。

> [!WARNING]
> **AI 编写声明。** 本仓库由 AI 编程助手（Claude Code 和 OpenAI Codex）在仓库所有者的指示下编写，**没有经过人工逐行审查**。
> 验证范围见下表与[测试说明](tests/README.md)，运行前请自行审阅。

这是 Windows 版 [LowGuiGui/gbfr_auto](https://github.com/LowGuiGui/gbfr_auto) 的 Linux 后继项目；Windows 版已归档。
你在任务柜台打开要刷的任务，选成功次数或一直刷：软件接下这个任务，之后用游戏自带的“再次挑战”连刷并计数，每 10 次确认时选“是”接着刷，结束时回到城镇。战斗依靠游戏的完全辅助模式和当前 AI 队伍；实测过的任务记录在 [catalog.json](assets/catalog.json)。
它的定位是让游戏在前台自己一轮接一轮地打，方便挂机观看，不追求最快的刷素材效率。

本中文说明为准，[英文版](README.en.md)是它的翻译。

## 当前方向与进度

Maa 提供识别、流程调度、截图和输入，MXU 提供已有界面。本项目维护游戏画面识别、刷取流程及少量运行计数和停止逻辑，不另造一套自动化引擎。

| 部分 | 当前范围 |
|---|---|
| 客户端与运行时 | 固定 MXU 2.11.2、MaaFramework 5.16.1；独立目录构建，保留旧版本用于回滚 |
| 真实游戏验证 | 0.3.0 开发版（2026-11-22，经 MXU，游戏不显示到桌面）：从岩鳄的任务详情页开始连续 12 轮无人操作，第 10 轮弹窗选“是”后不回城接着刷，最后停在城镇；本轮后停止、立即停止和真实失败这一版还没在真实游戏里跑过。它依据的游戏行为（第 10 次选“是”继续、失败页自己回城）同一天用测量脚本实测过。0.2.0-rc.1（2026-11-22）：岩鳄连续 12 轮无人操作（跨过第 10 轮弹窗）、本轮后停止、立即停止都通过；霜鳄的新循环还没验证。0.1.0-rc.2：简体中文 2.0.6、16:9、希德霍姆城镇起点；岩鳄完成 1/1 与连续五轮，霜鳄完成 1/1 |
| 任务范围 | 任意私人任务，从柜台打开的任务开始，识别不依赖任务名；只实测过巨爪岩鳄，记录在 [catalog.json](assets/catalog.json) |
| 停止 | 成功次数（1–999）或一直刷；当前轮结算后停止；立即停止操作；任务失败时不计数并停下 |
| 尚不支持 | 自动从标题画面继续、走到柜台选任务、从战斗或结算画面中途接管、自动配装和消耗素材、战斗中的输入 |

少量成功样本不能证明任意队伍或长期挂机都可靠。游戏正常重复奖励和经验仍会到账；未知画面、失败或超时会结束自动操作。

## 使用 Maa 客户端

在 Python 3.13 和 `uv` 可用的环境中构建：

```sh
python3 tools/build_maa.py --output dist/maa-0.3.0
```

通过 Steam 启动游戏，在任务柜台打开要刷的任务（停在详情页或准备画面），在游戏窗口里点一下。运行构建目录里的 `./start.sh`，
勾选“连刷当前任务”，填成功次数或选“一直刷”，然后开始；第一次刷某个任务请先用 1 次确认当前队伍能通关。
控制面板（欢迎信息里的链接）显示成功次数，提供“本轮后停止”和“立即停止”。

前提条件、出错时怎么办和更新方法见 [Maa 使用说明](docs/maa.md)。
停止软件不会暂停或关闭游戏；结束游戏请使用游戏自己的菜单。

## 旧命令行方案

`gbfr_auto.py`、`farm.py` 和 XTest 战斗循环保留作为历史实现与诊断参考。
Maa 的游戏验收不代表旧循环已完成同等验证。旧命令行默认只识别和记录，不发送输入：

```sh
.venv/bin/python gbfr_auto.py run
.venv/bin/python gbfr_auto.py status
.venv/bin/python gbfr_auto.py stop
```

旧循环与 Maa 使用同一会话锁，不能同时控制游戏。历史探测结果见 [tools](tools/README.md)。

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

Maa 路径在表格前四项；旧命令行循环和复用的检查工具也保留在仓库中。

| 文件 | 作用 |
|---|---|
| `assets/catalog.json`、`assets/interface.json` | 素材目标、前提条件与标准客户端选项 |
| `assets/resource/` | Maa 的共享识别与任务流程 |
| `maa_agent.py`、`maa_guard.py`、`maa_session.py`、`maa_target.py`、`maa_messages.py` | 目标和输入检查、单会话管理、通关计数与停止语义、停止原因的中文说明 |
| `tools/build_maa.py`、`assets/control.html` | 独立客户端构建，以及现有客户端服务上的进度/停止面板 |
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
- **审查**：**没有人工逐行审查。** 所有者负责项目方向。
- **验证**：
  - Linux 自动化测试，以及 Ruff 和 REUSE 检查。当前覆盖范围和真实游戏验证的限制见 [tests/README.md](tests/README.md)。
  - Maa 候选版本的真实游戏验收与自动化测试分别记录，见[当前方向与进度](#当前方向与进度)及 [Maa 使用说明](docs/maa.md)。旧命令行循环不能沿用 Maa 的验收结论。
- **风险**：脚本会向游戏发送输入。GPL 不提供任何担保（GPL-2.0 第 11、12 条）。
- **版权**：AI 生成的内容能否受版权保护，目前在法律上尚无定论；在受保护的范围内，适用 [COPYRIGHT](COPYRIGHT) 中的许可。AI 的输出也可能与其训练数据相似。
- **请勿**把这些代码提交给禁止 AI 生成内容的项目（例如 Gentoo、NetBSD、QEMU）。
- 本说明也由 AI 辅助维护。

## 注意事项

- 这是非官方项目，与 Cygames 无关，也未经其认可。碧蓝幻想：Relink 及其美术素材归 Cygames, Inc. 所有。

## 许可证

GPL-2.0-or-later：GPL 第 2 版，或者（由你选择）任何更新的版本。`opencv.py`、`pagetree.py` 和 `.gitignore` 原先含有上游按 GPL-2.0-only 授权的代码，2026-10-05 已经重写，上游的代码现在只留在 git 历史里。许可证全文在 [LICENSES/](LICENSES/)。`template/` 里的图片是游戏的美术素材，不在 GPL 之下。谁拥有什么、为什么这样划分，见 [COPYRIGHT](COPYRIGHT)。
