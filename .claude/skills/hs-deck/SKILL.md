---
name: hs-deck
description: 炉石传说组卡命令行工具 hs 的使用方式——筛卡、编解码卡组代码（deckstring）、校验 deck.json、卡组库存档与版本体检、生成可分享的卡组长图。当用户要组卡、校验/解码卡组、查卡、生成卡组图，或提到炉石组卡器、hs 命令、hs-deck-cli、deckstring、卡组代码时使用此 skill。
---

# 炉石组卡器（hs）

面向 AI Agent 的炉石传说组卡 CLI（开源仓库 hs-deck-cli）。纯 JSON 输入、报错逐条列出、无交互提示，适合「列卡 → 校验 → 出卡组代码 → 按报错修正 → 重跑」的秒级试错循环。

## 环境与入口

- 全局命令 `hs`；实体 `D:\AgentWorkspace\hs-deck-cli\hs_deck.py`（`bin\` 下 `hs` / `hs.cmd` 双包装器指向它，bash 与 PowerShell 通用）
- 数据同目录：`cards_zh.json` / `cards_en.json`（双语卡牌库）、`decks\`（卡组库存档）、`image\`（卡组图默认输出）
- 命令不可用时回退：`python "D:\AgentWorkspace\hs-deck-cli\hs_deck.py" <子命令>`（Python 3.10+；克隆仓库场景直接 `python hs_deck.py`）
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
- 默认输出到 `image\` 目录；`--lang=both` 一次出中英两版
- 标准卡组含非标准卡时默认拦截不出图——正确做法是修卡组，`--force` 只在明确要看非标准卡组时用
- 依赖本机 Chrome/Edge 无头渲染（自动探测，可用环境变量 `CHROME_PATH` 指定）

## 标准池维护（补丁日例行）

新版本上线后：`hs update` 刷新卡库 → 把新系列 set 代码加进脚本头部 `STANDARD_SETS` → `hs check` 体检卡组库（退环境卡逐条列出）。CORE_HIDDEN 数据假象已剔除，旧核心卡不会误判为标准可用。

## 注意

- 报错逐条输出，按条机械修正后重跑 `validate` 即可
- `hs fetch` 对 SPA 页面抓不到卡组代码，让用户手动复制后走 `hs save` / `hs decode`
- 狂野同名卡多版本自动选版，无需手动指定
- git-bash 里 python/node 全局命令缺失时，用全路径 python 或 windows-mcp 的 PowerShell 工具执行
