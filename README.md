# hs-deck-cli — Hearthstone Deck CLI for AI Agents

**面向 AI Agent 的炉石传说组卡命令行工具 —— 校验、编码、解码、筛卡、卡组库存档与版本体检。**
A command-line deck building tool for Hearthstone designed for AI agents — validation, encoding, decoding, card filtering, deck archiving and patch-cycle health checks.

Human deck builders are GUI simulators: drag cards, watch the mana curve. An agent doesn't need that — it needs fast, scriptable trial-and-error: pick 30 cards, validate, get a deck code, fix what's flagged, repeat. This tool does exactly that.

人类的组卡模拟器解决的是"可视化拖卡"，而 Agent 组卡需要的是秒级试错：列 30 张卡 → 校验 → 出卡组代码 → 按报错修正 → 再来一轮。这个工具就是为此而生。

## Features | 功能

- **Deck code encode/decode 卡组代码编解码** — full deckstring format incl. sideboard triplets; tolerant of extra trailing sections appended by platforms like InnKeeper/营地. 完整支持炉石 deckstring 格式（含副牌库三元组），并对营地等平台在标准代码后附加的扩展字节做了容错
- **Rule validation 构筑规则校验** — card count, per-card copy limits (2 / 1 for legendaries), class restrictions, Standard pool whitelist. 数量、同名限量（普通 2 张 / 传说 1 张）、职业限定、标准池白名单
- **Dynamic deck size 动态卡组容量** — Azalina the Soulbreaker (20-card deck), Rafam time-travel variant (40 cards with exactly 10 Rafams), normal 30. 裂魂者阿扎莉娜（套牌 20 张）、时空大盗拉法姆（40 张且恰含 10 张拉法姆）、常规 30 张
- **Sideboard / band 副牌库** — Band Manager's 3-card band, encoded as sideboard triplets. 乐队经理精英牛头人酋长的 3 张乐队，编码为 sideboard 三元组
- **Multiclass & Tourist 多职业与游客机制** — respects `classes` arrays (e.g. Death Wing, Deathlord of the World shared by six classes) and the Perils in Paradise tourist rules: a Tourist unlocks only the destination class's cards *from that expansion*, one Tourist per deck, no nesting. 按 `classes` 数组识别多职业卡（如六职业共用的灭世者死亡之翼），并完整实现胜地历险记游客三条规则：游客仅解锁目的地职业的该扩展卡牌、每套限一名游客、不可嵌套
- **Deck library & health checks 卡组库与版本体检** — save decks locally, then `hs check` after every patch to see exactly which cards rotated out. 卡组本地存档，版本更新后一键体检，退环境卡逐条列出
- **Deck image 卡组长图** — render any deck (library name or raw code) to a share-ready PNG, Chinese or English: mana curve, rarity-colored card rows (one row per card by default, `--merge` to combine duplicates), class sigil, hero and deck code. Uses local headless Chrome/Edge. 一条命令把卡组渲染成可分享的卡组长图（中/英版各自独立）：法力曲线、稀有度配色（默认逐张列出，`--merge` 合并同名卡）、职业徽记、英雄与卡组代码，调用本地无头 Chrome/Edge 渲染
- **Agent-friendly 对 Agent 友好** — plain-JSON input, itemized error output for precise self-correction, zero interactive prompts. 纯 JSON 输入、报错逐条列出便于自我修正、无任何交互式提示
- **Local card database 本地双语卡牌库** — zhCN + enUS data from [HearthstoneJSON](https://hearthstonejson.com/), refreshed with one command on patch day. 中英双语卡牌数据源自 HearthstoneJSON，补丁日一条命令刷新

## Install | 安装

Requirements 要求: Python 3.10+ (Windows / macOS / Linux)

`hs image` additionally needs Chrome or Edge installed locally (auto-detected; set `CHROME_PATH` to override).
`hs image` 另需本机安装 Chrome 或 Edge（自动探测，可用环境变量 `CHROME_PATH` 指定）。

```bash
git clone https://github.com/OstrichHermit/hs-deck-cli.git
cd hs-deck-cli
python hs_deck.py --help
```

Optional: drop `hs` / `hs.cmd` wrappers into a PATH directory to make it a global command (see repo `bin` examples in nas/asr style).

## Usage | 用法

```bash
# Refresh the card databases (auto-downloads latest zhCN + enUS collectible json)
# 刷新卡牌库（自动下载最新中文+英文全卡数据）
hs update

# Filter cards 筛卡
hs filter --class=战士 --set=CORE --cost=<=3 --text=嘲讽

# Decode a deck code 解码卡组代码
hs decode AAECAQcGo6AE...

# Validate + emit a deck code. 校验并输出卡组代码
hs validate deck.json

# Save a deck (from code or URL) into the local library. 卡组入库
hs save my-deck AAECAQcGo6AE...
hs save from-web https://example.com/deck-page

# Inspect the library 查看卡组库
hs list
hs show my-deck

# Health-check after a new patch (omit name = check all). 版本更新后体检
hs check

# Render a deck image PNG. 生成卡组长图
hs image my-deck                          # by library name 按卡组库名字
hs image AAECAQcGo6AE... --name=Turtle    # from a raw code 直接给代码
hs image my-deck --lang=en                # English version 英文版 (--lang=both 一次出中英两版)
hs image my-deck --merge                  # one row per distinct card 同名卡合并为一行
hs image my-deck --name=龟甲防战 --name-en=Turtle Warrior
```

Standard-format decks containing non-Standard cards are blocked by default; `--force` renders anyway.
标准卡组含非标准池卡时默认拦截不出图，`--force` 可强制渲染。

`deck.json` format | 输入格式:

```json
{
  "format": "standard",
  "hero": "加尔鲁什·地狱咆哮",
  "cards": { "斩杀": 2, "#69535": 1 },
  "sideboard": { "owner": "乐队经理精英牛头人酋长", "cards": { "蓝鳃战士": 2 } }
}
```

Cards accept names or `#dbfId`; sideboard is optional. 卡名或 `#dbfId` 均可，副牌库可选。

Validation errors are itemized line by line so an agent can fix the deck mechanically:
报错逐条输出，Agent 可以按条机械修正：

```
校验失败:
  - 套牌必须30张, 当前27张
  - 奇利亚斯豪华版3000型 的系列 WHIZBANGS_WORKSHOP 不在当前标准池
```

## Standard pool maintenance | 标准池维护

The whitelist lives at the top of `hs_deck.py` (`STANDARD_SETS`). When a new expansion drops: run `hs update`, add the new set code, then `hs check` your library.
标准池白名单在脚本头部 `STANDARD_SETS`。新版本上线后：跑 `hs update`，把新系列代码加进去，再 `hs check` 体检卡组库。

## Data source | 数据源

Card data comes from the community project [HearthstoneJSON](https://hearthstonejson.com/) (CC BY 4.0 for localized data). Builds are extracted from game files and typically appear within a day of each official patch; cards revealed during preview season appear only once the patch ships.
卡牌数据来自社区项目 HearthstoneJSON（本地化文本遵循 CC BY 4.0）。构建提取自游戏文件，官方补丁上线当天或次日即可获取；预览季爆料的新卡要等补丁正式部署后才会入库。

## Disclaimer | 免责声明

Hearthstone is a trademark of Blizzard Entertainment. This project is not affiliated with or endorsed by Blizzard. For personal and educational use.
炉石传说是暴雪娱乐的商标。本项目与暴雪官方无关，仅供个人学习研究使用。

## License | 许可

[MIT](LICENSE)
