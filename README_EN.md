# hs-deck-cli — Hearthstone Deck CLI for AI Agents

A command-line deck building tool for Hearthstone designed for AI agents — validation, encoding, decoding, card filtering, deck archiving and patch-cycle health checks.

**面向 AI Agent 的炉石传说组卡命令行工具 —— 校验、编码、解码、筛卡、卡组库存档与版本体检。**

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
- **Agent-friendly** — plain-JSON input, itemized error output for precise self-correction, zero interactive prompts
- **Local card database** — zhCN + enUS data from [HearthstoneJSON](https://hearthstonejson.com/), refreshed with one command on patch day

## Install

Requirements: Python 3.10+ (Windows / macOS / Linux)

`hs image` additionally needs Chrome or Edge installed locally (auto-detected; set `CHROME_PATH` to override).

```bash
git clone https://github.com/OstrichHermit/hs-deck-cli.git
cd hs-deck-cli
python hs_deck.py --help
```

Optional: drop `hs` / `hs.cmd` wrappers into a PATH directory to make it a global command.

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

## Standard pool maintenance

The whitelist lives at the top of `hs_deck.py` (`STANDARD_SETS`). When a new expansion drops: run `hs update`, add the new set code, then `hs check` your library.

## Data source

Card data comes from the community project [HearthstoneJSON](https://hearthstonejson.com/) (CC BY 4.0 for localized data). Builds are extracted from game files and typically appear within a day of each official patch; cards revealed during preview season appear only once the patch ships.

## Disclaimer

Hearthstone is a trademark of Blizzard Entertainment. This project is not affiliated with or endorsed by Blizzard. For personal and educational use.

## License

[MIT](LICENSE)
