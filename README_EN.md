# hearthstone-cli — Hearthstone CLI Toolbox for AI Agents

A command-line toolbox for Hearthstone designed for AI agents, with two cores: deck building (validation, encoding, decoding, filtering, archiving, deck images) and match analysis (real-time board state & action replay parsed from client logs, plus an AI-counselor watcher).

**面向 AI Agent 的炉石传说命令行工具箱，双核心：组卡（校验 / 编解码 / 筛卡 / 卡组库存档 / 版本体检 / 长图）+ 对局分析（解析客户端日志输出实时对局面板与战况回放，监听对局触发 AI 军师）。**

[English](README_EN.md) | [简体中文](README.md)

---

Human deck builders are GUI simulators: drag cards, watch the mana curve. An agent doesn't need that — it needs fast, scriptable trial-and-error: pick 30 cards, validate, get a deck code, fix what's flagged, repeat. And mid-game, an agent doesn't need a screenshot — it needs the full match state as parseable text: both sides' resources, boards, hands, and a turn-by-turn action replay. This toolbox answers both needs.

## Features

- **Deck code encode/decode** — full deckstring format incl. sideboard triplets; tolerant of extra trailing sections appended by platforms like InnKeeper
- **Rule validation** — card count, per-card copy limits (2 / 1 for legendaries), class restrictions, Standard pool whitelist
- **Dynamic deck size** — Azalina the Soulbreaker (20-card deck), Rafam time-travel variant (40 cards with exactly 10 Rafams), normal 30
- **Sideboard / band** — Band Manager's 3-card band, encoded as sideboard triplets
- **Multiclass & Tourist** — respects `classes` arrays (e.g. Death Wing, Deathlord of the World shared by six classes) and the Perils in Paradise tourist rules: a Tourist unlocks only the destination class's cards *from that expansion*, one Tourist per deck, no nesting
- **Deck library & health checks** — save decks locally, then `hs check` after every patch to see exactly which cards rotated out
- **Deck image** — render any deck (library name or raw code) to a share-ready PNG, Chinese or English: mana curve, rarity-colored card rows (one row per card by default, `--merge` to combine duplicates), class sigil, hero and deck code; rendered at 2x (1520px wide). Uses local headless Chrome/Edge
- **Game board** — full replay of the Hearthstone client log Power.log into a structured live panel: game mode, turn/action tracking, both sides' mana (incl. Overload locks) and turn order (coin marked), both heroes' HP/armor/weapon/power (incl. imbue/transform changes), board minions and locations with status tags (taunt, divine shield, windfury, frozen, dormant, ...), your hand with cost/atk/hp (Forge preview, powered-up / unplayable tags), both players' deck count / fatigue / corpses, quest progress (shown separately from secrets), and an endgame line (win/loss via lethal, concede, or fatigue)
- **Action replay** — a per-turn event stream covering every step of both players: plays (yours bare-named, opponent plays with effect text; battlecry targets for both), attacks (target + actual damage), hero powers, draw/discard, mulligan keeps/swaps, start-of-game triggers (legendary copies listed by name), deathrattle/trigger settlements (sourced summons, fatal damage tags, reborns, sourced heals), discover/cataclysm choices, prepare discounts, dormancy, deathrattle reveals (cast vs. revealed only), end-of-turn gains (with source), burned cards (named) — duplicates merged to avoid spam; the AI needs no card-db lookup
- **Advisor watcher** — `hs watch` tails Power.log in the background; on the mulligan phase and your turns it pushes a fixed prompt to a self-hosted IM bridge, triggering AI advisor analysis
- **Collection sync** (Windows only) — `hs collection` reads your saved deck lists straight from the running game's memory, encodes them into standard deck codes, and syncs them into the local deck library (`export` for a one-shot pull, `watch` for a background daemon that syncs on change)
- **Agent-friendly** — plain-JSON input, itemized error output for precise self-correction, zero interactive prompts
- **Local card database** — zhCN + enUS data from [HearthstoneJSON](https://hearthstonejson.com/), refreshed with one command on patch day

## Actively maintained

This project is actively maintained: with every Hearthstone patch (expansion or balance update), the local card database and the Standard-pool whitelist are updated in sync, and deck health checks follow each patch cycle. If an upstream data change breaks something, please open an issue.

## Install

Requirements: Python 3.10+ (Windows / macOS / Linux)

`hs image` additionally needs Chrome or Edge installed locally (auto-detected; set `CHROME_PATH` to override).

```bash
git clone https://github.com/OstrichHermit/hearthstone-cli.git
cd hearthstone-cli
pip install .
```

For development use `pip install -e .` (edits take effect immediately). Also available from PyPI: `pip install hearthstone-cli`.

Data lives by default in `~/.hearthstone-cli/` (card database, deck library, rendered images); override with the `HS_DECK_HOME` environment variable. Run `hs update` once after installing to fetch the card database.

### Install as an Agent Skill (optional)

This repo ships with an Agent Skill (`skills/hs-deck/SKILL.md`). Copy it into your AI agent's skills directory so the agent picks up the tool automatically. Claude Code example:

```bash
cp -r skills/hs-deck ~/.claude/skills/hs-deck
```

## Usage

```bash
# Refresh the card databases (auto-downloads latest zhCN + enUS collectible json plus the zhCN full json, which covers hero powers/tokens for the board panel)
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

# Parse the current game board (auto-discovers the latest log: Hearthstone_* subdirs under the game's Logs dir, plus the standard dir)
hs board
hs board --log=D:\games\Hearthstone\Logs\Power.log   # explicit log path (a dir also works: auto-discover)
hs board --player=鸵鸟居士                            # pin your player name if auto-detect is unsure

# Advisor watcher: POST a prompt to the IM bridge on mulligan / your turns to trigger AI analysis
# Edit the watch section of ~/.hearthstone-cli/config.json first (channel_id/url/token, ...), then start
hs watch start                                       # after filling the watch section in config.json
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

`hs board` renders the live game panel like this (real-game snapshot; Chinese, as the panel is aimed at zh AI advisors; opponent name anonymized):

```
=== 炉石对局面板 ===
休闲·标准 | 构建号 253216
【对局已结束】
对局结束：我方胜利（斩杀，对方英雄阵亡）
总第 19 手 | 我方第 10 回合 | 我的回合 | 我的法力 10/10（已用 0）
对方：遛弯的树懒（牧师）[后手+硬币] 手牌 9 牌库 16 尸体 6 法力 1/9（已用 8）
英雄：「情报掮客拉祖尔」血 0/30 护甲 0 武器 无 技能「月亮的祝福」(已用)
对方场面(3)：
  1. 「心灵扫荡者」2/3
  2. 「受难的恐翼巨龙」5/5
  3. 「凯洛斯的蛋」0/3
我方：鸵鸟居士（战士）[先手] 牌库 20 尸体 4
英雄：「麦格尼·铜须」血 30/30 护甲 5 武器 无 技能「全副武装！」(未用)
任务「走进失落之城」9/10
我方场面(5)：
  1. 「奥卓克希昂」6/7
  2. 「拉格纳罗斯的士兵」4/2
  3. 「破链灾星霍格」10/10 [嘲讽]
  4. 「奥卓克希昂」6/1
  5. 「拉格纳罗斯的士兵」2/1
我方手牌(7)：
  1. 「屠灭」6费 法术
  2. 「龟甲旋风」4费 法术
  3. 「拉格纳罗斯，绝世烈火」8费 8/8 随从 <兆示：「拉格纳罗斯之手」>
  4. 「放出鳄鱼」2费 法术
  5. 「强固」3费 法术
  6. 「怒袭甲龙」3费 4/3 随从
  7. 「城防守卫」4费 法术
=== 行动回顾 ===
[第 18 回合·对方]
18-1 手牌已满，烧掉「暗言术：毁」
18-2 打出 随从「受难的恐翼巨龙」5/5<亡语：抽两张龙牌，其法力值消耗减少（1）点。>
18-3 英雄技能「月亮的祝福」<选择一张可用的牧师随从牌或法术牌置入你的手牌，其法力值消耗减少（>
18-4 选择：「心灵扫荡者」
18-5 获得「心灵扫荡者」
18-6 打出 随从「心灵扫荡者」2/3<战吼：如果你在本牌在你手牌中时使用过对手卡牌的复制，对所有敌方随从造成2点伤害。>
[第 19 回合·我方]
19-1 抽牌「城防守卫」<召唤两个0/6并具有嘲讽的守卫。守卫在受到伤害时会获得+1攻击力。>
19-2 攻击：「破链灾星霍格」10/10 → 对方英雄
19-3 攻击：「奥卓克希昂」6/1 → 对方英雄
19-4 死亡：「情报掮客拉祖尔」
```

## Game board & advisor watcher (board / watch)

> **Quality assurance**: board's parsing coverage has been through multiple rounds of full-game audits against real matches, with per-item regression checks (number reconciliation, event tracing, and edge cases like instant-concede games, truncated logs, and hero-card transforms). The Hearthstone log format still shifts with each patch — if a new patch breaks parsing, please open an [issue](https://github.com/OstrichHermit/hearthstone-cli/issues) or send a PR.

`hs board` replays every packet from the last `CREATE_GAME` in the log and prints the complete panel at the current moment, ready to be fed to an AI. Your side is auto-detected as the player whose hand is visible (only the local client sees its own hand); pin it with `--player=<name>` if unsure. `--stdin` reads the log from a pipe for testing.

**Panel layer**: game mode & build number, total turns / current turn / acting side, both sides' mana (`avail/total (used N)`, Overload locks tagged) and turn order (coin marked for going second), both heroes' HP/armor/weapon/power (replaced or imbued powers shown as-is), board minions and locations (ATK/HP + taunt / divine shield / windfury / frozen / dormant / stealth / poisonous tags), your hand with cost/atk/hp (Forge preview `<Forge: card>`, powered-up / unplayable tags), both players' deck count / fatigue / corpses, quest progress slots (`quest "name" x/y`, counted separately from secrets, reward announced on completion), and an endgame line (win/loss via lethal, concede, or fatigue; truncated logs are flagged instead of misreported). During the mulligan phase the panel prints the opening deal, ready for keep-or-mulligan advice.

**Action recap**: automatically covers the last three turns along turn boundaries — all of my previous turn, all of the opponent's last turn, and what has already happened on my current turn (`--turns=N` to change how many turns, `--turns=0` to hide it). Events are stably ordered as they appear in the log:

- Card text is never truncated and follows the once-on-entry rule: your cards show it on draw/gain/opening deal/mulligan-in, your plays do not repeat it, opponent plays always carry it (their hand is invisible to you), and unattributed summons attach it too. With at-the-moment ATK/HP snapshots and battlecry targets; attacks carry the target and actual damage dealt (post-aura); hero powers carry effect text and built-in armor; deaths carry reborn info
- The opening section carries mulligan semantics (opening hand → kept/swapped/drawn-in → coin) and START_OF_GAME trigger effects (e.g. "copy your legendaries" with every copied card named)
- Engine-side settlements are fully recorded: deathrattle/triggered summons with their source (duplicates merged as ×N), deathrattle/trigger damage (fatal tagged), heals with their source, reborns, dormancy & awakenings, prepare discounts, discover/cataclysm choices, shuffle-into-deck summaries, end-of-turn gains with their source, deathrattle reveals (cast vs. revealed only), and burned cards (named)
- Privacy by design: opponent draws are reported as counts only, never card names


`hs watch start` runs a background daemon that tails Power.log; on the mulligan phase and on your turns it POSTs a fixed prompt to a self-hosted IM bridge (`POST /api/external/message`, Bearer-token auth), which then triggers AI advisor analysis in a Discord channel. Notes:

- **The bridge is a private component, not part of this repo** (default `http://127.0.0.1:8088`). On its own, `hs watch` only listens — it never sends anything without a reachable bridge. A failed POST is retried 3 times, then watching continues; recent triggers are visible via `hs watch status --events=N`
- All configuration lives in the `watch` section of `~/.hearthstone-cli/config.json` (`channel_id`/`url`/`token`/`log`/`mulligan_prompt`/`turn_prompt`), one shared config file for both `hs watch` and `hs collection`, managed per section; the CLI offers no configuration flags — edit the file, then `start`. `--force` restarts over a stale process
- Prompts and the token are set in the config file; the token can also come from the `HS_WATCH_TOKEN` environment variable

## Collection sync (optional, Windows only)

`hs collection` uses a small built-in memory reader (DeckExport) to read your saved deck lists straight from the running Hearthstone client — the same approach as community tools like Hearthstone Deck Tracker: **read-only memory, nothing is ever written to the game**. Standard/Wild decks are encoded into standard deck codes and written into the deck library in the same format as `hs save`, so `hs show` / `hs image` / `hs check` work on them right away.

Prerequisites:

- Windows, with the Hearthstone client running
- dotnet SDK 9 (`winget install Microsoft.DotNet.SDK.9`) — only needed to build the reader
- A patched HearthMirror source cache (`~/.hearthstone-cli/native/src/`; upstream HearthMirror_Decompiled source does not compile as-is — fix the compile errors yourself or copy the cache from another machine with this tool set up)

Common commands:

```bash
# One-shot export (auto-builds the reader on first run if missing)
hs collection export

# Compare only, write nothing (for debugging)
hs collection export --check

# Background daemon: syncs automatically whenever decks change in game (polls every 5s by default)
# Edit the collection section of ~/.hearthstone-cli/config.json first (interval / sync_delete), then start
hs collection watch start
hs collection watch status                              # running state + recent sync events
hs collection watch stop

# Restart with --force after editing the config so a running daemon picks it up
hs collection watch start --force

# Build / repair the memory reader manually
hs collection build
```

Notes:

- Sync is additive by default: decks deleted in game are kept locally; set `sync_delete` to `true` in the config to mirror deletions — but only archives previously written by the sync itself are removed, anything imported manually via `hs save` is never touched
- Duplicate deck names (the game allows multiple slots with the same name) get a `-<last 4 digits of deckId>` suffix from the second one on; naming is stable and reproducible
- Non-Standard/Wild formats (Classic, Arena, ...) are skipped and listed
- With the game closed, `export` exits with a friendly hint; `watch` idles silently and resumes once the game starts
- A deck containing cards unknown to the local database is skipped as a whole — run `hs update` and sync again
- `~/.hearthstone-cli/config.json` is the single entry point for configuration — one file, one section per feature (`watch` / `collection`, never interfering); the CLI no longer takes configuration flags (only the operational `--force` / `--check` / `--events=N` and the path locator `--config=路径`, which points at an alternative location; watch.pid / watch.log / collection.pid / collection.log live next to the config file for easy test isolation). Copy `config.example.json` from the repo root to get started. Full structure:

```json
{
  "watch": {
    "channel_id": "<Discord channel ID>",
    "url": "http://127.0.0.1:8088",
    "token": "<IM bridge token>",
    "log": "auto",
    "mulligan_prompt": "mulligan-phase prompt",
    "turn_prompt": "my-turn prompt"
  },
  "collection": {
    "interval": 5,
    "sync_delete": false
  }
}
```

- The legacy `watch_config.json` / `collection_config.json` files are migrated into the matching section of the unified file on first use (legacy files are kept)

## Standard pool maintenance

The whitelist lives in `src/hearthstone_cli/deck.py` (`STANDARD_SETS`). When a new expansion drops: run `hs update`, add the new set code, then `hs check` your library.

## Data source

Card data comes from the community project [HearthstoneJSON](https://hearthstonejson.com/) (CC BY 4.0 for localized data). Builds are extracted from game files and typically appear within a day of each official patch; cards revealed during preview season appear only once the patch ships.

## Disclaimer

Hearthstone is a trademark of Blizzard Entertainment. This project is not affiliated with or endorsed by Blizzard. For personal and educational use.

## License

[MIT](LICENSE)
