# hs-deck-cli — Hearthstone Deck CLI for AI Agents

**面向 AI Agent 的炉石传说组卡命令行工具 —— 校验、编码、解码、筛卡、卡组库存档与版本体检。**

A command-line deck building tool for Hearthstone designed for AI agents — validation, encoding, decoding, card filtering, deck archiving and patch-cycle health checks.

[English](README_EN.md) | [简体中文](README.md)

---

人类的组卡模拟器解决的是"可视化拖卡"，而 Agent 组卡需要的是秒级试错：列 30 张卡 → 校验 → 出卡组代码 → 按报错修正 → 再来一轮。这个工具就是为此而生。

## 功能特性

- **卡组代码编解码** — 完整支持炉石 deckstring 格式（含副牌库三元组），并对营地等平台在标准代码后附加的扩展字节做了容错
- **构筑规则校验** — 数量、同名限量（普通 2 张 / 传说 1 张）、职业限定、标准池白名单
- **动态卡组容量** — 裂魂者阿扎莉娜（套牌 20 张）、时空大盗拉法姆（40 张且恰含 10 张拉法姆）、常规 30 张
- **副牌库** — 乐队经理精英牛头人酋长的 3 张乐队，编码为 sideboard 三元组
- **多职业与游客机制** — 按 `classes` 数组识别多职业卡（如六职业共用的灭世者死亡之翼），并完整实现胜地历险记游客三条规则：游客仅解锁目的地职业的该扩展卡牌、每套限一名游客、不可嵌套
- **卡组库与版本体检** — 卡组本地存档，版本更新后一键体检，退环境卡逐条列出
- **卡组长图** — 一条命令把卡组渲染成可分享的卡组长图（中/英版各自独立）：法力曲线、稀有度配色（默认逐张列出，`--merge` 合并同名卡）、职业徽记、英雄与卡组代码，2x 渲染输出 1520px 宽，调用本地无头 Chrome/Edge
- **对 Agent 友好** — 纯 JSON 输入、报错逐条列出便于自我修正、无任何交互式提示
- **本地双语卡牌库** — 中英双语卡牌数据源自 [HearthstoneJSON](https://hearthstonejson.com/)，补丁日一条命令刷新

## 持续维护

本项目处于活跃维护状态：炉石每个新版本（扩展包 / 平衡补丁）上线后，会同步更新本地标准卡牌库与标准池白名单，卡组体检随版本跟进。若数据源变更导致问题，欢迎提 issue。

## 安装

要求：Python 3.10+（Windows / macOS / Linux）

`hs image` 另需本机安装 Chrome 或 Edge（自动探测，可用环境变量 `CHROME_PATH` 指定）。

```bash
git clone https://github.com/OstrichHermit/hs-deck-cli.git
cd hs-deck-cli
pip install .
```

开发模式用 `pip install -e .`（改动源码即时生效）。PyPI 发布：Coming soon。

数据目录默认 `~/.hs-deck-cli/`（卡牌库、卡组库、卡组图都存这里），可用环境变量 `HS_DECK_HOME` 覆盖。装好后先跑一次 `hs update` 下载卡牌库。

### 安装为 Agent Skill（可选）

仓库内附带 Agent Skill（`skills/hs-deck/SKILL.md`），把它复制到你所用 AI Agent 的 skills 目录，Agent 即可自动掌握本工具的用法。以 Claude Code 为例：

```bash
cp -r skills/hs-deck ~/.claude/skills/hs-deck
```

## 用法

```bash
# 刷新卡牌库（自动下载最新中文+英文全卡数据）
hs update

# 筛卡
hs filter --class=战士 --set=CORE --cost=<=3 --text=嘲讽

# 解码卡组代码
hs decode AAECAQcGo6AE...

# 校验并输出卡组代码
hs validate deck.json

# 卡组入库（支持代码或网页 URL）
hs save my-deck AAECAQcGo6AE...
hs save from-web https://example.com/deck-page

# 查看卡组库
hs list
hs show my-deck

# 版本更新后体检（省略名字 = 检查全部）
hs check

# 生成卡组长图
hs image my-deck                          # 按卡组库名字
hs image AAECAQcGo6AE... --name=Turtle    # 直接给代码
hs image my-deck --lang=en                # 英文版（--lang=both 一次出中英两版）
hs image my-deck --merge                  # 同名卡合并为一行
hs image my-deck --name=龟甲防战 --name-en=Turtle Warrior
```

标准卡组含非标准池卡时默认拦截不出图，`--force` 可强制渲染。

`deck.json` 格式：

```json
{
  "format": "standard",
  "hero": "加尔鲁什·地狱咆哮",
  "cards": { "斩杀": 2, "#69535": 1 },
  "sideboard": { "owner": "乐队经理精英牛头人酋长", "cards": { "蓝鳃战士": 2 } }
}
```

卡名或 `#dbfId` 均可，副牌库可选。

报错逐条输出，Agent 可以按条机械修正：

```
校验失败:
  - 套牌必须30张, 当前27张
  - 奇利亚斯豪华版3000型 的系列 WHIZBANGS_WORKSHOP 不在当前标准池
```

## 标准池维护

标准池白名单在源码 `src/hs_deck_cli/deck.py` 里的 `STANDARD_SETS`。新版本上线后：跑 `hs update`，把新系列代码加进去，再 `hs check` 体检卡组库。

## 数据源

卡牌数据来自社区项目 [HearthstoneJSON](https://hearthstonejson.com/)（本地化文本遵循 CC BY 4.0）。构建提取自游戏文件，官方补丁上线当天或次日即可获取；预览季爆料的新卡要等补丁正式部署后才会入库。

## 免责声明

炉石传说是暴雪娱乐的商标。本项目与暴雪官方无关，仅供个人学习研究使用。

## 许可

[MIT](LICENSE)
