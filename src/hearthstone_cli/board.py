"""hs board — 解析炉石客户端日志 Power.log, 输出当前对局面板 (供 AI 军师读取)

用法:
  hs board                          读默认日志 (%LOCALAPPDATA%\\Blizzard\\Hearthstone\\Logs\\Power.log)
  hs board --log=D:\\path\\Power.log  指定日志路径
  hs board --stdin                  从 stdin 读日志 (方便测试)
  hs board --player=鸵鸟居士         手动指定我方玩家名 (默认自动判定)
  hs board --events=N               行动回顾带最近 N 个回合 (默认 3, N=0 完全不输出)

只认 GameState.DebugPrintPower() 行 (PowerTaskList 是重复历史, 忽略)。
从最后一个 CREATE_GAME 起全量重放 packet, tag 原子覆盖, 输出最终状态面板;
重放时同步收集行动事件 (出牌/攻击/技能/抽弃牌; 起手发牌与换牌重抽记为开局回合), 供"行动回顾"输出。
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
# Target 值: 方括号实体引用 (内含空格) 或裸词 (后面还跟 SubOption= 等字段, 不能吞到行尾)
BLOCK_TARGET_RE = re.compile(r" Target=(\[.*?\]|\S+)")

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
# 经典技能描述兜底 (HearthstoneJSON 全量库缺基础技能条目, CS2_084 还被同名法术占用)
HERO_POWER_TEXTS = {
    "CS2_034": "造成1点伤害。", "CS2_056": "抽一张牌，你的英雄受到2点伤害。",
    "CS2_084": "获得2点护甲值。", "CS2_049": "召唤一个随机基础图腾。",
    "CS2_101": "召唤两个1/1的白银之手新兵。", "DS1h_292": "对敌方英雄造成2点伤害。",
    "CS2_017": "你的英雄本回合+1攻击力，并获得1点护甲值。", "CS1h_001": "恢复2点生命值。",
    "CS2_083b": "你的英雄本回合+1攻击力。",
}
# 按技能名的描述兜底: 经典技能的皮肤变体在 full 库里文本脏 (稳固射击变体自带重复段), 不可依赖
HERO_POWER_DESC_BY_NAME = {
    "全副武装": "获得2点护甲值。", "全副武装！": "获得2点护甲值。",
    "稳固射击": "对敌方英雄造成2点伤害。", "次级治疗术": "恢复2点生命值。",
    "火焰冲击": "造成1点伤害。", "生命分流": "抽一张牌，你的英雄失去2点生命值。",
    "图腾召唤": "召唤一个随机基础图腾。", "援军": "召唤两个1/1的白银之手新兵。",
    "变形": "你的英雄本回合+1攻击力，并获得1点护甲值。", "匕首精通": "你的英雄本回合+1攻击力。",
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
            # 行动回顾重放上下文: turn=当前回合值, actor=当前行动方 controller, gate=换牌结束后开闸记事件,
            # grace=开闸宽限期 (换牌重抽归入开局事件, 首个正式回合开始时关闭), opening_seen=开局发牌去重
            "turn": 0, "actor": None, "gate": False, "grace": False, "events": [], "blocks": [],
            "opening_seen": set()}


def _create_entity_by_id(game, eid, card_id, name="", zone=""):
    game["entities"][eid] = {
        "id": eid, "cardId": card_id, "zone": zone, "zone_pos": 0,
        "controller": None, "tags": {}, "name": name, "peak_hp": 0, "fresh": True,
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
    old_val = _num(ent["tags"].get(tag))
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
    # 正式对局中的回复类变化: DAMAGE 回落=治疗, ARMOR 上升=英雄加甲 (开局灌 tag/换牌宽限期不算;
    # zone 限定 PLAY 挡掉手牌区卡牌自带 ARMOR 数据假象)
    if game["gate"] and not game["grace"] and ent["zone"] == "PLAY":
        new_val = _num(value)
        if tag == "DAMAGE" and None not in (old_val, new_val) and new_val < old_val:
            base = _tag_int(ent, "HEALTH") or ent["peak_hp"]
            _record(game, "heal", eid=ent["id"], hp=(max(base - old_val, 0), max(base - new_val, 0)))
        elif tag == "ARMOR" and _ctype(ent) == "HERO" and None not in (old_val, new_val) and new_val > old_val:
            _record(game, "armor", eid=ent["id"], gain=new_val - old_val)
    if (tag == "FATIGUE" and _ctype(ent) == "PLAYER" and game["gate"] and not game["grace"]):
        new_val = _num(value)
        if None not in (old_val, new_val) and new_val > old_val:  # 第 N 次疲劳=抽空牌库, 该玩家英雄扣 N 血
            _record(game, "fatigue", eid=ent["id"], count=new_val)
    # FULL_ENTITY 直接建在场面上的英雄技能 = 技能被替换/升级 (灌注/英雄牌/形态切换), 无 zone 迁移可监听;
    # 开局/换牌期创建的实体只清标记不记事件, 避免自带技能误报
    if ent.get("fresh"):
        if not game["gate"] or game["grace"]:
            ent["fresh"] = False
        elif ent["zone"] == "PLAY" and _ctype(ent) == "HERO_POWER":
            ent["fresh"] = False
            _record(game, "power_change", eid=ent["id"])
        elif ent["zone"] and ent["zone"] != "PLAY":
            ent["fresh"] = False  # 迁移入场/库中实体不走技能变更事件


def _record(game, etype, turn=None, actor=None, **kw):
    """记一条事件, 快照当时的回合值与行动方 (turn/actor 显式传入时覆盖)"""
    game["events"].append({"type": etype,
                           "turn": game["turn"] if turn is None else turn,
                           "actor": game["actor"] if actor is None else actor, **kw})


def _record_opening(game, ent):
    """起手发牌/换牌重抽 -> 开局抽牌事件 (同实体去重, 回合记 0 渲染为"开局")"""
    if ent["id"] in game["opening_seen"]:
        return
    game["opening_seen"].add(ent["id"])
    _record(game, "draw", eid=ent["id"], turn=0, actor=ent["controller"])


def _stat_snap(game, eid):
    """随从当前攻血快照 (当前血=HEALTH-DAMAGE, 炉石受伤走 DAMAGE 累积); 非随从/取不到返回 None"""
    ent = game["entities"].get(eid) if eid is not None else None
    if not ent or _ctype(ent) != "MINION":
        return None
    atk = _tag_int(ent, "ATK")
    hp = (_tag_int(ent, "HEALTH") or ent["peak_hp"]) - _tag_int(ent, "DAMAGE")
    return atk, max(hp, 0)


def _watch_tag(game, ent, tag, value, old_zone, old_exh):
    """重放时同步收集行动事件; 换牌结束 (MULLIGAN_STATE=DONE) 前的初始铺场/换牌一律不记"""
    if tag == "TURN" and (_ctype(ent) == "GAME" or ent["id"] == 1):
        n = _num(value)
        if n is not None:
            game["turn"] = n
        game["grace"] = False  # TURN 变化兜底结束抽牌宽限期
        return
    if tag == "STEP" and _ctype(ent) == "GAME" and str(value) == "MAIN_READY":
        # 首个正式回合开始 (MAIN_READY), 宽限期结束, 之后的 DECK->HAND 都是正式抽牌
        game["grace"] = False
        return
    if tag == "MULLIGAN_STATE" and _norm(value, MULLIGAN_BY_NUM) == "DONE":
        # 唯一开闸点: 任一方换牌 DONE = 换牌流程结束 (先手先 DONE), 之后才是正式对局行动
        game["gate"] = True
        game["grace"] = True  # 开闸宽限: 引擎才落盘的换牌塞回/新抽移动不算正式抽牌
        game["actor"] = ent["controller"] if ent["controller"] is not None else (_tag_int(ent, "PLAYER_ID") or None)
        return
    if tag == "CURRENT_PLAYER" and _ctype(ent) == "PLAYER":
        if _num(value) == 1:
            # 每次回合切换都刷新行动方 (否则行动回顾全部归到换牌 DONE 时的先手头上)
            game["actor"] = ent["controller"] if ent["controller"] is not None else (_tag_int(ent, "PLAYER_ID") or None)
        return
    if not game["gate"]:
        if tag == "ZONE":
            _zone_event(game, ent, old_zone)  # 开闸前只可能命中 DECK->HAND 白名单 (起手发牌)
        return
    if tag == "ZONE":
        _zone_event(game, ent, old_zone)
    elif (tag == "EXHAUSTED" and _num(value) == 1 and old_exh != 1
          and _ctype(ent) == "HERO_POWER" and ent["zone"] == "PLAY"):
        _record(game, "power", eid=ent["id"])


def _zone_event(game, ent, old):
    """区域迁移 -> 事件 (只认白名单迁移, SETASIDE/REMOVEDFROMGAME 间挪动等噪音天然过滤)"""
    new, ct = ent["zone"], _ctype(ent)
    if old == "DECK" and new == "HAND":
        if not game["gate"] or game["grace"]:
            _record_opening(game, ent)  # 起手发牌/换牌重抽 -> 开局事件 (换牌塞回不在此白名单)
        else:
            _record(game, "draw", eid=ent["id"])
        return
    if not game["gate"]:
        return
    if old == "HAND" and new == "PLAY" and ct in PLAYABLE_TYPES:
        target = None
        top = game["blocks"][-1] if game["blocks"] else None
        if top and top["type"] == "PLAY" and _ref_id(top["entity"] or "") == ent["id"]:
            target = top["target"]  # 出牌块的 Target 即法术/武器指向
        _record(game, "play", eid=ent["id"], ctype=ct, target_ref=target,
                stat=_stat_snap(game, ent["id"]))
    elif old == "PLAY" and new == "HAND" and ct in PLAYABLE_TYPES:
        _record(game, "bounce", eid=ent["id"])  # 被移回手牌 (对方亡语/法术效果), 不记则场面凭空少人
    elif old == "HAND" and new == "SECRET":
        # 奥秘/任务打出: 打出时刻 cardId 未必可见 (对方奥秘触发才揭示), 快照可见性防止渲染时信息穿越
        _record(game, "secret_play", eid=ent["id"], known=bool(ent["cardId"]))
    elif old == "SETASIDE" and new == "PLAY":
        if ct == "MINION":
            _record(game, "summon", eid=ent["id"], stat=_stat_snap(game, ent["id"]))
        elif ct == "WEAPON":
            _record(game, "equip", eid=ent["id"])
    elif new == "GRAVEYARD":
        if old == "SECRET":
            _record(game, "secret_trigger", eid=ent["id"])  # 奥秘触发后进坟场, cardId 此时已揭示
        elif old == "PLAY" and ct in ("MINION", "HERO", "WEAPON", "LOCATION"):
            _record(game, "death", eid=ent["id"], stat=_stat_snap(game, ent["id"]))  # 法术/技能结算进坟场不算死亡
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
            # 起手直接建在手牌区的卡 (后手整手/幸运币) 没有区域迁移, 靠揭示时机记开局发牌
            if (not game["gate"] or game["grace"]) and ent["zone"] == "HAND" and ent["cardId"]:
                _record_opening(game, ent)
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
            atk_ref = ent_m.group(1) if ent_m else ""
            tgt_ref = tgt_m.group(1) if tgt_m else ""
            _record(game, "attack", atk_ref=atk_ref, tgt_ref=tgt_ref,
                    atk_stat=_stat_snap(game, _ref_id(atk_ref)),
                    tgt_stat=_stat_snap(game, _ref_id(tgt_ref)))
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
        return "未知卡牌" if not (ent and ent.get("name")) else ent["name"]
    if "UNKNOWN ENTITY" in card_id:  # 新版日志 token 占位文本, 不是真实 cardId
        return "未知随从"
    c = lookup.get(card_id)
    if c and c.get("name"):
        return c["name"]
    if card_id in HERO_POWER_NAMES:
        return HERO_POWER_NAMES[card_id]
    if ent and ent.get("name"):  # 日志方括号引用里的本地化名 (新卡/token 不在卡牌库时的兜底)
        return ent["name"]
    return card_id


def _plain_text(html):
    """卡牌 HTML 描述 -> 纯文本 (去标签 + 取 @ 升级段第一段 + 占位符转X + 压缩空白)"""
    text = (html or "").replace("<b>@</b>", "X")  # 段内动态数值占位 (@ 包在标签里, 区别于段分隔符)
    text = text.split("@")[0]  # 多阶段升级卡 text 用裸 @ 拼接多份, 取第一段
    text = re.sub(r"\{\d+\}", "X", text)  # {0} 等动态数值占位符
    text = re.sub(r"\$[a-zA-Z](?=\d)", "", text)  # $d2 等变量标记 ($字母+数字)
    text = text.replace("$", "").replace("#", "")  # $/# 数值高亮标记, 游戏内渲染不显示
    return " ".join(re.sub(r"<[^>]+>", "", text).split())


def _ev_desc(lookup, game, eid, limit=48):
    """事件实体的卡牌效果描述 (去 HTML, 超长截断 48 字符加…); 查不到/无描述返回空"""
    ent = game["entities"].get(eid) if eid is not None else None
    c = lookup.get(ent["cardId"]) if ent and ent["cardId"] else None
    text = _plain_text((c or {}).get("text") or "")
    if not text:
        return ""
    return text[:limit] + ("…" if len(text) > limit else "")


def _side_class(lookup, class_names, ent):
    c = lookup.get(ent["cardId"]) if ent else None
    return class_names.get(c.get("cardClass"), "未知职业") if c and c.get("cardClass") else "未知职业"


def _hero_line(lookup, game, controller):
    hero = next(iter(_of_side(game, controller, "PLAY", "HERO")), None)
    if not hero:
        return "英雄：无"
    hp = (_tag_int(hero, "HEALTH") or hero["peak_hp"]) - _tag_int(hero, "DAMAGE")  # 当前血=HEALTH-DAMAGE
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
        hp = (_tag_int(m, "HEALTH") or m["peak_hp"]) - _tag_int(m, "DAMAGE")
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
        ctype = "任务" if _tag_int(h, "QUEST") == 1 else TYPE_ZH.get(_ctype(h), _ctype(h) or "未知")
        body = f"{_card_name(lookup, h["cardId"], h)} {cost}费"
        if _ctype(h) == "MINION":
            body += f" {_tag_int(h, 'ATK')}/{(_tag_int(h, 'HEALTH') or h['peak_hp']) - _tag_int(h, 'DAMAGE')}"
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
    if not ref:
        return None
    ref = ref.strip()
    # 0/-1 = 无目标; System.Collections... = 新版日志把内部类型串塞进 Target, 都不算目标
    if ref in ("0", "-1") or ref.startswith("System.") or "`1[" in ref:
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


def _fmt_stat(stat):
    """攻血快照 -> " a/b" 后缀; 无快照返回空"""
    return f" {stat[0]}/{stat[1]}" if stat else ""


def _power_desc(lookup, game, eid, name):
    """英雄技能描述: 经典技能按名/ID 内置, 其余查全量库; 去掉原文自带的'英雄技能'引导词"""
    ent = game["entities"].get(eid)
    desc = (HERO_POWER_DESC_BY_NAME.get(name) or HERO_POWER_TEXTS.get((ent.get("cardId") if ent else "") or "")
            or _ev_desc(lookup, game, eid))
    if desc.startswith("英雄技能"):  # 技能原文自带的引导词与行前缀重复
        desc = desc[len("英雄技能"):].strip()
    return desc


def _single_event_text(lookup, game, me, opp, ev):
    eid = ev.get("eid")
    if ev["type"] == "play":
        name = _ev_name(lookup, game, eid, "未知卡牌")
        ct = ev.get("ctype")
        if ct == "HERO":
            return f"打出英雄牌 {name}"
        body = f"打出 {TYPE_ZH.get(ct, ct or '卡牌')}「{name}」"
        if ct == "MINION":
            body += _fmt_stat(ev.get("stat"))  # 打出时刻快照 (最终态可能已被 buff/打伤)
        desc = ""
        if not (me and ev.get("actor") == me["controller"]):  # 我方牌描述已在抽牌时给过, 打出只补对方
            desc = _ev_desc(lookup, game, eid)
        if desc:
            body += f"<{desc}>"
        if ct != "MINION":
            tgt = _ev_target(lookup, game, me, opp, ev.get("target_ref"))
            if tgt:
                body += f"→ {tgt}"
        return body
    if ev["type"] == "attack":
        ref = ev.get("atk_ref") or ""
        atk_id = _ref_id(ref)
        name = _ev_name(lookup, game, atk_id, fallback=ref if ref[:1].isalpha() else "") or "未知随从"
        tgt = _ev_target(lookup, game, me, opp, ev.get("tgt_ref"))
        # Target=0/-1 = 引擎记录的取消/无目标攻击块, 只显示攻击方, 不编造"未知目标"
        body = f"攻击：{name}{_fmt_stat(ev.get('atk_stat'))}"
        return f"{body} → {tgt}{_fmt_stat(ev.get('tgt_stat'))}" if tgt else body
    if ev["type"] == "power":
        name = _ev_name(lookup, game, eid, "未知技能")
        body = f"英雄技能 {name}"
        desc = _power_desc(lookup, game, eid, name)
        if desc:
            body += f"<{desc}>"
        return body
    if ev["type"] == "power_change":
        name = _ev_name(lookup, game, eid, "未知技能")
        body = f"技能变更 {name}"
        desc = _power_desc(lookup, game, eid, name)
        if desc:
            body += f"<{desc}>"
        return body
    if ev["type"] == "heal":
        ent = game["entities"].get(eid)
        if ent and _ctype(ent) == "HERO":
            name = "未知英雄"
            if me and ent["controller"] == me["controller"]:
                name = "我方英雄"
            elif opp and ent["controller"] == opp["controller"]:
                name = "对方英雄"
        else:
            name = _ev_name(lookup, game, eid, "未知目标")
        hp = ev.get("hp")
        return f"治疗：{name} 血{hp[0]}→{hp[1]}" if hp else f"治疗：{name}"
    if ev["type"] == "armor":
        ent = game["entities"].get(eid)
        if ent and me and ent["controller"] == me["controller"]:
            name = "我方英雄"
        elif ent and opp and ent["controller"] == opp["controller"]:
            name = "对方英雄"
        else:
            name = _ev_name(lookup, game, eid, "未知英雄")
        gain = ev.get("gain")
        return f"获得护甲 {name} +{gain}" if gain is not None else f"获得护甲 {name}"
    if ev["type"] == "summon":
        name = _ev_name(lookup, game, eid) or "未知随从"
        body = f"召唤 {name}{_fmt_stat(ev.get('stat'))}"
        desc = _ev_desc(lookup, game, eid)
        if desc:
            body += f"<{desc}>"
        return body
    if ev["type"] == "bounce":
        return f"回手 {_ev_name(lookup, game, eid, '未知卡牌')}"
    if ev["type"] == "equip":
        return f"装备武器 {_ev_name(lookup, game, eid) or '未知武器'}"
    if ev["type"] == "death":
        return f"死亡：{_ev_name(lookup, game, eid, '未知卡牌')}{_fmt_stat(ev.get('stat'))}"
    if ev["type"] == "secret_play":
        ent = game["entities"].get(eid)
        kind = "任务" if ent and _tag_int(ent, "QUEST") == 1 else "奥秘"  # SECRET 区含奥秘与任务, QUEST tag 区分
        name = _ev_name(lookup, game, eid, "") if ev.get("known") else ""
        return f"打出 {kind}「{name}」" if name else f"打出 {kind}"  # 打出时不可见(对方奥秘)则匿名, 不用触发才揭示的名字
    if ev["type"] == "secret_trigger":
        ent = game["entities"].get(eid)
        kind = "任务" if ent and _tag_int(ent, "QUEST") == 1 else "奥秘"
        name = _ev_name(lookup, game, eid, f"未知{kind}")
        body = f"{kind}{'完成' if kind == '任务' else '触发'} {name}"
        desc = _ev_desc(lookup, game, eid)
        if desc:
            body += f"<{desc}>"
        return body
    if ev["type"] == "fatigue":
        n = ev.get("count")
        return f"疲劳 第 {n} 次（英雄扣 {n} 血）" if n is not None else "疲劳"
    return str(ev["type"])


def _event_lines(lookup, game, me, opp):
    """事件列表 -> [(回合, 行动方, 文本)]; 连续抽牌/弃牌/获得按段合并计数"""
    lines, evs, i = [], game["events"], 0
    opp_ctl = opp["controller"] if opp else None
    n = len(evs)
    while i < n:
        ev = evs[i]
        t, actor, et = ev["turn"], ev["actor"], ev["type"]
        if et in ("draw", "discard", "gain"):
            j, items, unknown = i, [], 0
            while (j < n and evs[j]["type"] == et and evs[j]["turn"] == t and evs[j]["actor"] == actor):
                eid_j = evs[j].get("eid")
                nm = _ev_name(lookup, game, eid_j)
                if nm:
                    items.append((nm, eid_j))
                else:
                    unknown += 1
                j += 1
            if et == "draw":
                if opp_ctl is not None and actor == opp_ctl:
                    # 对方抽牌一律匿名: 对方手牌内容本就不可知, 事后打出揭示的 cardId 不回填到抽牌事件
                    lines.append((t, actor, f"抽牌 {len(items) + unknown} 张"))
                elif len(items) == 1 and not unknown:
                    nm, eid_j = items[0]
                    desc = _ev_desc(lookup, game, eid_j)
                    lines.append((t, actor, f"抽牌 {nm}<{desc}>" if desc else f"抽牌 {nm}"))
                else:
                    body = f"抽牌 {len(items) + unknown} 张"
                    if items:
                        parts = []
                        for nm, eid_j in items:
                            desc = _ev_desc(lookup, game, eid_j)
                            parts.append(f"{nm}<{desc}>" if desc else nm)
                        body += f"（{'、'.join(parts)}）"
                    lines.append((t, actor, body))
            else:
                verb = "弃牌" if et == "discard" else "获得"
                for nm, _ in items:
                    lines.append((t, actor, f"{verb}：{nm}" if et == "discard" else f"{verb} {nm}"))
                if unknown:
                    lines.append((t, actor, f"{verb} {unknown} 张牌" if et == "gain" else f"{verb} {unknown} 张"))
            i = j
            continue
        lines.append((t, actor, _single_event_text(lookup, game, me, opp, ev)))
        i += 1
    return lines


def _events_section(lookup, game, me, opp, turns=3, mulligan=False):
    """行动回顾: 按回合边界自动带最近 turns 个回合 (我方上回合全部+对方上回合全部+我方本回合已发生),
    开局发牌记为回合 0 显示为"开局", 不按条数截断"""
    lines = _event_lines(lookup, game, me, opp)
    out = ["=== 行动回顾 ==="]
    if not lines:
        out.append("（暂无行动记录）")
        return out
    cur_t = game["turn"]
    if turns and cur_t:
        floor = cur_t - (turns - 1)
        lines = [x for x in lines if floor <= x[0] <= cur_t]
    if not lines:
        out.append("（近期无行动记录）")
        return out
    out += [f"[{'开局' if t == 0 else f'第 {t} 回合'}·{_ev_side(game, me, opp, actor)}] {txt}"
            for t, actor, txt in lines]
    cur_actor = game["actor"]
    if cur_actor is not None and not mulligan and not any(t == cur_t and a == cur_actor for t, a, _ in lines):
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


def render_panel(game, start_line, total, lookup, class_names, player_arg=None, events_turns=3):
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
        fatigue = _tag_int(p, "FATIGUE")
        secrets = len(_in_zone(game, ctl, "SECRET"))
        stat = (f"手牌 {len(_in_zone(game, ctl, 'HAND'))} " if label != "我方" else "") + f"奥秘 {secrets} "
        # 不输出"牌库 N": 霍格复制传说等效果会让套牌超 30 张, 30 减法推算必不准, 干脆不给
        out.append(f"{name}：{pname}（{cls}）{stat}疲劳 {fatigue}" if label
                   else f"{pname}（{cls}）手牌 {len(_in_zone(game, ctl, 'HAND'))} 奥秘 {secrets} 疲劳 {fatigue}")
        out.append(_hero_line(lookup, game, ctl))
        if not mulligan:  # 换牌阶段双方场面输出为空
            board = _board_rows(lookup, game, ctl)
            out.append(f"{name}场面({len(board)})：")
            out.extend(board)
        if label == "我方":
            hand = _hand_rows(lookup, game, ctl)
            out.append(f"我方手牌({len(hand)})：")
            out.extend(hand)

    if events_turns:  # 行动回顾放在调试行前; 换牌阶段输出开局发牌供留牌建议
        out.extend(_events_section(lookup, game, me, opp, turns=events_turns, mulligan=mulligan))

    out.append(f"# 实体总数 {len(game['entities'])} | 解析起始行 {start_line} | 日志总行 {total}")
    return "\n".join(out)


def cmd_board(args):
    log_path, use_stdin, player, events_turns = None, False, None, 3
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
                events_turns = _num(v)
                if events_turns is None or events_turns < 0:
                    sys.exit("--events 需要非负整数 (默认 3 = 最近 3 个回合, N=0 完全不输出行动回顾)")
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
    from hearthstone_cli.deck import CLASS_NAMES, load_db, load_full_db  # 延迟导入避免循环依赖
    lookup = {c.get("id"): c for c in load_db()}
    for cid, c in load_full_db().items():
        # 全量库兜底 (英雄技能/token 等非 collectible): collectible 已有的条目不覆盖, 查卡顺序优先原库
        lookup.setdefault(cid, c)
    print(render_panel(game, start_line, total, lookup, CLASS_NAMES, player, events_turns=events_turns))


