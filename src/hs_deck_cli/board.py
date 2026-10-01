"""hs board — 解析炉石客户端日志 Power.log, 输出当前对局面板 (供 AI 军师读取)

用法:
  hs board                          读默认日志 (%LOCALAPPDATA%\\Blizzard\\Hearthstone\\Logs\\Power.log)
  hs board --log=D:\\path\\Power.log  指定日志路径
  hs board --stdin                  从 stdin 读日志 (方便测试)
  hs board --player=鸵鸟居士         手动指定我方玩家名 (默认自动判定)

只认 GameState.DebugPrintPower() 行 (PowerTaskList 是重复历史, 忽略)。
从最后一个 CREATE_GAME 起全量重放 packet, tag 原子覆盖, 输出最终状态面板。
"""
import os
import re
import sys
from pathlib import Path

DEFAULT_LOG = (Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
               / "Blizzard" / "Hearthstone" / "Logs" / "Power.log")

# 行前缀: 时间戳 D 21:47:52.3528941 GameState.DebugPrintPower() - <payload>
PREFIX_RE = re.compile(r"^D [\d:.]+ GameState\.DebugPrintPower\(\) - (.*)$")
TAG_LINE_RE = re.compile(r"^tag=(\S+) value=(.+?)\s*$")
CREATE_GAME_RE = re.compile(r"^CREATE_GAME$")
FULL_ENTITY_RE = re.compile(r"^FULL_ENTITY - Updating (.+) CardID=(\S*)\s*$")
SHOW_ENTITY_RE = re.compile(r"^SHOW_ENTITY - Updating (?:Entity=)?(.+) CardID=(\S*)\s*$")
HIDE_ENTITY_RE = re.compile(r"^HIDE_ENTITY - Entity=(.+?)(?: tag=(\S+) value=(.+?)\s*)?$")
TAG_CHANGE_RE = re.compile(r"^TAG_CHANGE Entity=(.+) tag=(\S+) value=(.+?)\s*$")
CHANGE_ENTITY_RE = re.compile(r"^CHANGE_ENTITY - Updating (?:Entity=)?(.+) CardID=(\S*)\s*$")
BRACKET_ID_RE = re.compile(r"\bID=(\d+)")
ENTITY_NAME_RE = re.compile(r"entityName=(.*?) ID=")

# 日志里 tag value 可能是数字枚举也可能是字符串, 两种都归一化
ZONE_BY_NUM = {1: "PLAY", 2: "DECK", 3: "HAND", 4: "GRAVEYARD", 5: "REMOVEDFROMGAME", 6: "SETASIDE", 7: "SECRET"}
CTYPE_BY_NUM = {1: "GAME", 2: "PLAYER", 3: "HERO", 4: "MINION", 5: "SPELL", 6: "ENCHANTMENT",
                7: "WEAPON", 9: "TOKEN", 10: "HERO_POWER", 39: "LOCATION"}
MULLIGAN_BY_NUM = {1: "INPUT"}
PLAYSTATE_BY_NUM = {4: "WON", 5: "LOST", 6: "TIED"}

# cards_zh.json 是 collectible 库, 英雄技能查不到, 内置经典技能兜底 (其余显示 cardId 原文)
HERO_POWER_NAMES = {
    "CS2_034": "火焰冲击", "CS2_056": "生命分流", "CS2_084": "全副武装",
    "CS2_049": "图腾召唤", "CS2_101": "援军", "DS1h_292": "稳固射击",
    "CS2_017": "变形", "CS1h_001": "次级治疗术", "CS2_083b": "匕首精通",
}

# 随从状态标签 (按此顺序输出, 冻结附注最后)
MINION_TAGS = [
    ("TAUNT", "嘲讽"), ("DIVINE_SHIELD", "圣盾"), ("WINDFURY", "风怒"), ("MEGA_WINDFURY", "巨型风怒"),
    ("STEALTH", "潜行"), ("LIFESTEAL", "吸血"), ("POISONOUS", "剧毒"), ("REBORN", "复生"),
    ("DORMANT", "休眠"), ("CHARGE", "冲锋"), ("RUSH", "突袭"), ("CANT_ATTACK", "不可攻击"),
    ("CANT_BE_ATTACKED", "无法被攻击"), ("SILENCED", "被沉默"), ("IMMUNE", "免疫"),
]
TYPE_ZH = {"MINION": "随从", "SPELL": "法术", "WEAPON": "武器", "HERO": "英雄", "LOCATION": "地标", "HERO_POWER": "技能"}


def _num(v):
    """字符串 -> int, 非数字返回 None"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _norm(v, table):
    """归一化枚举值: 数字枚举查表转字符串, 原本就是字符串的原样返回"""
    n = _num(v)
    return table.get(n, v) if n is not None else v


def _new_game():
    return {"entities": {}, "names": {}, "cur": None}


def _create_entity(game, ref, card_id):
    """按 FULL_ENTITY 引用创建实体, 返回 entityID (无 ID 的引用返回 None)"""
    eid = _ref_id(ref)
    if eid is None:
        return None
    name = ""
    nm = ENTITY_NAME_RE.search(ref)
    if nm:
        name = nm.group(1)
    zone = ""
    zm = re.search(r"\bZone=(\S+)", ref)
    if zm:
        zone = _norm(zm.group(1), ZONE_BY_NUM)
    game["entities"][eid] = {
        "id": eid, "cardId": card_id, "zone": zone, "zone_pos": 0,
        "controller": None, "tags": {}, "name": name, "peak_hp": 0,
    }
    if name:
        game["names"][name] = eid
    return eid


def _ref_id(ref):
    """实体引用 -> entityID: 纯数字或 [.. ID=n ..]; 名字形式返回 None"""
    s = ref.strip()
    if s.isdigit():
        return int(s)
    if s.startswith("["):
        m = BRACKET_ID_RE.search(s)
        if m:
            return int(m.group(1))
    return None


def _resolve(game, ref):
    """TAG_CHANGE/SHOW_ENTITY 的实体引用 -> entityID; 未知名字建占位实体避免丢 tag"""
    eid = _ref_id(ref)
    if eid is not None:
        return eid
    name = ref.strip()
    if name in game["names"]:
        return game["names"][name]
    eid = -len(game["entities"]) - 1
    game["entities"][eid] = {
        "id": eid, "cardId": "", "zone": "", "zone_pos": 0,
        "controller": None, "tags": {}, "name": name, "peak_hp": 0,
    }
    game["names"][name] = eid
    return eid


def _apply_tag(game, eid, tag, value):
    ent = game["entities"].get(eid)
    if not ent:
        return
    ent["tags"][tag] = value
    if tag == "ZONE":
        ent["zone"] = _norm(value, ZONE_BY_NUM)
    elif tag == "ZONE_POSITION":
        n = _num(value)
        if n is not None:
            ent["zone_pos"] = n
    elif tag == "CONTROLLER":
        n = _num(value)
        if n is not None:
            ent["controller"] = n
    elif tag == "HEALTH":
        n = _num(value)
        if n is not None:
            ent["peak_hp"] = max(ent["peak_hp"], n)  # 记录见过的最大生命, 推算满血


def parse_power_log(lines):
    """从最后一个 CREATE_GAME 起重放 packet 流 -> (game|None, 起始行, 总行数)"""
    game, start_line, total = None, 0, 0
    for lineno, raw in enumerate(lines, 1):
        total = lineno
        m = PREFIX_RE.match(raw.rstrip("\r\n"))
        if not m:
            continue  # 非 GameState.DebugPrintPower 行 (含 PowerTaskList 重复历史) 直接忽略
        payload = m.group(1)
        if payload[:1] in (" ", "\t"):
            t = TAG_LINE_RE.match(payload.strip())
            if t and game and game["cur"] is not None:
                _apply_tag(game, game["cur"], t.group(1), t.group(2))
            continue
        if game:
            game["cur"] = None
        if CREATE_GAME_RE.match(payload):
            game, start_line = _new_game(), lineno
            continue
        if game is None:
            continue
        m = FULL_ENTITY_RE.match(payload)
        if m:
            game["cur"] = _create_entity(game, m.group(1), m.group(2))
            continue
        m = SHOW_ENTITY_RE.match(payload)
        if m:
            eid = _resolve(game, m.group(1))
            ent = game["entities"].get(eid)
            if ent:
                ent["cardId"] = m.group(2)
            game["cur"] = eid
            continue
        m = HIDE_ENTITY_RE.match(payload)
        if m:
            eid = _resolve(game, m.group(1))
            ent = game["entities"].get(eid)
            if ent:
                ent["cardId"] = ""
            if m.group(2):
                _apply_tag(game, eid, m.group(2), m.group(3) or "")
            game["cur"] = eid
            continue
        m = TAG_CHANGE_RE.match(payload)
        if m:
            _apply_tag(game, _resolve(game, m.group(1)), m.group(2), m.group(3))
            continue
        m = CHANGE_ENTITY_RE.match(payload)
        if m:
            eid = _resolve(game, m.group(1))
            ent = game["entities"].get(eid)
            if ent:
                ent["cardId"] = m.group(2)
            game["cur"] = eid
            continue
        # BLOCK_START / BLOCK_END / META_DATA 等: 最终状态重放不需要块结构, 忽略
    return game, start_line, total


def _tag_int(ent, tag, default=0):
    n = _num(ent["tags"].get(tag, ""))
    return n if n is not None else default


def _ctype(ent):
    return _norm(ent["tags"].get("CARDTYPE", ""), CTYPE_BY_NUM)


def _players(game):
    ps = [e for e in game["entities"].values() if _ctype(e) == "PLAYER"]
    ps.sort(key=lambda e: _tag_int(e, "PLAYER_ID"))
    return ps


def _game_entity(game):
    return next((e for e in game["entities"].values() if _ctype(e) == "GAME"), None)


def _of_side(game, controller, zone, ctype):
    out = [e for e in game["entities"].values()
           if e["controller"] == controller and e["zone"] == zone and _ctype(e) == ctype]
    out.sort(key=lambda e: (e["zone_pos"], e["id"]))
    return out


def _in_zone(game, controller, zone):
    return [e for e in game["entities"].values() if e["controller"] == controller and e["zone"] == zone]


def _playstate(ent):
    return _norm(ent["tags"].get("PLAYSTATE", ""), PLAYSTATE_BY_NUM)


def is_mulligan(game):
    for e in list(game["entities"].values()):
        if _norm(e["tags"].get("MULLIGAN_STATE", ""), MULLIGAN_BY_NUM) == "INPUT":
            return True
    return False


def is_game_over(game):
    return any(_playstate(p) in ("WON", "LOST", "TIED") for p in _players(game))


def detect_me(game, player_arg=None):
    """我方判定: HAND 区卡牌 ID 持续可见 (cardId 非空) 的那一方。
    返回 (我方 player entity|None, 对方 player entity|None, 是否无法判定)"""
    players = _players(game)
    if player_arg:
        for p in players:
            if p["name"] == player_arg:
                other = [x for x in players if x is not p]
                return p, (other[0] if other else None), False
        sys.exit(f"找不到玩家: {player_arg} (现有: {'、'.join(_player_name(p) for p in players)})")
    counts = {}
    for e in game["entities"].values():
        if e["zone"] == "HAND" and e["cardId"] and e["controller"]:
            counts[e["controller"]] = counts.get(e["controller"], 0) + 1
    if counts:
        best = max(counts, key=counts.get)
        if list(counts.values()).count(counts[best]) == 1:
            for p in players:
                if _tag_int(p, "PLAYER_ID") == best:
                    other = [x for x in players if x is not p]
                    return p, (other[0] if other else None), False
    return (None, None, True)


def _card_name(lookup, card_id):
    if not card_id:
        return "未知卡牌"
    c = lookup.get(card_id)
    return c["name"] if c and c.get("name") else HERO_POWER_NAMES.get(card_id, card_id)


def _side_class(lookup, class_names, ent):
    c = lookup.get(ent["cardId"]) if ent else None
    return class_names.get(c.get("cardClass"), "未知职业") if c and c.get("cardClass") else "未知职业"


def _hero_line(lookup, game, controller):
    hero = next(iter(_of_side(game, controller, "PLAY", "HERO")), None)
    if not hero:
        return "英雄：无"
    hp = _tag_int(hero, "HEALTH")
    max_hp = _tag_int(hero, "MAX_HEALTH") or hero["peak_hp"] or hp
    armor = _tag_int(hero, "ARMOR")
    weapon = next(iter(_of_side(game, controller, "PLAY", "WEAPON")), None)
    power = next(iter(_of_side(game, controller, "PLAY", "HERO_POWER")), None)
    wname = _card_name(lookup, weapon["cardId"]) if weapon else "无"
    pname = _card_name(lookup, power["cardId"]) if power else "无"
    pstate = "已用" if power and _tag_int(power, "EXHAUSTED") == 1 else "未用"
    return f"英雄：{_card_name(lookup, hero['cardId'])} 血 {hp}/{max_hp} 护甲 {armor} 武器 {wname} 技能 {pname}({pstate})"


def _minion_tags(ent):
    tags = []
    atk = _tag_int(ent, "ATK")
    for key, zh in MINION_TAGS:
        if _tag_int(ent, key) == 1:
            tags.append(f"[{zh}]")
    if _tag_int(ent, "JUST_PLAYED") == 1 or (_tag_int(ent, "EXHAUSTED") == 1 and atk > 0):
        tags.append("[本回合上场]")
    if _tag_int(ent, "FROZEN") == 1:
        tags.append("[冻结]")
    return "".join(tags)


def _board_rows(lookup, game, controller):
    rows = []
    for i, m in enumerate(_of_side(game, controller, "PLAY", "MINION"), 1):
        hp = _tag_int(m, "HEALTH") or m["peak_hp"]
        rows.append(f"  {i}. {_card_name(lookup, m['cardId'])} {_tag_int(m, 'ATK')}/{hp} {_minion_tags(m)}".rstrip())
    return rows


def _hand_rows(lookup, game, controller):
    rows = []
    hand = sorted((e for e in _in_zone(game, controller, "HAND")), key=lambda e: (e["zone_pos"], e["id"]))
    for i, h in enumerate(hand, 1):
        c = lookup.get(h["cardId"])
        cost = _num(h["tags"].get("COST"))
        if cost is None:
            cost = c.get("cost") if c and c.get("cost") is not None else 0
        ctype = TYPE_ZH.get(_ctype(h), _ctype(h) or "未知")
        body = f"{_card_name(lookup, h['cardId'])} {cost}费"
        if _ctype(h) == "MINION":
            body += f" {_tag_int(h, 'ATK')}/{_tag_int(h, 'HEALTH') or h['peak_hp']}"
        rows.append(f"  {i}. {body} {ctype}")
    return rows


def _player_name(p):
    return p["name"] or f"玩家{_tag_int(p, 'PLAYER_ID')}"


def _mana_line(game, me, mulligan):
    if not me:
        return ""
    if mulligan:
        return "我的法力 未开始"
    res = _tag_int(me, "RESOURCES")
    used = _tag_int(me, "RESOURCES_USED")
    temp = _tag_int(me, "TEMP_RESOURCES")
    return f"我的法力 {res - used + temp}/{res}（已用 {used}）"


def render_panel(game, start_line, total, lookup, class_names, player_arg=None):
    out = ["=== 炉石对局面板 ==="]
    if game is None or not game["entities"]:
        out.append("（尚未开始对局或日志为空）")
        out.append(f"# 实体总数 0 | 解析起始行 {start_line} | 日志总行 {total}")
        return "\n".join(out)

    players = _players(game)
    me, opp, unknown = detect_me(game, player_arg)
    mulligan = is_mulligan(game)
    sides = [(opp, "对方"), (me, "我方")] if me else [(p, None) for p in players]
    sides = [(p, lab) for p, lab in sides if p]

    if is_game_over(game):
        out.append("【对局已结束】")
    if mulligan:
        out.append("【换牌阶段】手牌已亮出，可给留牌建议")
    if unknown and not player_arg:
        out.append("[无法确定我方，可加 --player 参数]")

    cur = next((p for p in players if _tag_int(p, "CURRENT_PLAYER") == 1), None)
    g_ent = _game_entity(game)
    turn = _tag_int(g_ent, "TURN") if g_ent else 0
    if mulligan:
        owner = "换牌阶段"
    elif me and cur:
        owner = "我的回合" if cur is me else "对方回合"
    elif cur:
        owner = f"{_player_name(cur)} 的回合"
    else:
        owner = "未知"
    mana = _mana_line(game, me, mulligan)
    head = f"回合 {turn} | {owner}"
    if mana:
        head += f" | {mana}"
    out.append(head)

    for p, label in sides:
        ctl = p["controller"]
        pname = _player_name(p)
        name = label or pname
        cls = _side_class(lookup, class_names, next(iter(_of_side(game, ctl, "PLAY", "HERO")), None))
        deck = len(_in_zone(game, ctl, "DECK"))
        fatigue = _tag_int(p, "FATIGUE")
        stat = f"手牌 {len(_in_zone(game, ctl, 'HAND'))} " if label != "我方" else ""
        out.append(f"{name}：{pname}（{cls}）{stat}牌库 {deck} 疲劳 {fatigue}" if label
                   else f"{pname}（{cls}）手牌 {len(_in_zone(game, ctl, 'HAND'))} 牌库 {deck} 疲劳 {fatigue}")
        out.append(_hero_line(lookup, game, ctl))
        if not mulligan:  # 换牌阶段双方场面输出为空
            board = _board_rows(lookup, game, ctl)
            out.append(f"{name}场面({len(board)})：")
            out.extend(board)
        if label == "我方":
            hand = _hand_rows(lookup, game, ctl)
            out.append(f"我方手牌({len(hand)})：")
            out.extend(hand)

    out.append(f"# 实体总数 {len(game['entities'])} | 解析起始行 {start_line} | 日志总行 {total}")
    return "\n".join(out)


def cmd_board(args):
    log_path, use_stdin, player = None, False, None
    for a in args:
        if a.startswith("--"):
            k, _, v = a[2:].partition("=")
            if k == "log":
                log_path = v
            elif k == "stdin":
                use_stdin = True
            elif k == "player":
                player = v
            else:
                sys.exit(f"未知选项: --{k}")
        else:
            sys.exit(f"board 不接受位置参数: {a}\n用法: board [--log=Power.log路径] [--stdin] [--player=玩家名]")
    if use_stdin:
        lines = sys.stdin.buffer.read().decode("utf-8", "replace").splitlines()
    else:
        p = Path(log_path) if log_path else DEFAULT_LOG
        if not p.exists():
            sys.exit(f"日志不存在: {p} (可用 --log=路径 指定, 或 --stdin 从管道读入)")
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()

    game, start_line, total = parse_power_log(lines)
    from hs_deck_cli.deck import CLASS_NAMES, load_db  # 延迟导入避免循环依赖
    lookup = {c.get("id"): c for c in load_db()}
    print(render_panel(game, start_line, total, lookup, CLASS_NAMES, player))
