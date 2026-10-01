# hs-deck-cli — Hearthstone Deck CLI for AI Agents

A command-line deck building tool for Hearthstone designed for AI agents — validation, encoding, decoding, card filtering, deck archiving and patch-cycle health checks, plus in-game board parsing from client logs and a turn watcher that feeds an AI advisor.

**面向 AI Agent 的炉石传说组卡命令行工具 —— 校验、编码、解码、筛卡、卡组库存档与版本体检，另可解析客户端日志输出对局面板、监听对局触发 AI 军师分析。**

[English](README_EN.md) | [简体中文](README.md)

---

Human deck builders are GUI simulators: drag cards, watch the mana curve. An agent doesn't need that — it needs fast, scriptable trial-and-error: pick 30 cards, validate, get a deck code, fix what's flagged, repeat. This tool does exactly that.

## Features

- **Deck code encode/decode** — full deckstring format incl. sideboard triplets; tolerant of extra trailing sections appended by platforms like InnKeeper
- **Rule validation** — card count, per-card copy limits (2 / 1 for legendaries), class restrictions, Standard pool whitelist
- **Dynamic deck size** — Azalina the Soulbreaker (20-card deck), Rafam time-travel variant (40 cards with exactly 10 Rafams), normal 30
- **Sideboard / band** — Band Manager's 3-card band, encoded as sideboard triplets
- **Multiclass & Tourist** — respects `classes` arrays (e.g. Death Wing, Deathlord of the World shared by six classes) and the Perils in Paradise tourist rules: a Tourist unlocks only the destination class's cards *from that expansion*, one Tourist per deck, no nesting
- **Deck library & health checks** — save decks locally, then `hs check` after every patch to see exactly which cards rotated out
- **Deck image** — render any deck (library name or raw code) to a share-ready PNG, Chinese or English: mana curve, rarity-colored card rows (one row per card by default, `--merge` to combine duplicates), class sigil, hero and deck code; rendered at 2x (1520px wide). Uses local headless Chrome/Edge
- **Game board** — parse the Hearthstone client log Power.log into a structured board panel: turn, mana, both heroes' HP/armor, board minions with status tags (taunt, divine shield, ...), your hand with cost/attack/health, opponent's hand size and deck/fatigue
- **Advisor watcher** — `hs watch` tails Power.log in the background; on the mulligan phase and your turns it pushes a fixed prompt to a self-hosted IM bridge, triggering AI advisor analysis
- **Agent-friendly** — plain-JSON input, itemized error output for precise self-correction, zero interactive prompts
- **Local card database** — zhCN + enUS data from [HearthstoneJSON](https://hearthstonejson.com/), refreshed with one command on patch day

## Actively maintained

This project is actively maintained: with every Hearthstone patch (expansion or balance update), the local card database and the Standard-pool whitelist are updated in sync, and deck health checks follow each patch cycle. If an upstream data change breaks something, please open an issue.

## Install

Requirements: Python 3.10+ (Windows / macOS / Linux)

`hs image` additionally needs Chrome or Edge installed locally (auto-detected; set `CHROME_PATH` to override).

```bash
git clone https://github.com/OstrichHermit/hs-deck-cli.git
cd hs-deck-cli
pip install .
```

For development use `pip install -e .` (edits take effect immediately). PyPI release: coming soon.

Data lives by default in `~/.hs-deck-cli/` (card database, deck library, rendered images); override with the `HS_DECK_HOME` environment variable. Run `hs update` once after installing to fetch the card database.

### Install as an Agent Skill (optional)

This repo ships with an Agent Skill (`skills/hs-deck/SKILL.md`). Copy it into your AI agent's skills directory so the agent picks up the tool automatically. Claude Code example:

```bash
cp -r skills/hs-deck ~/.claude/skills/hs-deck
```

## Usage

```bash
# Refresh the card databases (auto-downloads latest zhCN + enUS collectible json)
hs update

# Filter cards
hs filter --class=战士 --set=CORE --cost=<=3 --text=嘲讽

# Decode a deck code
hs decode AAECAQcGo6AE...

# Validate + emit a deck code
hs validate deck.json

# Save a deck (from code or URL) into the local library
hs save my-deck AAECAQcGo6AE...
hs save from-web https://example.com/deck-page

# Inspect the library
hs list
hs show my-deck

# Health-check after a new patch (omit name = check all)
hs check

# Render a deck image PNG
hs image my-deck                          # by library name
hs image AAECAQcGo6AE... --name=Turtle    # from a raw code
hs image my-deck --lang=en                # English version (--lang=both renders zh + en)
hs image my-deck --merge                  # one row per distinct card
hs image my-deck --name=龟甲防战 --name-en=Turtle Warrior

# Parse the current game board (reads %LOCALAPPDATA%\Blizzard\Hearthstone\Logs\Power.log by default)
hs board
hs board --log=D:\games\Hearthstone\Logs\Power.log   # explicit log path
hs board --player=鸵鸟居士                            # pin your player name if auto-detect is unsure

# Advisor watcher: POST a prompt to the IM bridge on mulligan / your turns to trigger AI analysis
hs watch start --channel=<Discord channel ID> --token=<bridge token>
hs watch status                                      # running state + recent trigger events
hs watch stop
```

Standard-format decks containing non-Standard cards are blocked by default; `--force` renders anyway.

`deck.json` format:

```json
{
  "format": "standard",
  "hero": "加尔鲁什·地狱咆哮",
  "cards": { "斩杀": 2, "#69535": 1 },
  "sideboard": { "owner": "乐队经理精英牛头人酋长", "cards": { "蓝鳃战士": 2 } }
}
```

Cards accept names or `#dbfId`; sideboard is optional.

Validation errors are itemized line by line so an agent can fix the deck mechanically:

```
校验失败:
  - 套牌必须30张, 当前27张
  - 奇利亚斯豪华版3000型 的系列 WHIZBANGS_WORKSHOP 不在当前标准池
```

`hs board` renders the live game panel like this (Chinese, as the panel is aimed at zh AI advisors):

```
=== 炉石对局面板 ===
回合 8 | 我的回合 | 我的法力 5/8（已用 3）
对方：暴风城诗人（法师）手牌 4 牌库 18 疲劳 0
英雄：吉安娜·普罗德摩尔 血 15/30 护甲 0 武器 无 技能 火焰冲击(未用)
对方场面(2)：
  1. 卑劣的脏鼠 3/6 [嘲讽]
  2. 苦痛侍僧 1/3
我方：鸵鸟居士（战士）牌库 22 疲劳 0
英雄：加尔鲁什·地狱咆哮 血 22/30 护甲 5 武器 无 技能 全副武装(未用)
我方场面(3)：
  1. 铸甲师 1/4
  2. 库卡隆精英卫士 4/3 [冲锋]
  3. 暴乱狂战士 3/3
我方手牌(4)：
  1. 斩杀 2费 法术
  2. 盾牌格挡 3费 法术
  3. 铸甲师 1费 随从 1/4
  4. 绝命乱斗 5费 法术
# 实体总数 142 | 解析起始行 3210 | 日志总行 9845
```

## Game board & advisor watcher (board / watch)

`hs board` replays every packet from the last `CREATE_GAME` in the log and prints the final state panel, ready to be fed to an AI. Your side is auto-detected as the player whose hand is visible (only the local client sees its own hand); pin it with `--player=<name>` if unsure. `--stdin` reads the log from a pipe for testing.

`hs watch start` runs a background daemon that tails Power.log; on the mulligan phase and on your turns it POSTs a fixed prompt to a self-hosted IM bridge (`POST /api/external/message`, Bearer-token auth), which then triggers AI advisor analysis in a Discord channel. Notes:

- **The bridge is a private component, not part of this repo** (default `http://127.0.0.1:8088`). On its own, `hs watch` only listens — it never sends anything without a reachable bridge. A failed POST is retried 3 times, then watching continues; recent triggers are visible via `hs watch status --events=N`
- Config is merged into `~/.hs-deck-cli/watch_config.json`; a later `start` with no flags reuses the last config, and `--force` restarts over a stale process
- Prompts are customizable via `--mulligan-prompt=` / `--turn-prompt=`; the token can also come from the `HS_WATCH_TOKEN` environment variable

## Standard pool maintenance

The whitelist lives in `src/hs_deck_cli/deck.py` (`STANDARD_SETS`). When a new expansion drops: run `hs update`, add the new set code, then `hs check` your library.

## Data source

Card data comes from the community project [HearthstoneJSON](https://hearthstonejson.com/) (CC BY 4.0 for localized data). Builds are extracted from game files and typically appear within a day of each official patch; cards revealed during preview season appear only once the patch ships.

## Disclaimer

Hearthstone is a trademark of Blizzard Entertainment. This project is not affiliated with or endorsed by Blizzard. For personal and educational use.

## License

[MIT](LICENSE)
