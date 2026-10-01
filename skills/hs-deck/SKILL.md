---
name: hs-deck
description: 炉石传说组卡命令行工具 hs 的使用方式——筛卡、编解码卡组代码（deckstring）、校验 deck.json、卡组库存档与版本体检、生成可分享的卡组长图、解析 Power.log 输出对局面板、监听对局触发 AI 军师。当用户要组卡、校验/解码卡组、查卡、生成卡组图、看对局面板/对局分析，或提到炉石组卡器、hs 命令、hs-deck-cli、deckstring、卡组代码、hs board、hs watch 时使用此 skill。
---

# 炉石组卡器（hs）

面向 AI Agent 的炉石传说组卡 CLI（开源仓库 hs-deck-cli）。纯 JSON 输入、报错逐条列出、无交互提示，适合「列卡 → 校验 → 出卡组代码 → 按报错修正 → 重跑」的秒级试错循环。

## 环境与入口

- 全局命令 `hs`（标准 Python 包 hs-deck-cli，`pip install -e D:\AgentWorkspace\hs-deck-cli` 安装，入口 exe 在 Python314\Scripts\hs.exe）
- git-bash 通道：PATH 无 Python Scripts 目录时走 `D:\AgentWorkspace\bin\hs`（转发 hs.exe）
- 数据目录：`~/.hs-deck-cli\`（含 `cards_zh.json` / `cards_en.json` 双语卡牌库、`decks\` 卡组库存档、`image\` 卡组图默认输出），可用环境变量 `HS_DECK_HOME` 自定义
- 仓库本地路径 `D:\AgentWorkspace\hs-deck-cli\`（src 布局，源码在 `src/hs_deck_cli/deck.py`）
- 卡牌库缺失或补丁后先 `hs update` 刷新（从 HearthstoneJSON 下载中英全卡库，需联网）

## 常用命令

```bash
hs update                             # 刷新中英双语卡牌库
hs filter --class=战士 --set=CORE --cost='<=3' --text=嘲讽 --name=嘲讽
hs decode 'AAECAQcGo6AE...'           # 解码卡组代码为卡牌清单
hs validate deck.json                 # 校验卡组并输出卡组代码（stdin 传 -）
hs save <名字> <代码或URL> [来源备注]  # 卡组入库
hs list                               # 列出卡组库
hs show <名字>                        # 查看存档卡组明细
hs check [名字]                       # 体检存档卡组（省略名字 = 全部）
hs fetch <URL>                        # 抓网页里的卡组代码（只打印，不入库）
hs image <名字或代码> [选项]           # 生成卡组长图 PNG
hs board [--log=路径|--stdin] [--player=名字]  # 解析 Power.log 输出当前对局面板
hs watch start/stop/status             # 军师监听：换牌/我方回合时 POST 提示词到 IM 桥接器
```

`hs image` 选项：`--lang=zh|en|both`、`--name=标题`、`--name-en=英文标题`、`--merge`（同名卡合并一行）、`--force`（绕过标准池拦截）、`--out=路径.png`。

## deck.json 格式（validate 的输入）

```json
{
  "format": "standard",
  "hero": "加尔鲁什·地狱咆哮",
  "cards": {"斩杀": 2, "#69535": 1},
  "sideboard": {"owner": "乐队经理精英牛头人酋长", "cards": {"蓝鳃战士": 2}}
}
```

卡名或 `#dbfId` 均可；`hero` 给职业名或 dbfId；`sideboard` 可选（牛头人酋长的 3 张乐队）。

## 校验规则要点

- 标准 30 张；传说限 1、普通限 2；职业限定；标准池白名单（`format: wild` 免白名单）
- 动态容量：裂魂者阿扎莉娜套牌 20 张；时空大盗拉法姆 40 张且恰含 10 张拉法姆
- 多职业卡按 `classes` 数组识别；游客三条规则：仅解锁目的地职业的该扩展卡、每套限一名、不可嵌套
- 营地等平台在标准 deckstring 后附加的扩展字节，解码已容错

## 卡组图（hs image）

- 2x 渲染 1520px 宽，含法力曲线、稀有度配色、职业徽记、英雄与卡组代码；默认同名卡逐张列出
- 默认输出到数据目录的 `image\`（`~/.hs-deck-cli\image`）；`--lang=both` 一次出中英两版
- 标准卡组含非标准卡时默认拦截不出图——正确做法是修卡组，`--force` 只在明确要看非标准卡组时用
- 依赖本机 Chrome/Edge 无头渲染（自动探测，可用环境变量 `CHROME_PATH` 指定）

## 对局面板与军师监听（hs board / hs watch）

- `hs board` 解析炉石客户端日志 Power.log（默认自动发现最新日志：游戏目录 `Logs\Hearthstone_*\Power.log` 与 `%LOCALAPPDATA%` 标准目录都扫，可 `--log=` 指定文件或目录，或 `--stdin` 管道读），从最后一个 CREATE_GAME 全量重放，输出结构化面板：回合/法力、双方英雄血甲、场面随从带状态标签、我方手牌费用攻血、对手牌库疲劳；我方按"手牌可见方"自动判定，不准时 `--player=玩家名` 手动指定；输出末尾附最近行动回顾（双方上回合出牌/攻击/技能/抽弃牌事件流，`--events=N` 调整条数，`--events=0` 关闭）
- `hs watch start/stop/status`：后台守护进程 tail Power.log，检测到换牌阶段或轮到我方回合时，向自建 IM 桥接器 `POST /api/external/message` 注入固定提示词，触发 Discord 军师频道 AI 分析（工作原理一句话：检测回合 → POST 提示词 → 桥接器触发 AI）
- 桥接器是私有组件（默认 `http://127.0.0.1:8088`，Bearer token 鉴权），不在本仓库；`hs watch` 单独使用只监听不发送，POST 失败重试 3 次后继续监听
- 配置 merge 存 `~/.hs-deck-cli/watch_config.json`，不带参 `start` 沿用上次配置；`--force` 强制重启；`status --events=N` 看最近触发；提示词用 `--mulligan-prompt=` / `--turn-prompt=` 自定义

## 标准池维护（补丁日例行）

新版本上线后：`hs update` 刷新卡库 → 把新系列 set 代码加进 `src/hs_deck_cli/deck.py` 头部 `STANDARD_SETS` → `hs check` 体检卡组库（退环境卡逐条列出）。CORE_HIDDEN 数据假象已剔除，旧核心卡不会误判为标准可用。

## 注意

- 报错逐条输出，按条机械修正后重跑 `validate` 即可
- `hs fetch` 对 SPA 页面抓不到卡组代码，让用户手动复制后走 `hs save` / `hs decode`
- 狂野同名卡多版本自动选版，无需手动指定
- `hs board` / `hs watch` 仅在 Windows 且本机跑过炉石客户端时可用（依赖 Power.log）；军师分析需 IM 桥接器在本机运行
- git-bash 里 python/node 全局命令缺失时，用全路径 python 或 windows-mcp 的 PowerShell 工具执行
