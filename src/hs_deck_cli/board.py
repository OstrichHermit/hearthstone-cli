"""hs board — 解析炉石客户端日志 Power.log, 输出当前对局面板 (供 AI 军师读取)

用法:
  hs board                          读默认日志 (%LOCALAPPDATA%\\Blizzard\\Hearthstone\\Logs\\Power.log)
  hs board --log=D:\\path\\Power.log  指定日志路径
  hs board --stdin                  从 stdin 读日志 (方便测试)
  hs board --player=鸵鸟居士         手动指定我方玩家名 (默认自动判定)
  hs board --events=12              行动回顾条数 (默认 12, N=0 完全不输出)

只认 GameState.DebugPrintPower() 行 (PowerTaskList 是重复历史, 忽略)。
从最后一个 CREATE_GAME 起全量重放 packet, tag 原子覆盖, 输出最终状态面板;
重放时同步收集 (TURN, 当前行动方) 行动段内的出牌/攻击/技能/抽弃牌事件, 供"行动回顾"输出。
"""
import os
import re
import sys
from pathlib import Path

DEFAULT_LOG = (Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
               / "Blizzard" / "Hearthstone" / "Logs" / "Power.log")
# 日志根目录候选: 本机炉石把日志写在安装目录下(每次启动生成 Hearthstone_<时间戳> 子目录),
# 标准位置 %LOCALAPPDATA%\Blizzard\Hearthstone\Logs 作为兜底
DEFAULT_LOG_DIRS = [
    Path(r"E:\Hearthstone\Logs"),
    (Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
     / "Blizzard" / "Hearthstone" / "Logs"),
]


def find_latest_power(base_dir=None):
    """自动发现最新 Power.log: 目录下直接存在, 或 Hearthstone_<时间戳> 子目录中, 取最新。

    base_dir=None 时扫描 DEFAULT_LOG_DIRS 全部候选, 否则只扫指定目录。返回 Path 或 None。
    """
    bases = [Path(base_dir)] if base_dir else DEFAULT_LOG_DIRS
    best, best_key = None, None
    for b in bases:
        if not b.is_dir():
            continue
        cands = []
        direct = b / "Power.log"
        if direct.is_file():
            cands.append(direct)
        try:
            for d in b.iterdir():
                if d.is_dir() and d.name.startswith("Hearthstone_"):
                    f = d / "Power.log"
                    if f.is_file():
                        cands.append(f)
        except OSError:
            pass
        for f in cands:
            key = (f.parent.name, f.stat().st_mtime)  # 子目录名时间戳字典序=时间序, mtime 兜底
            if best_key is None or key > best_key:
                best, best_key = f, key
    return best

# 行前缀: 时间戳 D 21:47:52.3528941 GameState.DebugPrintPower() - <payload>
PREFIX_RE = re.compile(r"^D [\d:.]+ GameState\.DebugPrintPower\(\) - (.*)$")
# 玩家名行(新版日志): D ... GameState.DebugPrintGame() - PlayerID=2, PlayerName=鸵鸟居士#5869
PLAYER_NAME_RE = re.compile(r"^D [\d:.]+ GameState\.DebugPrintGame\(\) - PlayerID=(\d+), PlayerName=(.+?)\s*$")
TAG_LINE_RE = re.compile(r"^tag=(\S+) value=(.+?)\s*$")
CREATE_GAME_RE = re.compile(r"^CREATE_GAME$")
# 新版: FULL_ENTITY - Creating ID=4 CardID= (实体名/zone 不再内联, 由后续 tag 行填充)
FULL_ENTITY_CREATE_RE = re.compile(r"^FULL_ENTITY - Creating ID=(\d+) CardID=(\S*)\s*$")
# 旧版: FULL_ENTITY - Updating [entityName=.. ID=4 Zone=..] CardID=..
FULL_ENTITY_RE = re.compile(r"^FULL_ENTITY - Updating (.+) CardID=(\S*)\s*$")
# 新版: GameEntity EntityID=1 / Player EntityID=2 PlayerID=1 (名字只在 DebugPrintGame 行)
GAME_ENTITY_RE = re.compile(r"^GameEntity EntityID=(\d+)\s*$")
PLAYER_ENTITY_RE = re.compile(r"^Player EntityID=(\d+) PlayerID=(\d+)")
SHOW_ENTITY_RE = re.compile(r"^SHOW_ENTITY - Updating (?:Entity=)?(.+) CardID=(\S*)\s*$")
HIDE_ENTITY_RE = re.compile(r"^HIDE_ENTITY - Entity=(.+?)(?: tag=(\S+) value=(.+?)\s*)?$")
TAG_CHANGE_RE = re.compile(r"^TAG_CHANGE Entity=(.+) tag=(\S+) value=(.*?)\s*$")  # value 允许为空 (新版日志有 value= 空值行)
CHANGE_ENTITY_RE = re.compile(r"^CHANGE_ENTITY - Updating (?:Entity=)?(.+) CardID=(\S*)\s*$")
BRACKET_ID_RE = re.compile(r"\b[Ii][Dd]=(\d+)")  # 方括号引用大小写 id= 都有
ENTITY_NAME_RE = re.compile(r"entityName=(.*?) [Ii][Dd]=")
BLOCK_TYPE_RE = re.compile(r"^BLOCK_START BlockType=(\S+)")
BLOCK_ENTITY_RE = re.compile(r" Entity=(.+?) EffectCardId=")
BLOCK_TARGET_RE = re.compile(r" Target=(.+?)\s*$")

# 日志里 tag value 可能是数字枚举也可能是字符串, 两种都归一化
ZONE_BY_NUM = {1: "PLAY", 2: "DECK", 3: "HAND", 4: "GRAVEYARD", 5: "REMOVEDFROMGAME", 6: "SETASIDE", 7: "SECRET"}
CTYPE_BY_NUM = {1: "GAME", 2: "PLAYER", 3: "HERO", 4: "MINION", 5: "SPELL", 6: "ENCHANTMENT",
                7: "WEAPON", 9: "TOKEN", 10: "HERO_POWER", 39: "LOCATION"}
MULLIGAN_BY_NUM = {1: "INPUT", 2: "READY", 3: "DONE"}
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
# 可主动打出的牌型 (行动回顾 "打出" 事件)
PLAYABLE_TYPES = {"MINION", "SPELL", "WEAPON", "LOCATION", "HERO"}


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
    return {"entities": {}, "names": {}, "player_names": {}, "cur": None,
            # 行动回顾重放上下文: turn=当前回合值, actor=当前行动方 controller, gate=换牌结束后开闸记事件
            "turn": 0, "actor": None, "gate": False, "events": [], "blocks": []}


def _create_entity_by_id(game, eid, card_id, name="", zone=""):
    game["entities"][eid] = {
        "id": eid, "cardId": card_id, "zone": zone, "zone_pos": 0,
        "controller": None, "tags": {}, "name": name, "peak_hp": 0,
    }
    if name:
        game["names"][name] = eid
    return eid


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
    zm = re.search(r"\b[Zz]one=(\S+)", ref)
    if zm:
        zone = _norm(zm.group(1), ZONE_BY_NUM)
    return _create_entity_by_id(game, eid, card_id, name, zone)


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
    """TAG_CHANGE/SHOW_ENTITY 的实体引用 -> entityID; 未知名字建占位实体避免丢 tag。

    新版日志玩家名 (如 鸵鸟居士#5869) 引用要先对上真正的 Player 实体:
    DebugPrintGame 行建立 PlayerID->名字映射, 此处按名字(含去 #后缀)反查绑定。
    方括号引用内联的本地化卡名 (entityName=) 顺手挂到实体, 供渲染兜底。"""
    eid = _ref_id(ref)
    if eid is not None:
        ent = game["entities"].get(eid)
        if ent and not ent["name"]:
            nm = ENTITY_NAME_RE.search(ref)
            if nm:
                ent["name"] = nm.group(1)
        return eid
    name = ref.strip()
    if name in game["names"]:
        return game["names"][name]
    for pid, pname in game["player_names"].items():
        if name == pname or name == pname.split("#")[0] or name.split("#")[0] == pname:
            for p in _players(game):
                if _tag_int(p, "PLAYER_ID") == pid:
                    game["names"][name] = p["id"]
                    if not p["name"]:
                        p["name"] = pname
                    return p["id"]
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
    old_zone, old_exh = ent["zone"], _num(ent["tags"].get("EXHAUSTED"))
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
    _watch_tag(game, ent, tag, value, old_zone, old_exh)


def _record(game, etype, **kw):
    """记一条事件, 快照当时的回合值与行动方"""
    game["events"].append({"type": etype, "turn": game["turn"], "actor": game["actor"], **kw})


def _watch_tag(game, ent, tag, value, old_zone, old_exh):
    """重放时同步收集行动事件; 换牌结束 (MULLIGAN_STATE=DONE) 前的初始铺场/换牌一律不记"""
    if tag == "TURN" and (_ctype(ent) == "GAME" or ent["id"] == 1):
        n = _num(value)
        if n is not None:
            game["turn"] = n
        return
    if tag == "MULLIGAN_STATE" and _norm(value, MULLIGAN_BY_NUM) == "DONE":
        # 唯一开闸点: 任一方换牌 DONE = 换牌流程结束 (先手先 DONE), 之后才是正式对局行动
        game["gate"] = True
        game["actor"] = ent["controller"] if ent["controller"] is not None else (_tag_int(ent, "PLAYER_ID") or None)
        return
    if tag == "CURRENT_PLAYER" and _ctype(ent) == "PLAYER":
        # 不在此开闸: 换牌流程内也会写 CURRENT_PLAYER (先手标记), 会导致换牌抽牌被记成事件
        return
    if not game["gate"]:
        return
    if tag == "ZONE":
        _zone_event(game, ent, old_zone)
    elif (tag == "EXHAUSTED" and _num(value) == 1 and old_exh != 1
          and _ctype(ent) == "HERO_POWER" and ent["zone"] == "PLAY"):
        _record(game, "power", eid=ent["id"])


def _zone_event(game, ent, old):
    """区域迁移 -> 事件 (只认白名单迁移, SETASIDE/REMOVEDFROMGAME 间挪动等噪音天然过滤)"""
    new, ct = ent["zone"], _ctype(ent)
    if old == "HAND" and new == "PLAY" and ct in PLAYABLE_TYPES:
        target = None
        top = game["blocks"][-1] if game["blocks"] else None
        if top and top["type"] == "PLAY" and _ref_id(top["entity"] or "") == ent["id"]:
            target = top["target"]  # 出牌块的 Target 即法术/武器指向
        _record(game, "play", eid=ent["id"], ctype=ct, target_ref=target)
    elif old == "DECK" and new == "HAND":
        _record(game, "draw", eid=ent["id"])
    elif old == "SETASIDE" and new == "PLAY":
        if ct == "MINION":
            _record(game, "summon", eid=ent["id"])
        elif ct == "WEAPON":
            _record(game, "equip", eid=ent["id"])
    elif new == "GRAVEYARD":
        if old == "PLAY":
            _record(game, "death", eid=ent["id"])
        elif old == "HAND":
            _record(game, "discard", eid=ent["id"])
    elif old == "SETASIDE" and new == "HAND":
        _record(game, "gain", eid=ent["id"])


# 只有 Player 实体才有的 tag (新版日志对手真名经 TAG_CHANGE Entity=<名字> 揭晓, 用于归并)
PLAYER_ONLY_TAGS = {"PLAYSTATE", "CURRENT_PLAYER", "MULLIGAN_STATE", "HERO_ENTITY", "TIMEOUT",
                    "PLAYER_ID", "MAXHANDSIZE", "STARTHANDSIZE", "TEAM_ID", "MAXRESOURCES",
                    "FIRST_PLAYER", "FATIGUE", "LAST_MSG_PLAYED"}


def _handle_packet(game, payload):
    """处理一条 packet 行 (CREATE_GAME 由外层处理); 顶格与块内缩进共用"""
    m = GAME_ENTITY_RE.match(payload)
    if m:
        game["cur"] = _create_entity_by_id(game, int(m.group(1)), "", name="GameEntity")
        return
    m = PLAYER_ENTITY_RE.match(payload)
    if m:
        eid = _create_entity_by_id(game, int(m.group(1)), "")
        game["entities"][eid]["tags"]["PLAYER_ID"] = m.group(2)
        pname = game["player_names"].get(int(m.group(2)))
        if pname and not pname.startswith("UNKNOWN HUMAN PLAYER"):  # 匿名占位不占名字位, 等真名归并
            game["entities"][eid]["name"] = pname
            game["names"][pname] = eid
        game["cur"] = eid
        return
    m = FULL_ENTITY_CREATE_RE.match(payload)
    if m:
        game["cur"] = _create_entity_by_id(game, int(m.group(1)), m.group(2))
        return
    m = FULL_ENTITY_RE.match(payload)
    if m:
        game["cur"] = _create_entity(game, m.group(1), m.group(2))
        return
    m = SHOW_ENTITY_RE.match(payload)
    if m:
        eid = _resolve(game, m.group(1))
        ent = game["entities"].get(eid)
        if ent:
            ent["cardId"] = m.group(2)
        game["cur"] = eid
        return
    m = HIDE_ENTITY_RE.match(payload)
    if m:
        eid = _resolve(game, m.group(1))
        ent = game["entities"].get(eid)
        if ent:
            ent["cardId"] = ""
        if m.group(2):
            _apply_tag(game, eid, m.group(2), m.group(3) or "")
        game["cur"] = eid
        return
    m = TAG_CHANGE_RE.match(payload)
    if m:
        ref, tag, val = m.group(1), m.group(2), m.group(3)
        eid = _resolve(game, ref)
        ent = game["entities"].get(eid)
        if ent and eid < 0 and tag in PLAYER_ONLY_TAGS:
            # 匿名占位 (UNKNOWN HUMAN PLAYER) 或无名 Player 都算待归并, 真名到来时覆盖
            unnamed = [p for p in _players(game)
                       if not p["name"] or p["name"] == "UNKNOWN HUMAN PLAYER"]
            if len(unnamed) == 1:  # 唯一待归并 Player -> 真名归并
                p = unnamed[0]
                game["names"][ent["name"]] = p["id"]
                p["name"] = ent["name"]
                del game["entities"][eid]
                eid = p["id"]
        _apply_tag(game, eid, tag, val)
        return
    m = CHANGE_ENTITY_RE.match(payload)
    if m:
        eid = _resolve(game, m.group(1))
        ent = game["entities"].get(eid)
        if ent:
            ent["cardId"] = m.group(2)
        game["cur"] = eid
        return
    if payload[:11] == "BLOCK_START":
        # 块结构只用于事件归属: 压栈记录; cur 置空, 块参数 tag (PROPOSED_*/SCRIPT_DATA 等) 不误挂实体
        game["cur"] = None
        bt = BLOCK_TYPE_RE.match(payload)
        btype = bt.group(1) if bt else ""
        ent_m = BLOCK_ENTITY_RE.search(payload)
        tgt_m = BLOCK_TARGET_RE.search(payload)
        game["blocks"].append({"type": btype, "entity": ent_m.group(1) if ent_m else None,
                               "target": tgt_m.group(1) if tgt_m else None})
        if btype == "ATTACK" and game["gate"]:
            _record(game, "attack", atk_ref=ent_m.group(1) if ent_m else "",
                    tgt_ref=tgt_m.group(1) if tgt_m else "")
        return
    if payload[:9] == "BLOCK_END" and game["blocks"]:
        game["blocks"].pop()
        game["cur"] = None
        return
    # META_DATA 等: 最终状态重放不需要, 忽略


def parse_power_log(lines):
    """从最后一个 CREATE_GAME 起重放 packet 流 -> (game|None, 起始行, 总行数)"""
    game, start_line, total = None, 0, 0
    for lineno, raw in enumerate(lines, 1):
        total = lineno
        line = raw.rstrip("\r\n")
        nm = PLAYER_NAME_RE.match(line)  # DebugPrintGame 玩家名行 (无 Power 前缀)
        if nm:
            if game is not None:
                pid, pname = int(nm.group(1)), nm.group(2).strip()
                game["player_names"][pid] = pname
                for p in _players(game):  # 名字迟到时补绑: 已占位的名字引用重定向到真身
                    if _tag_int(p, "PLAYER_ID") == pid:
                        p["name"] = p["name"] or pname
                        game["names"][pname] = p["id"]
                        break
            continue
        m = PREFIX_RE.match(line)
        if not m:
            continue  # 非 GameState.DebugPrintPower 行 (含 PowerTaskList 重复历史) 直接忽略
        payload = m.group(1)
        if payload[:1] in (" ", "\t"):
            stripped = payload.strip()
            t = TAG_LINE_RE.match(stripped)
            if t:
                if game and game["cur"] is not None:
                    _apply_tag(game, game["cur"], t.group(1), t.group(2))
                continue
            # 新版日志块内 packet 也带缩进 (TAG_CHANGE/SHOW_ENTITY/GameEntity/Player...)
            if game is None:
                continue
            game["cur"] = None
            _handle_packet(game, stripped)
            continue
        if game:
            game["cur"] = None
        if CREATE_GAME_RE.match(payload):
            game, start_line = _new_game(), lineno
            continue
        if game is None:
            continue
        game["cur"] = None
        _handle_packet(game, payload)
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


DECK_SIZE = 30  # 标准/休闲构筑固定 30 张


def _deck_count(game, controller):
    """逻辑牌库数 = 30 - 手牌 - 场面(随从/武器/地标/奥秘) - 坟场。

    日志 DECK 区实体含换牌塞回的额外实体, 直接数会虚高, 故用减法推算;
    非卡组对象 (英雄/技能/附魔/代币游戏实体) 不计入。"""
    used = 0
    for e in game["entities"].values():
        if e["controller"] != controller:
            continue
        ct = _ctype(e)
        if ct in ("HERO", "HERO_POWER", "GAME", "PLAYER", "ENCHANTMENT"):
            continue
        if e["zone"] in ("HAND", "PLAY", "SECRET", "GRAVEYARD"):
            used += 1
    return max(0, DECK_SIZE - used)


def _playstate(ent):
    return _norm(ent["tags"].get("PLAYSTATE", ""), PLAYSTATE_BY_NUM)


def is_mulligan(game):
    """换牌阶段判定: 只看真实 Player 实体 (占位/匿名实体的残留 INPUT 不算)"""
    return any(_norm(p["tags"].get("MULLIGAN_STATE", ""), MULLIGAN_BY_NUM) == "INPUT"
               for p in _players(game))


def is_game_over(game):
    return any(_playstate(p) in ("WON", "LOST", "TIED") for p in _players(game))


def detect_me(game, player_arg=None):
    """我方判定: HAND 区卡牌 ID 持续可见 (cardId 非空) 的那一方。
    返回 (我方 player entity|None, 对方 player entity|None, 是否无法判定)"""
    players = _players(game)
    if player_arg:
        for p in players:
            if p["name"] == player_arg or (p["name"] or "").split("#")[0] == player_arg:
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


def _card_name(lookup, card_id, ent=None):
    if not card_id:
        return "未知卡牌"
    c = lookup.get(card_id)
    if c and c.get("name"):
        return c["name"]
    if card_id in HERO_POWER_NAMES:
        return HERO_POWER_NAMES[card_id]
    if ent and ent.get("name"):  # 日志方括号引用里的本地化名 (新卡/token 不在卡牌库时的兜底)
        return ent["name"]
    return card_id


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
    wname = _card_name(lookup, weapon["cardId"], weapon) if weapon else "无"
    pname = _card_name(lookup, power["cardId"], power) if power else "无"
    pstate = "已用" if power and _tag_int(power, "EXHAUSTED") == 1 else "未用"
    return f"英雄：{_card_name(lookup, hero["cardId"], hero)} 血 {hp}/{max_hp} 护甲 {armor} 武器 {wname} 技能 {pname}({pstate})"


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
        rows.append(f"  {i}. {_card_name(lookup, m["cardId"], m)} {_tag_int(m, 'ATK')}/{hp} {_minion_tags(m)}".rstrip())
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
        body = f"{_card_name(lookup, h["cardId"], h)} {cost}费"
        if _ctype(h) == "MINION":
            body += f" {_tag_int(h, 'ATK')}/{_tag_int(h, 'HEALTH') or h['peak_hp']}"
        rows.append(f"  {i}. {body} {ctype}")
    return rows


def _player_name(p):
    name = p["name"] or f"玩家{_tag_int(p, 'PLAYER_ID')}"
    return name.split("#")[0]  # 战网名 鸵鸟居士#5869 -> 鸵鸟居士


# ---------- 行动回顾渲染 ----------

def _ev_name(lookup, game, eid, fallback=""):
    """事件实体 -> 显示名: 优先最终 cardId 查中文卡名 (对手出牌等事后揭示也能取到)"""
    ent = game["entities"].get(eid) if eid is not None else None
    if ent:
        if ent["cardId"]:
            return _card_name(lookup, ent["cardId"], ent)
        if ent["name"] and not ent["name"].startswith("UNKNOWN"):
            return ent["name"]
    return fallback


def _ev_target(lookup, game, me, opp, ref):
    """实体引用 -> 目标名; 是某方英雄则显示 我方英雄/对方英雄"""
    if not ref or ref == "0":
        return None
    eid = _ref_id(ref)
    ent = game["entities"].get(eid) if eid is not None else None
    if ent and _ctype(ent) == "HERO":
        if me and ent["controller"] == me["controller"]:
            return "我方英雄"
        if opp and ent["controller"] == opp["controller"]:
            return "对方英雄"
        return _ev_name(lookup, game, eid) or "英雄"
    nm = _ev_name(lookup, game, eid, fallback=ref if ref[:1].isalpha() else "")
    return nm or "未知目标"


def _ev_side(game, me, opp, actor):
    """行动方 controller -> 我方/对方; 判不出我方时退回玩家名"""
    if me and actor == me["controller"]:
        return "我方"
    if opp and actor == opp["controller"]:
        return "对方"
    for p in _players(game):
        if p["controller"] == actor:
            return _player_name(p)
    return "未知"


def _single_event_text(lookup, game, me, opp, ev):
    eid = ev.get("eid")
    if ev["type"] == "play":
        name = _ev_name(lookup, game, eid, "未知卡牌")
        ct = ev.get("ctype")
        if ct == "HERO":
            return f"打出英雄牌 {name}"
        body = f"打出 {TYPE_ZH.get(ct, ct or '卡牌')}「{name}」"
        m = game["entities"].get(eid)
        if ct == "MINION" and m:
            body += f"{_tag_int(m, 'ATK')}/{_tag_int(m, 'HEALTH') or m['peak_hp']}"
        if ct != "MINION":
            tgt = _ev_target(lookup, game, me, opp, ev.get("target_ref"))
            if tgt:
                body += f"→ {tgt}"
        return body
    if ev["type"] == "attack":
        ref = ev.get("atk_ref") or ""
        atk_id = _ref_id(ref)
        name = _ev_name(lookup, game, atk_id, fallback=ref if ref[:1].isalpha() else "") or "未知随从"
        return f"攻击：{name} → {_ev_target(lookup, game, me, opp, ev.get('tgt_ref')) or '未知目标'}"
    if ev["type"] == "power":
        return f"英雄技能 {_ev_name(lookup, game, eid, '未知技能')}"
    if ev["type"] == "summon":
        return f"召唤 {_ev_name(lookup, game, eid) or '未知随从'}"
    if ev["type"] == "equip":
        return f"装备武器 {_ev_name(lookup, game, eid) or '未知武器'}"
    if ev["type"] == "death":
        return f"死亡：{_ev_name(lookup, game, eid, '未知卡牌')}"
    return str(ev["type"])


def _event_lines(lookup, game, me, opp):
    """事件列表 -> [(回合, 行动方, 文本)]; 连续抽牌/弃牌/获得按段合并计数"""
    lines, evs, i = [], game["events"], 0
    n = len(evs)
    while i < n:
        ev = evs[i]
        t, actor, et = ev["turn"], ev["actor"], ev["type"]
        if et in ("draw", "discard", "gain"):
            j, names, unknown = i, [], 0
            while (j < n and evs[j]["type"] == et and evs[j]["turn"] == t and evs[j]["actor"] == actor):
                nm = _ev_name(lookup, game, evs[j].get("eid"))
                if nm:
                    names.append(nm)
                else:
                    unknown += 1
                j += 1
            if et == "draw":
                if len(names) == 1 and not unknown:
                    lines.append((t, actor, f"抽牌 {names[0]}"))
                else:
                    body = f"抽牌 {len(names) + unknown} 张"
                    if names:
                        body += f"（{'、'.join(names)}）"
                    lines.append((t, actor, body))
            else:
                verb = "弃牌" if et == "discard" else "获得"
                for nm in names:
                    lines.append((t, actor, f"{verb}：{nm}" if et == "discard" else f"{verb} {nm}"))
                if unknown:
                    lines.append((t, actor, f"{verb} {unknown} 张牌" if et == "gain" else f"{verb} {unknown} 张"))
            i = j
            continue
        lines.append((t, actor, _single_event_text(lookup, game, me, opp, ev)))
        i += 1
    return lines


def _events_section(lookup, game, me, opp, limit):
    lines = _event_lines(lookup, game, me, opp)
    out = ["=== 行动回顾 ==="]
    if not lines:
        out.append("（暂无行动记录）")
        return out
    out += [f"[第 {t} 回合·{_ev_side(game, me, opp, actor)}] {txt}"
            for t, actor, txt in lines[-limit:]]
    cur_t, cur_actor = game["turn"], game["actor"]
    if cur_actor is not None and not any(t == cur_t and a == cur_actor for t, a, _ in lines):
        out.append(f"[第 {cur_t} 回合·{_ev_side(game, me, opp, cur_actor)}] （尚未行动或无动作）")
    return out


def _mana_line(game, me, mulligan):
    if not me:
        return ""
    if mulligan:
        return "我的法力 未开始"
    res = _tag_int(me, "RESOURCES")
    used = _tag_int(me, "RESOURCES_USED")
    temp = _tag_int(me, "TEMP_RESOURCES")
    return f"我的法力 {res - used + temp}/{res}（已用 {used}）"


def render_panel(game, start_line, total, lookup, class_names, player_arg=None, events_n=12):
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
    # 新版日志 GameEntity 的 TURN 是双方合计手数, 玩家自己的 TURN tag 才是"第 N 回合"
    if turn and me and _tag_int(me, "TURN"):
        head = f"总第 {turn} 手 | 我方第 {_tag_int(me, 'TURN')} 回合 | {owner}"
    else:
        head = f"回合 {turn} | {owner}"
    if mana:
        head += f" | {mana}"
    out.append(head)

    for p, label in sides:
        ctl = p["controller"]
        pname = _player_name(p)
        name = label or pname
        cls = _side_class(lookup, class_names, next(iter(_of_side(game, ctl, "PLAY", "HERO")), None))
        deck = _deck_count(game, ctl)
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

    if events_n and not mulligan:  # 行动回顾放在调试行前; 换牌阶段/无对局不输出
        out.extend(_events_section(lookup, game, me, opp, events_n))

    out.append(f"# 实体总数 {len(game['entities'])} | 解析起始行 {start_line} | 日志总行 {total}")
    return "\n".join(out)


def cmd_board(args):
    log_path, use_stdin, player, events_n = None, False, None, 12
    for a in args:
        if a.startswith("--"):
            k, _, v = a[2:].partition("=")
            if k == "log":
                log_path = v
            elif k == "stdin":
                use_stdin = True
            elif k == "player":
                player = v
            elif k == "events":
                events_n = _num(v)
                if events_n is None or events_n < 0:
                    sys.exit("--events 需要非负整数 (默认 12, N=0 完全不输出行动回顾)")
            else:
                sys.exit(f"未知选项: --{k}")
        else:
            sys.exit(f"board 不接受位置参数: {a}\n"
                     "用法: board [--log=Power.log路径] [--stdin] [--player=玩家名] [--events=N]")
    if use_stdin:
        lines = sys.stdin.buffer.read().decode("utf-8", "replace").splitlines()
    else:
        p = Path(log_path) if log_path else find_latest_power()
        if not p or not p.is_file():
            sys.exit(f"未找到 Power.log (已扫描候选目录: {'; '.join(str(d) for d in DEFAULT_LOG_DIRS)})"
                     f"\n可用 --log=路径 指定, 或 --stdin 从管道读入")
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()

    game, start_line, total = parse_power_log(lines)
    from hs_deck_cli.deck import CLASS_NAMES, load_db  # 延迟导入避免循环依赖
    lookup = {c.get("id"): c for c in load_db()}
    print(render_panel(game, start_line, total, lookup, CLASS_NAMES, player, events_n=events_n))
