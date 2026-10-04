"""hs board — 解析炉石客户端日志 Power.log, 输出当前对局面板 (供 AI 军师读取)

用法:
  hs board                          读默认日志 (%LOCALAPPDATA%\\Blizzard\\Hearthstone\\Logs\\Power.log)
  hs board --log=D:\\path\\Power.log  指定日志路径
  hs board --stdin                  从 stdin 读日志 (方便测试)
  hs board --player=鸵鸟居士         手动指定我方玩家名 (默认自动判定)
  hs board --turns=N                行动回顾带最近 N 个回合 (默认 3, N=0 完全不输出)
  hs board --debug                  末尾附解析调试行 (实体总数/日志行数)

只认 GameState.DebugPrintPower() 行 (PowerTaskList 是重复历史, 忽略)。
从最后一个 CREATE_GAME 起全量重放 packet, tag 原子覆盖, 输出最终状态面板;
重放时同步收集行动事件 (出牌/攻击/技能/抽弃牌), 并单独还原开局语义:
真实起手 -> 双方换牌 (我方带卡名/对方报张数) -> 幸运币 -> START_OF_GAME 开局触发汇总, 供"行动回顾"输出。
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
META_BURN_RE = re.compile(r"^META_DATA - Meta=BURNED_CARD\b")
# Info 行: 方括号实体引用 (内含嵌套 [..], 贪婪取到最后一个 ]) 或裸实体 id
INFO_LINE_RE = re.compile(r"^Info\[\d+\] = (?:\[(.+)\]|(\S+))\s*$")
BRACKET_ID_RE = re.compile(r"\b[Ii][Dd]=(\d+)")  # 方括号引用大小写 id= 都有
ENTITY_NAME_RE = re.compile(r"entityName=(.*?) [Ii][Dd]=")
BLOCK_TYPE_RE = re.compile(r"^BLOCK_START BlockType=(\S+)")
BLOCK_ENTITY_RE = re.compile(r" Entity=(.+?) EffectCardId=")
# Target 值: 方括号实体引用 (内含空格) 或裸词 (后面还跟 SubOption= 等字段, 不能吞到行尾)
BLOCK_TARGET_RE = re.compile(r" Target=(\[.*?\]|\S+)")
TRIGGER_KW_RE = re.compile(r" TriggerKeyword=(\S+)")
# 换牌保留选择行 (非 Power 行): D ... GameState.DebugPrintEntitiesChosen() -   Entities[0]=[... id=52 ... player=2]
# 语义 = 该玩家确定保留的牌 (与实际换掉不一一对应), 换掉 = 起手 - 保留
CHOSEN_ENT_RE = re.compile(r"^D [\d:.]+ GameState\.DebugPrintEntitiesChosen\(\) -\s+Entities\[\d+\]=\[(.+)\]\s*$")
CHOSEN_PLAYER_RE = re.compile(r"\bplayer=(\d+)")
# 对局中 GENERAL 选择 (发现/灾变类, 换牌 MULLIGAN 是另一类型不受影响):
# DebugPrintEntityChoices 列选项 (id/玩家/Source), DebugPrintEntitiesChosen 确认所选 (双方都走这里, 对方无 SendChoices)
EC_HEAD_RE = re.compile(
    r"^D [\d:.]+ GameState\.DebugPrintEntityChoices\(\) - id=(\d+) Player=(\S+).*\bChoiceType=(\S+)")
EC_CHOSEN_HEAD_RE = re.compile(
    r"^D [\d:.]+ GameState\.DebugPrintEntitiesChosen\(\) - id=(\d+) Player=(\S+) EntitiesCount=(\d+)")
EC_BRACKET_RE = re.compile(r"(\[.+\])\s*$")
# 对局元信息行 (非 Power 行): D ... GameState.DebugPrintGame() - GameType=GT_CASUAL / FormatType=FT_STANDARD / BuildNumber=253216
GAME_META_RE = re.compile(r"^D [\d:.]+ GameState\.DebugPrintGame\(\) - (BuildNumber|GameType|FormatType)=(\S+)\s*$")
# 日志超限截断标记 (裸行): Truncating log, which has reached the size limit of 10000KB
TRUNCATE_RE = re.compile(r"Truncating log, which has reached the size limit of (\d+)KB")
GAME_TYPE_ZH = {"GT_RANKED": "排名", "GT_CASUAL": "休闲", "GT_ARENA": "竞技场", "GT_FRIENDLY": "好友对战",
                "GT_VS_AI": "人机对战", "GT_TUTORIAL": "教学", "GT_MERCENARIES": "佣兵战纪",
                "GT_BATTLEGROUNDS": "酒馆战棋", "GT_BATTLEGROUNDS_FRIENDLY": "酒馆好友"}
FORMAT_TYPE_ZH = {"FT_STANDARD": "标准", "FT_WILD": "狂野", "FT_CLASSIC": "经典", "FT_TWIST": "异画"}

# 日志里 tag value 可能是数字枚举也可能是字符串, 两种都归一化
ZONE_BY_NUM = {1: "PLAY", 2: "DECK", 3: "HAND", 4: "GRAVEYARD", 5: "REMOVEDFROMGAME", 6: "SETASIDE", 7: "SECRET"}
CTYPE_BY_NUM = {1: "GAME", 2: "PLAYER", 3: "HERO", 4: "MINION", 5: "SPELL", 6: "ENCHANTMENT",
                7: "WEAPON", 9: "TOKEN", 10: "HERO_POWER", 39: "LOCATION"}
MULLIGAN_BY_NUM = {1: "INPUT", 2: "READY", 3: "DONE"}
PLAYSTATE_BY_NUM = {4: "WON", 5: "LOST", 6: "TIED", 7: "PLAYING", 8: "CONCEDED"}

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

# 随从状态标签 (按此顺序输出, 冻结附注最后; DORMANT 单独处理: 值为剩余唤醒回合数, 显示 [休眠N])
MINION_TAGS = [
    ("TAUNT", "嘲讽"), ("DIVINE_SHIELD", "圣盾"), ("WINDFURY", "风怒"), ("MEGA_WINDFURY", "巨型风怒"),
    ("STEALTH", "潜行"), ("LIFESTEAL", "吸血"), ("POISONOUS", "剧毒"), ("REBORN", "复生"),
    ("CHARGE", "冲锋"), ("RUSH", "突袭"), ("CANT_ATTACK", "不可攻击"),
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
            "pending_stats": [], "opening_seen": set(),
            # 效果层事件: dead_seen=已死亡实体(亡语块判定), death_evs=死亡事件(复生回填),
            # summon_acc=同回合同来源召唤合并, blk_seq=块序号, burn_pending=BURNED_CARD 等待 Info 行
            "dead_seen": set(), "death_evs": {}, "summon_acc": {}, "blk_seq": 0, "burn_pending": False,
            "pending_summon": [],
            # 技能自带护甲内联: hp_armor_pend=技能实体id->[累计护甲, 英雄实体id] 等技能事件消费;
            # heal_watch=待定治疗(同块 HEALTH 下调则改判光环回调), aura_drops=块内 HEALTH 下调记录
            "hp_armor_pend": {}, "heal_watch": [], "aura_drops": [],
            # 对局中 GENERAL 选择: ec_choice=已列选项待确认, ec_confirm=已确认待出事件
            "ec_choice": None, "ec_confirm": None,
            # 亡语/触发块内 SHOW_ENTITY 揭示的实体 -> 亮牌观察 (CARDTYPE=SPELL 出事件, 被藏回/收尾清理)
            "reveal_watch": {},
            # 已在触发时刻出过事件的奥秘 (SHOW_ENTITY 揭示即触发, 坟场迁移时去重不再补发)
            "secret_trig": set(),
            # 开局/换牌语义: dealt=起手发牌, mull_in=换入, kept=该方确定保留的实体 (换掉=dealt-kept)
            "opening": {"dealt": {}, "mull_in": {}, "kept": {}},
            # 对局元信息 (DebugPrintGame 行) 与日志截断标记; first_hero=双方初始英雄实体 id (职业显示锚点)
            "meta": {}, "truncated": False, "trunc_kb": None, "first_hero": {}}


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
        nm = ENTITY_NAME_RE.search(ref)
        if nm and not nm.group(1).startswith("UNKNOWN") and (not ent["name"] or ent["name"].startswith("UNKNOWN")):
            # 内联名迟到: 真名覆盖空名/UNKNOWN 占位 (对方任务打出时引用才带真名)
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


def _apply_tag(game, eid, tag, value, via_change=False):
    """via_change=True 表示这条变化来自 TAG_CHANGE/HIDE_ENTITY packet (真实动作),
    而非 FULL_ENTITY/SHOW_ENTITY 的初始 tag 快照 (休眠/预备类事件只认前者)"""
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
            if ent["id"] > 0 and _ctype(ent) == "HERO":  # 首个拿到归属的英雄实体 = 初始英雄 (变形不影响职业)
                game["first_hero"].setdefault(n, ent["id"])
    elif tag == "HEALTH":
        n = _num(value)
        if n is not None:
            ent["peak_hp"] = max(ent["peak_hp"], n)  # 记录见过的最大生命, 推算满血
    elif tag == "CARDTYPE":
        if ent["controller"] is not None and ent["id"] > 0 and _norm(value, CTYPE_BY_NUM) == "HERO":
            game["first_hero"].setdefault(ent["controller"], ent["id"])
        _note_reveal_cardtype(game, ent, value)
    if tag == "PLAYSTATE" and _norm(value, PLAYSTATE_BY_NUM) == "CONCEDED":
        # 引擎发 CONCEDED 后必跟 LOST 覆盖, 最终态查不到投降; 重放时单独留痕供终局行判定
        ent["conceded"] = True
    _watch_tag(game, ent, tag, value, old_zone, old_exh)
    # 战吼目标: PLAY 块内对其他实体的影响类 tag -> 追加到打出事件
    if tag in BC_TARGET_TAGS and game["gate"] and _ctype(ent) in ("MINION", "HERO", "WEAPON", "LOCATION"):
        _note_bc_target(game, ent)
    # 正式对局中的回复类变化: DAMAGE 回落=治疗, ARMOR 上升=英雄加甲 (开局灌 tag/换牌宽限期不算;
    # zone 限定 PLAY 挡掉手牌区卡牌自带 ARMOR 数据假象; ARMOR 旧值缺省按 0, 首次加甲不漏报)
    if game["gate"] and not game["grace"] and ent["zone"] == "PLAY":
        new_val = _num(value)
        depth = len(game["blocks"])
        if tag == "DAMAGE" and None not in (old_val, new_val) and new_val < old_val:
            drop = next((d for d in game["aura_drops"] if d["eid"] == ent["id"]), None)
            if drop is not None:  # 同块 HEALTH 已同步下调 = 光环回调, 有效生命没变, 不报治疗
                game["aura_drops"].remove(drop)
                _record(game, "aura_cb", eid=ent["id"], vals=drop["vals"])
            else:
                base = _tag_int(ent, "HEALTH") or ent["peak_hp"]
                ev = _record(game, "heal", eid=ent["id"], hp=(max(base - old_val, 0), max(base - new_val, 0)))
                s_kind, s_id = _heal_src(game)
                if s_kind:
                    ev["src_kind"], ev["src_id"] = s_kind, s_id
                game["heal_watch"].append({"ev": ev, "eid": ent["id"], "depth": depth})
        elif tag == "HEALTH" and new_val is not None and old_val is not None and new_val < old_val:
            _note_aura_drop(game, ent, old_val, new_val, depth)
        elif tag == "ARMOR" and _ctype(ent) == "HERO" and new_val is not None and new_val > (old_val or 0):
            gain = new_val - (old_val or 0)
            pid = _power_blk_id(game)
            if pid is not None:  # 技能块自带护甲: 内联进技能行, 不再单独发护甲事件
                _attach_power_armor(game, pid, gain, ent["id"])
            else:
                _record(game, "armor", eid=ent["id"], gain=gain)
    # 效果层伤害/攻击目标反解: 块内 DAMAGE 上升 = 引擎自动结算的伤害
    # (旧值缺省按 0: 皮肤英雄等初始 FULL_ENTITY 不带 DAMAGE tag)
    new_val = _num(value)
    # 攻击块内英雄先破甲后扣血: 护甲损耗也累计进该攻击的实际伤害 (光环增幅/破甲可见)
    top = game["blocks"][-1] if game["blocks"] else None
    if (tag == "ARMOR" and top is not None and top["type"] == "ATTACK"
            and None not in (old_val, new_val) and new_val < old_val):
        aev = top.get("atk_ev")
        if (aev is not None and eid != _ref_id(top.get("entity") or "")
                and top.get("dmg_tgt") in (None, eid)):
            top["dmg_tgt"] = eid
            aev["dmg_amt"] = (aev.get("dmg_amt") or 0) + (old_val - new_val)
    if (tag == "DAMAGE" and game["gate"] and not game["grace"]
            and new_val is not None and new_val > (old_val or 0)):
        dmg_old = old_val or 0
        top = game["blocks"][-1] if game["blocks"] else None
        if top is not None and top["type"] == "ATTACK":
            if eid != _ref_id(top.get("entity") or "") and top.get("atk_ev") is not None:
                if top.get("dmg_tgt") in (None, eid):
                    top["dmg_tgt"] = eid  # 攻击块 Target=0 时受击实体 = 反解目标
                    aev = top["atk_ev"]
                    aev["dmg_amt"] = (aev.get("dmg_amt") or 0) + (new_val - dmg_old)  # 实际伤害 (光环增幅可见)
        elif ent["zone"] == "PLAY" and _ctype(ent) in ("HERO", "MINION"):
            has_atk = any(b["type"] == "ATTACK" for b in game["blocks"])
            trig, trig_i = None, -1
            for bi in range(len(game["blocks"]) - 1, -1, -1):  # 内层优先: 嵌套时伤害执行者是最内层触发块
                b = game["blocks"][bi]
                if b["type"] == "TRIGGER":
                    s = game["entities"].get(_ref_id(b.get("entity") or ""))
                    if s is not None and _ctype(s) != "PLAYER":  # 疲劳/阶段触发(玩家实体)不算来源
                        trig, trig_i = b, bi
                        break
            # 归属看直接所属块: 触发块之内再无 PLAY 块即归触发结算 —— 清场法术 PLAY 块里
            # 嵌套的亡语 TRIGGER 子块不再被外层 PLAY 误杀; PLAY/POWER 直接层的法术伤害
            # 找不到触发块 (或触发块内又嵌 PLAY), 天然不会走到这里, 不重复报
            if trig is not None and not has_atk and not any(
                    b["type"] == "PLAY" for b in game["blocks"][trig_i + 1:]):
                # 亡语/回合结束触发的伤害: 斩杀链回溯 (主动攻击/主动法术伤害已有事件, 不重复)
                sid = _ref_id(trig.get("entity") or "")
                kind = "deathrattle" if (trig.get("kw") == "DEATHRATTLE" or sid in game["dead_seen"]) else "trigger"
                sent = game["entities"].get(sid)
                base = _tag_int(ent, "HEALTH") or ent["peak_hp"]
                _record(game, "eff_damage",
                        actor=(sent["controller"] if sent is not None and sent["controller"] is not None
                               else ent["controller"]),
                        src_kind=kind, src_id=sid, tgt_id=eid, amount=new_val - dmg_old,
                        lethal=bool(base and new_val >= base))
    # 脚本攻击 Target=0 反解: 块内 PROPOSED_DEFENDER 即真实攻击目标
    if (tag == "PROPOSED_DEFENDER" and game["blocks"] and game["blocks"][-1]["type"] == "ATTACK"
            and (ent["id"] == 1 or _ctype(ent) == "GAME")):
        v = _num(value)
        aev = game["blocks"][-1].get("atk_ev")
        if v and aev is not None and aev.get("tgt_ref") in (None, "0", "-1") and game["entities"].get(v) is not None:
            aev["tgt_ref"] = str(v)
    if (tag == "FATIGUE" and _ctype(ent) == "PLAYER" and game["gate"] and not game["grace"]):
        new_val = _num(value)
        if None not in (old_val, new_val) and new_val > old_val:  # 第 N 次疲劳=抽空牌库, 该玩家英雄扣 N 血
            _record(game, "fatigue", eid=ent["id"], count=new_val)
    # 休眠/唤醒 (囚禁类效果): DORMANT 0→1 记囚禁 (来源取块上下文, 战吼块=战吼随从), 1→0 记苏醒;
    # 只认真实 TAG_CHANGE, 实体初始快照自带 DORMANT 不重复报 (召唤事件已覆盖)
    if (tag == "DORMANT" and via_change and game["gate"] and not game["grace"]
            and ent["zone"] == "PLAY" and _ctype(ent) == "MINION"):
        dor_new = _num(value)
        dor_old = old_val if old_val is not None else 0
        if dor_new == 1 and dor_old == 0:
            kind, sid = _summon_ctx(game)
            _record(game, "dormant", eid=ent["id"], src_kind=kind, src_id=sid)
        elif dor_new == 0 and dor_old == 1:
            _record(game, "awaken", eid=ent["id"])
    # DECK_ACTION 块内 (手牌预备动作): 块实体的动作类 tag 变化留摘要供块结束汇总
    if via_change and game["blocks"]:
        for _b in reversed(game["blocks"]):
            if "deck_act" in _b:
                da = _b["deck_act"]
                if ent["id"] == da["eid"]:
                    if tag.startswith("PREPARE") or tag == "DECK_ACTION_COST":
                        da["prepare"] = True
                    elif (not tag.isdigit() and tag not in ("ZONE", "ZONE_POSITION", "EXHAUSTED")
                          and all(t != tag for t, _, _ in da["tagsum"]) and len(da["tagsum"]) < 4):
                        da["tagsum"].append((tag, old_val if old_val is not None else 0, value))
                break
    # FULL_ENTITY 直接建在场面上的英雄技能 = 技能被替换/升级 (灌注/英雄牌/形态切换), 无 zone 迁移可监听;
    # 开局/换牌期创建的实体只清标记不记事件, 避免自带技能误报
    if ent.get("fresh"):
        if not game["gate"] or game["grace"]:
            ent["fresh"] = False
        elif ent["zone"] == "PLAY" and _ctype(ent) == "HERO_POWER":
            ent["fresh"] = False
            pev = _record(game, "power_change", eid=ent["id"])
            pend = game["hp_armor_pend"].pop(ent["id"], None)
            if pend:
                pev["armor"] = pend[0]
        elif ent["zone"] == "PLAY" and _ctype(ent) == "MINION":
            # 亡语/触发/战吼块内直接建在场面的召唤 (否则对方场面凭空多随从)。
            # 延迟到实体 tag 段结束 (下一 packet) 再定性: CREATOR/HAS_BEEN_REBORN 在 ZONE 之后才到
            ent["fresh"] = False
            game["pending_summon"].append(ent["id"])
        elif ent["zone"] == "HAND":
            if ent["controller"] is None:
                pass  # CONTROLLER tag 在 ZONE 之后才到, 保持 fresh 等后续 tag 再定性
            else:
                ent["fresh"] = False
                _record_gain_src(game, ent)  # 触发效果直接送入手牌 (逐月幼龙类)
        elif ent["zone"] == "DECK":
            if ent["controller"] is None:
                pass  # CONTROLLER tag 在 ZONE 之后才到, 保持 fresh 等后续 tag 再定性
            else:
                if game["gate"] and not game["grace"] and game["blocks"]:
                    # 效果生成直接进牌库的实体 (洗入牌库): 挂到所属块, 块结束出汇总事件
                    game["blocks"][-1].setdefault("deck_new", []).append(ent["id"])
                ent["fresh"] = False
        elif ent["zone"] and ent["zone"] != "PLAY":
            ent["fresh"] = False  # 迁移入场/库中实体不走技能变更事件


def _record(game, etype, turn=None, actor=None, **kw):
    """记一条事件, 快照当时的回合值与行动方 (turn/actor 显式传入时覆盖); 返回事件对象供后续回填"""
    ev = {"type": etype,
          "turn": game["turn"] if turn is None else turn,
          "actor": game["actor"] if actor is None else actor, **kw}
    game["events"].append(ev)
    return ev


def _record_opening(game, ent):
    """开局发牌/换牌重抽归类 (同实体去重): 该方保留选择已知后的入手 = 换入, 否则 = 起手发牌"""
    if ent["id"] in game["opening_seen"]:
        return
    game["opening_seen"].add(ent["id"])
    ctl = ent["controller"]
    if ctl is None:
        return
    op = game["opening"]
    key = "mull_in" if ctl in op["kept"] else "dealt"
    op[key].setdefault(ctl, []).append(ent["id"])


def _note_created(game, eid):
    """FULL_ENTITY 建实体 -> 归入最内层开局触发块 (块结束时生成开局触发汇总事件)"""
    for b in reversed(game["blocks"]):
        if "sog" in b:
            b["sog"]["created"].append(eid)
            break


def _note_deck_create(game):
    """FULL_ENTITY 建实体 -> 归入最内层 DECK_ACTION 块 (块结束时出实体变化摘要)"""
    for b in reversed(game["blocks"]):
        if "deck_act" in b:
            b["deck_act"]["created"] += 1
            break


def _finish_deck_action(game, blk):
    """DECK_ACTION 块结束 -> 手牌动作事件 (预备/囚禁类手牌操作, 通用按块内变化汇总):
    花费 = 行动方 RESOURCES_USED 块内增量; 减费数值渲染时按 ATTACHED 附魔推断, 判不出带块内摘要"""
    da = blk["deck_act"]
    pel = game["entities"].get(da["pel_id"]) if da["pel_id"] is not None else None
    if pel is not None and da["ctl"] is None:
        da["ctl"] = pel["controller"]
    if da["eid"] is None:
        return  # 块实体引用不到 id (罕见引用形态), 无法命名, 不生成噪音事件
    spent = None
    if pel is not None and da["pre_res"] >= 0:
        spent = max(_tag_int(pel, "RESOURCES_USED") - da["pre_res"], 0)
    summary = "；".join(f"{t} {o}→{v}" for t, o, v in da["tagsum"])
    if da["created"]:
        summary = (summary + "；" if summary else "") + f"创建实体 {da['created']} 个"
    _record(game, "deck_action", actor=da["ctl"], eid=da["eid"], spent=spent,
            prepare=da["prepare"], summary=summary)


def _deck_cost_cut(lookup, game, eid):
    """DECK_ACTION 减费推断: ATTACHED 到该实体的附魔, 卡文本含减费且带 TAG_SCRIPT_DATA_NUM_1
    数值 -> 累计减费; 原费取卡库 cost, 兜底 TAG_LAST_KNOWN_COST_IN_HAND; 判不出返回 None"""
    ent = game["entities"].get(eid)
    if not ent:
        return None
    cut = 0
    for e in game["entities"].values():
        if _ctype(e) != "ENCHANTMENT" or _tag_int(e, "ATTACHED") != eid:
            continue
        cid = e["cardId"] or e.get("seen_cardid") or ""  # 附魔用完即藏回, 曾见名兜底
        if not cid:
            continue
        text = (lookup.get(cid) or {}).get("text") or ""
        if "消耗减少" in text or "费用减少" in text or "费用降低" in text:
            n = _tag_int(e, "TAG_SCRIPT_DATA_NUM_1")
            if n > 0:
                cut += n
    if not cut:
        return None
    c = lookup.get(ent["cardId"]) if ent["cardId"] else None
    base = c.get("cost") if c else None
    if base is None:
        base = _tag_int(ent, "TAG_LAST_KNOWN_COST_IN_HAND") or None
    if base is None:
        return None
    now = max(base - cut, 0)
    return (base, now) if now < base else None


def _finish_sog(game, blk):
    """开局触发块结束 -> 汇总事件: 触发源 + 复制洗入牌库的张数/卡名 + 替换的英雄技能"""
    info = blk["sog"]
    src_id = _ref_id(info["src"] or "")
    src = game["entities"].get(src_id) if src_id is not None else None
    copies, power = [], None
    for eid in info["created"]:
        ent = game["entities"].get(eid)
        if not ent:
            continue
        if _ctype(ent) == "HERO_POWER":
            power = eid  # 开局替换技能 (穆格·兹伊类)
        elif ent["zone"] == "DECK":
            copies.append(eid)  # 复制洗入牌库 (霍格类)
    if src is None and not copies and power is None:
        return
    _record(game, "sog", turn=0,
            actor=src["controller"] if src and src["controller"] is not None else None,
            src=src_id, copies=copies, power=power)


def _handle_entity_choices(game, line):
    """对局中 GENERAL 选择列选项 (发现/灾变类): 记 id/玩家/Source/选项实体, 等 EntitiesChosen 确认。
    只认 ChoiceType=GENERAL, 换牌 MULLIGAN/战吼 TARGET 不进此状态, 不与开局处理重复"""
    m = EC_HEAD_RE.match(line)
    if m:
        game["ec_choice"] = ({"id": int(m.group(1)), "player": m.group(2).strip(),
                              "src": None, "options": []}
                             if m.group(3) == "GENERAL" else None)
        return
    ch = game.get("ec_choice")
    if ch is None:
        return
    body = line.split("GameState.DebugPrintEntityChoices() - ", 1)
    body = body[1] if len(body) == 2 else ""
    if body.startswith("Source="):
        ch["src"] = _ref_id(body[7:].strip())
        return
    bm = EC_BRACKET_RE.search(body)
    if bm:
        eid = _ref_id(bm.group(1))
        if eid is not None:
            ch["options"].append(eid)


def _handle_entities_chosen(game, line):
    """EntitiesChosen 双语义: 换牌期 = 保留选择 (kept, 原逻辑不动);
    对局中 = GENERAL 选择确认 -> 「选择：选项名」事件 (对方选择也走这里, 无 SendChoices 行)"""
    if not game["gate"] or game["grace"]:
        ce = CHOSEN_ENT_RE.match(line)
        if ce:
            pm = CHOSEN_PLAYER_RE.search(ce.group(1))
            idm = BRACKET_ID_RE.search(ce.group(1))
            if pm and idm:
                ctl, eid = int(pm.group(1)), int(idm.group(1))
                lst = game["opening"]["kept"].setdefault(ctl, [])
                if eid not in lst:
                    lst.append(eid)
        return
    cm = EC_CHOSEN_HEAD_RE.match(line)
    if cm:
        ch = game.get("ec_choice")
        game["ec_confirm"] = ({"player": cm.group(2).strip(), "total": _num(cm.group(3)) or 1,
                               "chosen": []}
                              if ch is not None and ch["id"] == int(cm.group(1)) else None)
        return
    cf = game.get("ec_confirm")
    if cf is None:
        return
    ce = CHOSEN_ENT_RE.match(line)
    if ce:
        idm = BRACKET_ID_RE.search(ce.group(1))
        eid = int(idm.group(1)) if idm else None
        if eid is not None and eid not in cf["chosen"]:
            cf["chosen"].append(eid)
        if len(cf["chosen"]) >= cf["total"]:
            _emit_choose(game, cf)
            game["ec_confirm"], game["ec_choice"] = None, None


def _emit_choose(game, cf):
    """GENERAL 选择确认 -> 选择事件 (选项实体留 id, 渲染时解名: 对方后来打出的发现牌也能对上名字)"""
    if not cf["chosen"]:
        return
    ctl = None
    peid = game["names"].get(cf["player"])
    pent = game["entities"].get(peid) if peid is not None else None
    if pent is not None and _ctype(pent) == "PLAYER":
        ctl = pent["controller"]
    _record(game, "choose", actor=ctl, eids=list(cf["chosen"]))


def _finish_deck_new(game, blk, ctx):
    """块结束 -> 块内生成直接进牌库的实体汇总事件 (洗入牌库 N 张（来源效果）, 卡名可见才带):
    灾变类选择/触发效果洗牌; 开局霍格式复制在 gate 开闸前, 仍由 sog 事件覆盖, 不重复"""
    if not game["gate"] or game["grace"]:
        return
    by_ctl = {}
    for eid in blk["deck_new"]:
        ent = game["entities"].get(eid)
        if ent is not None and ent["controller"] is not None:
            by_ctl.setdefault(ent["controller"], []).append(eid)
    for ctl, eids in by_ctl.items():
        _record(game, "shuffle", actor=ctl, eids=eids, count=len(eids),
                src_kind=ctx[0] if ctx else "effect", src_id=ctx[1] if ctx else None)


def _reveal_kind(game, info):
    """亮牌来源类别: 触发块_keyword 是亡语 (或来源实体已死亡) 判亡语, 否则普通触发"""
    sid = info.get("src_id")
    if info.get("kw") == "DEATHRATTLE" or (sid is not None and sid in game["dead_seen"]):
        return "deathrattle"
    return "trigger"


def _note_reveal(game, eid):
    """亡语块内 SHOW_ENTITY 揭示隐藏实体 (雷鸣流云亡语亮出吸收的法术): 挂观察等 CARDTYPE 定性
    (只有法术报亮牌), 块内 POWER/PLAY 块以它为源结算 = 已施放; 被藏回 (REVEALED 后 HIDE) =
    过场动画, 撤回。非亡语触发 (兆示自亮/阶段触发) 不挂, 避免噪音"""
    inner = next((b for b in reversed(game["blocks"]) if b["type"] == "TRIGGER"), None)
    if inner is None:
        return
    info = {"depth": len(game["blocks"]), "src_id": _ref_id(inner.get("entity") or ""),
            "kw": inner.get("kw"), "hidden": False, "ev": None}
    if _reveal_kind(game, info) != "deathrattle":
        return
    game["reveal_watch"][eid] = info


def _note_reveal_cardtype(game, ent, value):
    """揭示实体定性为法术 -> 立即出亮牌事件 (占位: 事件顺序紧跟死亡/触发, 施放标记后补)"""
    info = game["reveal_watch"].get(ent["id"])
    if (info is None or info.get("ev") is not None or info.get("hidden")
            or _norm(value, CTYPE_BY_NUM) != "SPELL" or ent["controller"] is None
            or not game["gate"] or game["grace"]):
        return
    info["ev"] = _record(game, "reveal", actor=ent["controller"], eid=ent["id"],
                         src_kind=_reveal_kind(game, info), src_id=info.get("src_id"), cast=False)


def _finalize_reveal(game, eid):
    """亮牌观察收尾 (所属块关闭): 被藏回的过场揭示撤回事件, 其余保留 (cast 标记已定)"""
    info = game["reveal_watch"].pop(eid)
    ev = info.get("ev")
    if info.get("hidden"):
        if ev is not None and ev in game["events"]:
            game["events"].remove(ev)
        return
    if ev is not None:
        return
    ent = game["entities"].get(eid)
    if (ent is None or _ctype(ent) != "SPELL" or ent["controller"] is None
            or not game["gate"] or game["grace"]):
        return
    _record(game, "reveal", actor=ent["controller"], eid=eid,
            src_kind=_reveal_kind(game, info), src_id=info.get("src_id"), cast=False)


def _stat_snap(game, eid):
    """随从当前攻血快照 (当前血=HEALTH-DAMAGE, 炉石受伤走 DAMAGE 累积); 非随从/取不到返回 None"""
    ent = game["entities"].get(eid) if eid is not None else None
    if not ent or _ctype(ent) != "MINION":
        return None
    atk = _tag_int(ent, "ATK")
    hp = (_tag_int(ent, "HEALTH") or ent["peak_hp"]) - _tag_int(ent, "DAMAGE")
    return atk, max(hp, 0)


def _track_settle(game, ev):
    """出牌/召唤事件的攻血快照等结算后回填: 记下事件与所处块深度, BLOCK_END 时刷新为稳定值"""
    if ev.get("stat") is not None:
        game["pending_stats"].append({"ev": ev, "depth": len(game["blocks"])})


def _settle_stats(game):
    """块结束 -> 把本块(含子块)内记录的出牌/召唤攻血刷新为当前实体属性。

    战吼在出牌块的嵌套 POWER 子块里结算 (如暮光幼龙 +血), 记录时刻的快照是打出前旧值;
    块关闭时回填即"结算稳定后的面板值", 块外后续回合的 buff 不受影响。
    key/eid 可覆盖回填字段与实体 (复生回填死亡事件、反解攻击目标回填 tgt_stat)。"""
    depth = len(game["blocks"])
    keep = []
    for it in game["pending_stats"]:
        if it["depth"] >= depth:
            it["ev"][it.get("key", "stat")] = _stat_snap(game, it.get("eid") or it["ev"].get("eid"))
        else:
            keep.append(it)
    game["pending_stats"] = keep
    # 已关闭块内的治疗/光环下调记录过期, 防跨块误配
    game["heal_watch"] = [x for x in game["heal_watch"] if x["depth"] < depth]
    game["aura_drops"] = [x for x in game["aura_drops"] if x["depth"] < depth]


def _summon_ctx(game):
    """块栈 -> (来源类别, 来源实体id): 内层优先。TRIGGER 块按 TriggerKeyword/死者判亡语, 否则触发效果;
    无触发块时找 PLAY 块 (块实体是随从=战吼)。类别: deathrattle/battlecry/trigger/effect"""
    dead = game["dead_seen"]
    for b in reversed(game["blocks"]):
        if b["type"] == "TRIGGER":
            sid = _ref_id(b.get("entity") or "")
            kind = "deathrattle" if (b.get("kw") == "DEATHRATTLE" or (sid is not None and sid in dead)) else "trigger"
            return kind, sid
    for b in reversed(game["blocks"]):
        if b["type"] == "PLAY":
            sid = _ref_id(b.get("entity") or "")
            sent = game["entities"].get(sid)
            return ("battlecry" if sent is not None and _ctype(sent) == "MINION" else "effect"), sid
    if game["blocks"]:
        b = game["blocks"][-1]
        return "effect", _ref_id(b.get("entity") or "")
    return "effect", None


def _power_blk_id(game):
    """块栈内层往外第一个 POWER 块且块实体是英雄技能 -> 技能实体id; 中途遇 TRIGGER 或非技能块返回 None
    (技能块内触发的效果不算技能自带, 护甲照常单独发事件)"""
    for b in reversed(game["blocks"]):
        if b["type"] == "TRIGGER":
            return None
        if b["type"] == "POWER":
            pid = _ref_id(b.get("entity") or "")
            p = game["entities"].get(pid)
            return pid if p is not None and _ctype(p) == "HERO_POWER" else None
    return None


def _attach_power_armor(game, pid, gain, hero_id):
    """英雄技能块内的护甲变化不单独发事件: 优先回填本块刚生成的技能事件, 否则挂起等技能事件生成时内联"""
    for ev in reversed(game["events"][-4:]):
        if ev["type"] in ("power", "power_change") and ev.get("eid") == pid:
            ev["armor"] = (ev.get("armor") or 0) + gain
            return
    p = game["hp_armor_pend"].get(pid)
    game["hp_armor_pend"][pid] = [gain, hero_id] if not p else [p[0] + gain, hero_id]


def _heal_src(game):
    """治疗来源块上下文: 内层优先 触发块=触发源, 英雄技能块=技能, 其他 POWER 块=块实体;
    判不出返回 (None, None)"""
    for b in reversed(game["blocks"]):
        if b["type"] == "TRIGGER":
            sid = _ref_id(b.get("entity") or "")
            s = game["entities"].get(sid)
            if s is not None and _ctype(s) != "PLAYER":  # 疲劳/阶段触发(玩家实体)不算来源
                return "trigger", sid
            continue
        if b["type"] == "POWER":
            pid = _ref_id(b.get("entity") or "")
            if pid is None:
                return None, None
            p = game["entities"].get(pid)
            return ("heropower" if p is not None and _ctype(p) == "HERO_POWER" else "power"), pid
    return None, None


def _note_aura_drop(game, ent, old_v, new_v, depth):
    """块内 HEALTH 下调 = 疑似光环移除: 同块刚记的治疗事件改判为光环回调, 否则留档供紧随的
    DAMAGE 回调识别 (有效生命没变, 不应显示为治疗)"""
    for it in reversed(game["heal_watch"]):
        if it["eid"] == ent["id"] and it["depth"] >= depth:
            game["heal_watch"].remove(it)
            it["ev"]["type"] = "aura_cb"
            it["ev"]["vals"] = (old_v, new_v)
            for k in ("hp", "src_kind", "src_id"):
                it["ev"].pop(k, None)
            return
    game["aura_drops"].append({"eid": ent["id"], "vals": (old_v, new_v), "depth": depth})


def _note_bc_target(game, ent):
    """PLAY 块(战吼)内对其他实体的影响类 tag (DAMAGE/SILENCED/控制类) -> 记为打出目标,
    事件行渲染时追加 「→ 目标」。触发块内的变化 (亡语/吸血等) 与块内新创建的实体
    (复生/附属物自带 DAMAGE 数据) 不算战吼指向"""
    for b in reversed(game["blocks"]):
        if b["type"] == "TRIGGER":
            return
        if b["type"] == "PLAY":
            pid = _ref_id(b.get("entity") or "")
            pev = b.get("play_ev")
            if pev is None or pid is None or pid == ent["id"] or ent["id"] not in b.get("pre_ids", ()):
                return  # 内层 PLAY 块不匹配 (嵌套出牌) 不再外溯
            p = game["entities"].get(pid)
            if p is not None and _ctype(p) == "MINION" and pev.get("ctype") == "MINION":
                lst = pev.setdefault("bc_tgts", [])
                if ent["id"] not in lst:
                    lst.append(ent["id"])
            return


# 战吼目标标注认的影响类 tag: 伤害/沉默/冻结/消灭/控制
BC_TARGET_TAGS = {"DAMAGE", "SILENCED", "FROZEN", "DESTROY", "CONTROLLER"}


def _src_label(lookup, game, ev):
    """事件来源类别+实体 -> 来源描述 (亡语/战吼/复生/<名>效果); 判不出返回 None"""
    kind, sid = ev.get("src_kind"), ev.get("src_id")
    if kind == "reborn":
        return "复生"
    if kind == "deathrattle" and sid is None:
        return "亡语"
    nm = _ev_name(lookup, game, sid, "") if sid is not None else ""
    if kind == "deathrattle":
        return f"{_cn(nm)}亡语" if nm else "亡语"
    if kind == "battlecry":
        return f"{_cn(nm)}战吼" if nm else "战吼"
    return f"{_cn(nm)}效果" if nm else None


def _reborn_backfill(game, dev, eid):
    """复生回填: 死亡事件追加复生标记, 块结束时回填复活实体的攻血快照"""
    if dev is None or dev.get("reborn"):
        return
    dev["reborn"] = True
    game["pending_stats"].append({"ev": dev, "depth": len(game["blocks"]), "key": "reborn_stat", "eid": eid})


def _flush_summon(game):
    """实体 tag 段结束 (下一 packet 到来) -> 给延迟定性的效果召唤出事件"""
    ids = game["pending_summon"]
    if not ids:
        return
    game["pending_summon"] = []
    for eid in ids:
        ent = game["entities"].get(eid)
        if ent is not None:
            _record_effect_summon(game, ent)


def _record_effect_summon(game, ent):
    """块内 FULL_ENTITY 直接建在场面的召唤 -> 效果召唤事件 (亡语/战吼/触发/复生来源)。

    同回合同来源的召唤合并为一条 (×N) 防食尸鬼潮刷屏; 复生召唤优先回填死者死亡事件。"""
    ctl = ent["controller"]
    if ctl is None:
        return
    ent["entered_turn"] = game["turn"]
    if _tag_int(ent, "HAS_BEEN_REBORN") == 1:
        kind, sid = "reborn", None
    elif game["blocks"]:
        kind, sid = _summon_ctx(game)
    else:
        return  # 块外无上下文的直接铺场判不出来源, 不生成 (降级安全)
    if kind == "reborn":
        dev = game["death_evs"].get(_tag_int(ent, "CREATOR") or -1)
        if dev is not None:
            _reborn_backfill(game, dev, ent["id"])
            return
    cid = ent["cardId"] or ent["name"]
    if _tag_int(ent, "COLOSSAL_LIMB") == 1:  # 巨型附属物 t1/t2/t3 按本体归并, 渲染时用实际卡名 (如 黑血黏质 ×3)
        cid = re.sub(r"t\d+$", "", cid)
    key = (game["turn"], ctl, kind, sid, cid)  # 仅同名召唤合并
    acc = game["summon_acc"].get(key)
    if acc is not None:
        acc["count"] = (acc.get("count") or 1) + 1
        game["pending_stats"].append({"ev": acc, "depth": len(game["blocks"]), "key": "stat", "eid": ent["id"]})
        return
    ev = _record(game, "summon", actor=ctl, eid=ent["id"], stat=_stat_snap(game, ent["id"]),
                 src_kind=kind, src_id=sid, count=1)
    game["pending_stats"].append({"ev": ev, "depth": len(game["blocks"]), "key": "stat", "eid": ent["id"]})
    game["summon_acc"][key] = ev


def _record_gain_src(game, ent):
    """块内直接建在手牌的获得 -> 带来源获得事件:
    TRIGGER 块 = 触发效果 (逐月幼龙类回合结束送牌);
    PLAY 块且块实体是随从 = 战吼生成进手牌 (对方手牌否则无中生有)"""
    if ent["controller"] is None or not game["blocks"]:
        return
    for b in reversed(game["blocks"]):
        if b["type"] != "TRIGGER":
            continue
        sid = _ref_id(b.get("entity") or "")
        sent = game["entities"].get(sid)
        if sent is not None and _ctype(sent) != "PLAYER":  # 疲劳/阶段类触发(玩家实体)不算来源
            _record(game, "gain_src", actor=ent["controller"], eid=ent["id"], src_kind="trigger", src_id=sid)
        return
    for b in reversed(game["blocks"]):  # 战吼: 内层 PLAY 块实体是随从 -> 归该随从战吼 (法术块不归)
        if b["type"] != "PLAY":
            continue
        sid = _ref_id(b.get("entity") or "")
        sent = game["entities"].get(sid)
        if sent is not None and _ctype(sent) == "MINION":
            _record(game, "gain_src", actor=ent["controller"], eid=ent["id"], src_kind="battlecry", src_id=sid)
        return


def _watch_tag(game, ent, tag, value, old_zone, old_exh):
    """重放时同步收集行动事件; 换牌结束 (MULLIGAN_STATE=DONE) 前的初始铺场/换牌一律不记"""
    if tag == "TURN" and (_ctype(ent) == "GAME" or ent["id"] == 1):
        # 回合切换前兜底: 技能块护甲没等到技能事件 (日志顺序异常) 就补发独立护甲事件, 不丢数值
        for gain, hero_id in game["hp_armor_pend"].values():
            if gain and hero_id:
                _record(game, "armor", eid=hero_id, gain=gain)
        game["hp_armor_pend"].clear()
        n = _num(value)
        if n is not None:
            game["turn"] = n
        game["grace"] = False  # TURN 变化兜底结束抽牌宽限期
        game["summon_acc"].clear()  # 召唤合并按回合分桶, 跨回合不再合并
        return
    if tag == "STEP" and _ctype(ent) == "GAME" and str(value) == "MAIN_READY":
        # 首个正式回合开始 (MAIN_READY), 宽限期结束, 之后的 DECK->HAND 都是正式抽牌
        game["grace"] = False
        return
    if tag == "MULLIGAN_STATE" and _norm(value, MULLIGAN_BY_NUM) == "DONE":
        # 唯一开闸点: 任一方换牌 DONE = 换牌流程结束, 之后才是正式对局行动 (只开闸一次)
        if not game["gate"]:
            game["gate"] = True
            game["grace"] = True  # 开闸宽限: 引擎才落盘的换牌塞回/新抽移动不算正式抽牌
            # 行动方 = 先手方 (FIRST_PLAYER tag 的真实回合归属): 第 1 回合起手期间日志不再发
            # CURRENT_PLAYER=1, 若按 DONE 主体归属会被后完成换牌的后手方错误覆盖
            fp = next((p for p in _players(game) if _tag_int(p, "FIRST_PLAYER") == 1), None)
            if fp is not None:
                game["actor"] = fp["controller"] if fp["controller"] is not None else (_tag_int(fp, "PLAYER_ID") or None)
            if game["actor"] is None:  # 兜底: 日志无 FIRST_PLAYER 时按首个 DONE (先手先完成换牌)
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
        # 块起点已出过技能事件 (power_ev) 则复用, 防止重复; 无块上下文 (EXHAUSTED 迟到) 才补发
        pev = next((b["power_ev"] for b in reversed(game["blocks"])
                    if "power_ev" in b and b["power_ev"].get("eid") == ent["id"]), None)
        if pev is None:
            pev = _record(game, "power", eid=ent["id"])
        pend = game["hp_armor_pend"].pop(ent["id"], None)
        if pend:  # 技能块内攒下的护甲内联到技能行 (如 全副武装！（+2 护甲）)
            pev["armor"] = pend[0]


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
        match_blk = top if (top and top["type"] == "PLAY"
                            and _ref_id(top["entity"] or "") == ent["id"]) else None
        if match_blk:
            target = match_blk["target"]  # 出牌块的 Target 即法术/武器指向
        ev = _record(game, "play", eid=ent["id"], ctype=ct, target_ref=target,
                     stat=_stat_snap(game, ent["id"]),
                     forge_pid=_tag_int(ent, "DISPLAY_ENTITY_ON_MOUSEOVER") or None)
        # 兆示 (Forge) 预览指针打牌瞬间快照: 引擎随后立刻把它清 0, 最终态查不到
        if match_blk:
            match_blk["play_ev"] = ev  # 战吼对其他实体的影响 tag 回填到此事件 (→ 目标)
            match_blk["pre_ids"] = {e["id"] for e in game["entities"].values() if e["zone"] == "PLAY"}
        ent["entered_turn"] = game["turn"]  # 上场回合号, 供 [本回合上场] 与当前回合对账
        _track_settle(game, ev)
    elif old == "PLAY" and new == "HAND" and ct in PLAYABLE_TYPES:
        _record(game, "bounce", eid=ent["id"])  # 被移回手牌 (对方亡语/法术效果), 不记则场面凭空少人
    elif old == "HAND" and new == "SECRET":
        # 奥秘/任务打出: 任务名进度公开(方括号引用带内联名), 真奥秘隐藏(UNKNOWN) 触发才揭示;
        # 打出时刻快照可见性与名字 (任务链变形会重建实体, 最终态查不到打出的名字)
        nm = ent.get("name") or ""
        known = bool(ent["cardId"]) or bool(nm and not nm.startswith("UNKNOWN"))
        _record(game, "secret_play", eid=ent["id"], known=known,
                name=nm if nm and not nm.startswith("UNKNOWN") else "", card_id=ent["cardId"] or "")
    elif old == "SETASIDE" and new == "PLAY":
        if ct == "MINION":
            ev = _record(game, "summon", eid=ent["id"], stat=_stat_snap(game, ent["id"]))
            ent["entered_turn"] = game["turn"]
            _track_settle(game, ev)
        elif ct == "WEAPON":
            _record(game, "equip", eid=ent["id"])
    elif old == "GRAVEYARD" and new == "PLAY" and ct == "MINION":
        # 同实体复活 (少见形态): 回填死亡事件复生标记
        _reborn_backfill(game, game["death_evs"].get(ent["id"]), ent["id"])
    elif new == "GRAVEYARD":
        if old == "SECRET":
            # 触发时刻已在 SHOW_ENTITY 揭示时出过事件 (secret_trig), 这里只兜底没走揭示路径的
            # (被效果直接清掉进坟场等), 保持原口径
            if ent["id"] not in game["secret_trig"]:
                _record(game, "secret_trigger", eid=ent["id"])  # 奥秘触发后进坟场, cardId 此时已揭示
        elif old == "PLAY" and ct in ("MINION", "HERO", "WEAPON", "LOCATION"):
            ev = _record(game, "death", eid=ent["id"], stat=_stat_snap(game, ent["id"]))  # 法术/技能结算进坟场不算死亡
            game["dead_seen"].add(ent["id"])  # 亡语块判定 + 复生回填锚点
            game["death_evs"][ent["id"]] = ev
        elif old == "HAND":
            _record(game, "discard", eid=ent["id"])
    elif old == "SETASIDE" and new == "HAND":
        if game["gate"] and not game["grace"] and any(b["type"] == "TRIGGER" for b in game["blocks"]):
            _record_gain_src(game, ent)  # 触发效果送入手牌 -> 带来源获得
        else:
            _record(game, "gain", eid=ent["id"])


# 只有 Player 实体才有的 tag (新版日志对手真名经 TAG_CHANGE Entity=<名字> 揭晓, 用于归并)
PLAYER_ONLY_TAGS = {"PLAYSTATE", "CURRENT_PLAYER", "MULLIGAN_STATE", "HERO_ENTITY", "TIMEOUT",
                    "PLAYER_ID", "MAXHANDSIZE", "STARTHANDSIZE", "TEAM_ID", "MAXRESOURCES",
                    "FIRST_PLAYER", "FATIGUE", "LAST_MSG_PLAYED"}


def _handle_packet(game, payload):
    """处理一条 packet 行 (CREATE_GAME 由外层处理); 顶格与块内缩进共用"""
    _flush_summon(game)  # 新 packet = 上一个 FULL_ENTITY 的 tag 段已结束
    if game.get("burn_pending"):
        # 手牌满烧牌: META_DATA Meta=BURNED_CARD 的下一行 Info[0] 指向被烧实体 (卡名已公开)
        game["burn_pending"] = False
        m = INFO_LINE_RE.match(payload)
        if m:
            ref = f"[{m.group(1)}]" if m.group(1) else (m.group(2) or "")
            eid = _ref_id(ref)
            ent = game["entities"].get(eid)
            if ent is not None and game["gate"] and not game["grace"]:
                _record(game, "burn", actor=ent["controller"], eid=eid)
            return
    m = META_BURN_RE.match(payload)
    if m:
        game["burn_pending"] = True
        return
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
        eid = int(m.group(1))
        game["cur"] = _create_entity_by_id(game, eid, m.group(2))
        _note_created(game, eid)
        _note_deck_create(game)
        return
    m = FULL_ENTITY_RE.match(payload)
    if m:
        eid = _create_entity(game, m.group(1), m.group(2))
        if eid is not None:
            _note_created(game, eid)
        _note_deck_create(game)
        game["cur"] = eid
        return
    m = SHOW_ENTITY_RE.match(payload)
    if m:
        eid = _resolve(game, m.group(1))
        ent = game["entities"].get(eid)
        if ent:
            revealed_new = not ent["cardId"] and m.group(2)
            ent["cardId"] = m.group(2)
            # 隐藏奥秘被揭示 = 奥秘触发时刻 (揭示紧跟触发块/触发效果之前)。原先等 SECRET->GRAVEYARD
            # 迁移才补发, 但迁移在触发块收尾, 事件会排到触发效果 (如冰冻陷阱回手) 之后, 次序颠倒
            if (revealed_new and game["gate"] and not game["grace"]
                    and ent["zone"] == "SECRET" and eid not in game["secret_trig"]):
                game["secret_trig"].add(eid)
                _record(game, "secret_trigger", eid=eid)
            # 亡语/触发块内首次揭示隐藏实体 (雷鸣流云亡语亮出吸收的法术): 挂观察等定性
            if (revealed_new and game["gate"] and not game["grace"]
                    and any(b["type"] == "TRIGGER" for b in game["blocks"])):
                _note_reveal(game, eid)
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
            if ent["cardId"]:
                ent["seen_cardid"] = ent["cardId"]  # 曾揭示后又被藏回 (开局触发源换回牌库), 留名兜底
            ent["cardId"] = ""
        info = game["reveal_watch"].get(eid)
        if info is not None:
            info["hidden"] = True  # 亮出随即藏回 = 过场动画 (野兽绊索洗牌), 收尾时撤回
        if m.group(2):
            _apply_tag(game, eid, m.group(2), m.group(3) or "", via_change=True)
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
        _apply_tag(game, eid, tag, val, via_change=True)
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
        kw_m = TRIGGER_KW_RE.search(payload)
        blk = {"type": btype, "entity": ent_m.group(1) if ent_m else None,
               "target": tgt_m.group(1) if tgt_m else None,
               "kw": kw_m.group(1) if kw_m else None}
        game["blk_seq"] += 1
        blk["seq"] = game["blk_seq"]
        rid = _ref_id(blk["entity"] or "") if blk["entity"] else None
        if rid is not None and btype in ("POWER", "PLAY", "TRIGGER") and rid in game["reveal_watch"]:
            info = game["reveal_watch"][rid]
            if info.get("ev") is not None:
                # 亮出的牌有以它为源的结算块 = 已施放 (POWER=直接结算, TRIGGER=施放时施法 CASTS_WHEN_DRAWN)
                info["ev"]["cast"] = True
        if btype in ("PLAY", "POWER") and rid is not None and not any("power_ev" in b for b in game["blocks"]):
            # 英雄技能启用: 块起点即按键时刻, 立即出事件。原先等块尾 EXHAUSTED=1 才补发,
            # 技能块内触发的效果 (甲龙攻击/灌注获得) 会排到技能前面, 次序颠倒
            pent = game["entities"].get(rid)
            if pent is not None and _ctype(pent) == "HERO_POWER" and pent["zone"] == "PLAY":
                ev = _record(game, "power", eid=rid)
                blk["power_ev"] = ev
                pend = game["hp_armor_pend"].pop(rid, None)
                if pend:
                    ev["armor"] = pend[0]
        if btype == "TRIGGER" and blk["kw"] == "START_OF_GAME_KEYWORD":
            blk["sog"] = {"src": blk["entity"], "created": []}  # 开局触发: 块结束出汇总事件
        if btype == "DECK_ACTION" and game["gate"] and not game["grace"]:
            # 手牌预备动作块 (预备/囚禁类手牌操作): 记块实体与行动方, 块结束出事件
            bid = _ref_id(blk["entity"] or "")
            bent = game["entities"].get(bid) if bid is not None else None
            ctl = bent["controller"] if bent is not None else None
            pel = next((p for p in _players(game) if p["controller"] == ctl), None) if ctl is not None else None
            blk["deck_act"] = {"eid": bid, "ctl": ctl,
                               "pre_res": _tag_int(pel, "RESOURCES_USED", -1) if pel is not None else -1,
                               "pel_id": pel["id"] if pel is not None else None,
                               "prepare": False, "created": 0, "tagsum": []}
        game["blocks"].append(blk)
        if btype == "ATTACK" and game["gate"]:
            atk_ref = ent_m.group(1) if ent_m else ""
            tgt_ref = tgt_m.group(1) if tgt_m else ""
            ev = _record(game, "attack", atk_ref=atk_ref, tgt_ref=tgt_ref,
                         atk_stat=_stat_snap(game, _ref_id(atk_ref)),
                         tgt_stat=_stat_snap(game, _ref_id(tgt_ref)))
            blk["atk_ev"] = ev  # Target=0 的脚本攻击: 块内 PROPOSED_DEFENDER/受击伤害反解目标
        return
    if payload[:9] == "BLOCK_END" and game["blocks"]:
        dn_ctx = _summon_ctx(game) if "deck_new" in game["blocks"][-1] else None
        blk = game["blocks"].pop()
        game["cur"] = None
        if blk["type"] == "ATTACK":
            ev = blk.get("atk_ev")
            dt = blk.get("dmg_tgt")
            if ev is not None and dt is not None and ev.get("tgt_ref") in (None, "0", "-1"):
                ev["tgt_ref"] = str(dt)
                game["pending_stats"].append({"ev": ev, "depth": len(game["blocks"]), "key": "tgt_stat", "eid": dt})
        if "deck_act" in blk:
            _finish_deck_action(game, blk)
        _settle_stats(game)
        if "sog" in blk:
            _finish_sog(game, blk)
        if blk.get("deck_new"):
            _finish_deck_new(game, blk, dn_ctx)
        for k in [e for e, v in game["reveal_watch"].items() if v["depth"] > len(game["blocks"])]:
            _finalize_reveal(game, k)  # 归属块已关闭: 藏回的撤回, 存活的亮牌事件定稿
        return
    # META_DATA 等: 最终状态重放不需要, 忽略


def parse_power_log(lines):
    """从最后一个 CREATE_GAME 起重放 packet 流 -> (game|None, 起始行, 总行数)"""
    game, start_line, total = None, 0, 0
    for lineno, raw in enumerate(lines, 1):
        total = lineno
        # 首行 \ufeff 前缀 (带 BOM 文件/PowerShell 管道喂 stdin) 会让 ^D 锚点的
        # CREATE_GAME/元信息正则失配, 致 --log 与 --stdin 解析结果不一致, 统一剥掉
        line = raw.lstrip("\ufeff").rstrip("\r\n")
        tm = TRUNCATE_RE.search(line)  # 日志超限截断标记 (裸行): 出现在最后一个 CREATE_GAME 之后才算本局截断
        if tm:
            if game is not None:
                game["truncated"], game["trunc_kb"] = True, _num(tm.group(1))
            continue
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
        mm = GAME_META_RE.match(line)  # DebugPrintGame 元信息行: 对局类型/赛制/构建号, 每局一份
        if mm:
            if game is not None:
                game["meta"][mm.group(1)] = mm.group(2)
            continue
        if "GameState.DebugPrintEntityChoices() -" in line:
            # 对局中 GENERAL 选择列选项 (发现/灾变类): 记选项, 确认行到来出事件
            if game is not None:
                _handle_entity_choices(game, line)
            continue
        if "GameState.DebugPrintEntitiesChosen() -" in line:
            # 换牌期 = 保留选择 (后手的选择可能晚于先手 DONE 开闸, 故宽限到 grace 结束);
            # 对局中 = GENERAL 选择确认 -> 「选择：选项名」事件
            if game is not None:
                _handle_entities_chosen(game, line)
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
    if game is not None:
        _flush_summon(game)  # 日志截尾时兜底冲刷
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


def _is_quest_ent(ent):
    """任务类实体: SECRET 区里带 QUEST/SIDEQUEST tag 的 (任务链二段组件同样带 QUEST tag)"""
    return _tag_int(ent, "QUEST") == 1 or _tag_int(ent, "SIDEQUEST") == 1


def _hero_dead(game, ctl):
    """该方英雄是否阵亡: 在场英雄伤害>=30 或血量归零; 或已无在场英雄且有英雄实体进坟场
    (英雄牌替换时旧英雄也会离开场面, 但会有新英雄顶上, 不能只看坟场)"""
    heroes = [e for e in game["entities"].values() if e["controller"] == ctl and _ctype(e) == "HERO"]
    if not heroes:
        return False
    live = [h for h in heroes if h["zone"] == "PLAY"]
    for h in live:
        base = _tag_int(h, "HEALTH") or h["peak_hp"]
        if _tag_int(h, "DAMAGE") >= 30 or (base > 0 and base - _tag_int(h, "DAMAGE") <= 0):
            return True
    return not live and any(h["zone"] == "GRAVEYARD" for h in heroes)


def _endgame_line(game, me, opp):
    """终局结果行: 我方/对方视角的胜负 + 结束方式 (投降 > 斩杀 > 疲劳), 判不出方式只报胜负"""
    players = _players(game)
    states = {p["id"]: _playstate(p) for p in players}
    if any(s == "TIED" for s in states.values()):
        return "对局结束：平局"
    winner = next((p for p in players if states.get(p["id"]) == "WON"), None)
    my_state = states.get(me["id"], "") if me else ""
    opp_state = states.get(opp["id"], "") if opp else ""
    res = None
    if me:
        if my_state == "WON" or (my_state not in ("LOST", "CONCEDED") and opp_state in ("LOST", "CONCEDED")):
            res = "我方胜利"
        elif my_state in ("LOST", "CONCEDED") or opp_state == "WON":
            res = "我方失败"
    conceded = next((p for p in players if p.get("conceded")), None)
    reason = ""
    if conceded is not None:
        if me and conceded is me:
            reason = "我方投降"
        elif me and conceded is opp:
            reason = "对方投降"
        else:
            reason = f"{_player_name(conceded)} 投降"
    else:
        opp_dead = opp is not None and _hero_dead(game, opp["controller"])
        me_dead = me is not None and _hero_dead(game, me["controller"])
        if opp_dead and not me_dead:
            reason = "斩杀，对方英雄阵亡"
        elif me_dead and not opp_dead:
            reason = "我方英雄阵亡"
        elif me_dead and opp_dead:
            reason = "双方英雄阵亡"
        elif any(_tag_int(p, "FATIGUE") > 0 for p in players):
            reason = "疲劳"
    head = res or (f"{_player_name(winner)} 获胜" if winner else "")
    return ("对局结束：" + head if head else "对局结束") + (f"（{reason}）" if reason else "")


def _order_labels(players):
    """先手/后手标注: FIRST_PLAYER=1 先手, 其余一方为后手(带硬币); 日志缺 tag 则不标"""
    first = next((p for p in players if _tag_int(p, "FIRST_PLAYER") == 1), None)
    out = {}
    if first is not None:
        out[first["id"]] = "[先手]"
        for p in players:
            if p is not first:
                out[p["id"]] = "[后手+硬币]"
    return out


def _secret_count(game, ctl):
    """真奥秘数: SECRET 区实体去掉任务类 (任务会被引擎放进 SECRET 区, 计入会误导)"""
    return len([e for e in _in_zone(game, ctl, "SECRET") if not _is_quest_ent(e)])


def _quest_lines(lookup, game, ctl):
    """任务槽: 该方 SECRET 区任务类实体逐行显示名称与进度 (QUEST_PROGRESS/QUEST_PROGRESS_TOTAL),
    完成判定 = QUEST_COMPLETED tag 或 progress>=total; 无任务返回空列表"""
    rows = []
    quests = sorted((e for e in _in_zone(game, ctl, "SECRET") if _is_quest_ent(e)),
                    key=lambda e: (e["zone_pos"], e["id"]))
    for q in quests:
        name = _card_name(lookup, q["cardId"], q)
        prog, total = _tag_int(q, "QUEST_PROGRESS"), _tag_int(q, "QUEST_PROGRESS_TOTAL")
        done = _tag_int(q, "QUEST_COMPLETED") == 1 or (total > 0 and prog >= total)
        prog_txt = f"{prog}/{total}" if total else (f"{prog}" if prog else "")
        rows.append(f"任务{_cn(name)}{prog_txt}（已完成）" if done else f"任务{_cn(name)}{prog_txt}")
    return rows


def _quest_reward_hint(lookup, game, ev):
    """任务完成事件后追加奖励入手提示: 奖励实体通常是完成块内新建并进 HAND 的卡,
    带 CREATOR/DISPLAYED_CREATOR=任务实体 tag, 按 creator 反查手牌; 兜底看紧邻的己方获得事件"""
    qid = ev.get("eid")
    if qid is None:
        return ""
    cands = [e for e in game["entities"].values()
             if e["zone"] == "HAND" and qid in (_tag_int(e, "CREATOR"), _tag_int(e, "DISPLAYED_CREATOR"))]
    if cands:
        nm = _ev_name(lookup, game, min(cands, key=lambda e: e["id"])["id"])
        if nm:
            return f"（奖励 {_cn(nm)} 已入手）"
        return "（奖励已入手）"
    evs = game["events"]
    idx = next((i for i, e in enumerate(evs) if e is ev), None)
    if idx is not None:
        for j in range(idx - 1, max(idx - 4, -1), -1):
            prev = evs[j]
            if (prev["type"] == "gain" and prev.get("actor") == ev.get("actor")
                    and prev.get("turn") == ev.get("turn")):
                return "（奖励已入手）"
    return ""


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


_lower_idx_cache = None  # (lookup 对象, 小写 cardId 索引) 惰性缓存, 变体兜底用


def _db_card(lookup, card_id):
    """cardId -> 卡库条目 多级兜底: 精确 -> 全表小写 (大小写漂移) -> 皮肤/钻石/异画变体键
    去后缀逐级回退 (END_030t2 -> END_030t -> END_030); 全部落空返回 None"""
    if not card_id or "UNKNOWN ENTITY" in card_id:
        return None
    c = lookup.get(card_id)
    if c is not None:
        return c
    global _lower_idx_cache
    if _lower_idx_cache is None or _lower_idx_cache[0] is not lookup:
        _lower_idx_cache = (lookup, {str(k).lower(): v for k, v in lookup.items() if k})
    low = card_id.lower()
    idx = _lower_idx_cache[1]
    c = idx.get(low)
    if c is not None:
        return c
    cid = low
    while len(cid) > 5:  # 变体后缀 (t2/e/h 等) 逐级剥掉重试, 命中最近的基础卡; 留长度下限防误配短键
        cid = cid[:-1]
        c = lookup.get(cid) or idx.get(cid)
        if c is not None:
            return c
    return None


def _card_name(lookup, card_id, ent=None):
    if not card_id:
        return "未知卡牌" if not (ent and ent.get("name")) else ent["name"]
    if "UNKNOWN ENTITY" in card_id:  # 新版日志 token 占位文本, 不是真实 cardId
        return "未知随从"
    c = _db_card(lookup, card_id)
    if c and c.get("name"):
        return c["name"]
    if card_id in HERO_POWER_NAMES:
        return HERO_POWER_NAMES[card_id]
    if ent and ent.get("name"):  # 日志方括号引用里的本地化名 (新卡/token 不在卡牌库时的兜底)
        return ent["name"]
    return card_id


def _cn(name):
    """卡名统一「」包裹 (防卡名自带标点与数值/分隔符粘连; 空名原样返回)"""
    return f"「{name}」" if name else name


def _j(a, b):
    """两段拼接: 紧邻「」名字或前段以冒号结尾则无缝, 否则补空格 (称谓/普通词间保留空格); b 空返回 a"""
    if not b:
        return a
    sep = "" if (a.endswith("」") or a.endswith("：") or b.startswith("「")) else " "
    return f"{a}{sep}{b}"


def _plain_text(html):
    """卡牌 HTML 描述 -> 纯文本 (去标签 + 取 @ 升级段第一段 + 占位符转X + 压缩空白)"""
    text = (html or "").replace("<b>@</b>", "X")  # 段内动态数值占位 (@ 包在标签里, 区别于段分隔符)
    if "兆示" in text:  # 兆示卡多级 text 用 "</i>1" 裸数字拼接各等级 (无 @ 分隔), 只取基础级避免重复句堆叠
        text = re.split(r"</i>1(?![0-9])", text)[0]
    text = text.split("@")[0]  # 多阶段升级卡 text 用裸 @ 拼接多份, 取第一段
    text = re.sub(r"\{\d+\}", "X", text)  # {0} 等动态数值占位符
    text = re.sub(r"\$[a-zA-Z](?=\d)", "", text)  # $d2 等变量标记 ($字母+数字)
    text = text.replace("$", "").replace("#", "")  # $/# 数值高亮标记, 游戏内渲染不显示
    return " ".join(re.sub(r"<[^>]+>", "", text).split())


def _ev_desc(lookup, game, eid, card_id=None):
    """事件实体的卡牌效果描述 (去 HTML 全文, 不截断——每张牌全对局只挂一次, 长一点换来决策质量);
    查不到/无描述返回空; card_id 显式传入时优先 (实体可能被任务链变形重建)"""
    ent = game["entities"].get(eid) if eid is not None else None
    cid = card_id or (ent["cardId"] if ent and ent["cardId"] else "")
    c = _db_card(lookup, cid) if cid else None
    return _plain_text((c or {}).get("text") or "")


def _hero_ent(game, controller):
    """该方当前英雄实体: 玩家实体 HERO_ENTITY 指向优先 (变形后指向新英雄), 退场面英雄,
    再退该方任意离场英雄 (终局斩杀后英雄进坟场/英雄牌变形后旧英雄进暂存区, 面板仍要显示)。
    HAND/DECK 里的英雄牌只是卡牌, 不算英雄本体。找不到返回 None"""
    for p in _players(game):
        if p["controller"] == controller:
            hid = _tag_int(p, "HERO_ENTITY")
            if hid > 0:
                h = game["entities"].get(hid)
                if h is not None and _ctype(h) == "HERO":
                    return h
            break
    live = _of_side(game, controller, "PLAY", "HERO")
    if live:
        return live[0]
    gone = [e for e in game["entities"].values()
            if e["controller"] == controller and e["id"] > 0 and _ctype(e) == "HERO"
            and e["zone"] not in ("HAND", "DECK")]
    return gone[0] if gone else None


def _ent_class(lookup, class_names, ent):
    """英雄实体 -> 职业中文: 查卡库 cardClass, 缺失再退日志自带 CLASS tag; 判不出返回空"""
    if not ent:
        return ""
    c = lookup.get(ent["cardId"])
    cls = (c.get("cardClass") if c else "") or str(ent["tags"].get("CLASS") or "")
    return class_names.get(cls, "") if cls else ""


def _side_class(lookup, class_names, game, controller):
    """职业显示 = 初始英雄卡的职业: 变形/打出英雄牌只换英雄名不改职业 (如死亡之翼仍是战士)。
    初始英雄锚点缺失时退当前英雄, 再判不出报未知职业"""
    fh = (game.get("first_hero") or {}).get(controller)
    for ent in ((game["entities"].get(fh) if fh is not None else None), _hero_ent(game, controller)):
        zh = _ent_class(lookup, class_names, ent)
        if zh:
            return zh
    return "未知职业"


def _hero_line(lookup, game, controller):
    hero = _hero_ent(game, controller)
    if not hero:
        return "英雄：无"
    hp = max((_tag_int(hero, "HEALTH") or hero["peak_hp"]) - _tag_int(hero, "DAMAGE"), 0)  # 当前血=HEALTH-DAMAGE, 阵亡不为负
    max_hp = _tag_int(hero, "MAX_HEALTH") or hero["peak_hp"] or hp
    armor = _tag_int(hero, "ARMOR")
    weapon = next(iter(_of_side(game, controller, "PLAY", "WEAPON")), None)
    power = next(iter(_of_side(game, controller, "PLAY", "HERO_POWER")), None)
    weapon_txt = ""
    if weapon:
        wdur = _tag_int(weapon, "DURABILITY")  # 剩余耐久口径同地标: 有 tag 用之, 缺失按血量-已伤推算
        if not wdur:
            wdur = max((_tag_int(weapon, "HEALTH") or weapon["peak_hp"]) - _tag_int(weapon, "DAMAGE"), 0)
        weapon_txt = f" 武器 {_cn(_card_name(lookup, weapon['cardId'], weapon))}{_tag_int(weapon, 'ATK')}/{wdur}"
    pname = _cn(_card_name(lookup, power["cardId"], power)) if power else "无"
    pstate = "已用" if power and _tag_int(power, "EXHAUSTED") == 1 else "未用"
    return (f"英雄：{_cn(_card_name(lookup, hero["cardId"], hero))}血量 {hp}/{max_hp} 护甲 {armor}"
            f"{weapon_txt} 技能{pname}({pstate})")


def _minion_tags(ent, cur_turn=0):
    tags = []
    for key, zh in MINION_TAGS:
        if _tag_int(ent, key) == 1:
            tags.append(f"[{zh}]")
    dor = _tag_int(ent, "DORMANT")  # 休眠: 值=剩余唤醒回合数, 1 (或只标休眠无倒计时) 显示 [休眠]
    if dor >= 1:
        tags.append(f"[休眠{dor}]" if dor > 1 else "[休眠]")
    # 本回合上场: 按日志 JUST_PLAYED tag 实际当前值渲染 (引擎回合结束清 0, 不会残留);
    # 上场回合号与当前回合对账兜底 (个别召唤不带 JUST_PLAYED tag)。
    # 不能用 EXHAUSTED 判定: 登场疲劳/攻击后的 EXHAUSTED=1 会跨回合残留, 标记过期
    entered = ent.get("entered_turn")
    if _tag_int(ent, "JUST_PLAYED") == 1 or (cur_turn and entered == cur_turn):
        tags.append("[本回合上场]")
    if _tag_int(ent, "FROZEN") == 1:
        tags.append("[冻结]")
    return "".join(tags)


def _board_rows(lookup, game, controller):
    rows = []
    g_ent = _game_entity(game)
    cur_turn = _tag_int(g_ent, "TURN") if g_ent else 0
    ents = _of_side(game, controller, "PLAY", "MINION") + _of_side(game, controller, "PLAY", "LOCATION")
    ents.sort(key=lambda e: (e["zone_pos"], e["id"]))  # 地标占场面格子, 与随从按场上位置合并排序
    for i, m in enumerate(ents, 1):
        if _ctype(m) == "LOCATION":
            dur = _tag_int(m, "DURABILITY")  # 剩余耐久: 有 DURABILITY tag 用之, 否则按血量-已伤推算
            if not dur:
                dur = max((_tag_int(m, "HEALTH") or m["peak_hp"]) - _tag_int(m, "DAMAGE"), 0)
            rows.append(f"  {i}.{_cn(_card_name(lookup, m['cardId'], m))}[地标 耐久{dur}]")
            continue
        hp = (_tag_int(m, "HEALTH") or m["peak_hp"]) - _tag_int(m, "DAMAGE")
        rows.append(f"  {i}.{_cn(_card_name(lookup, m["cardId"], m))}{_tag_int(m, 'ATK')}/{hp} {_minion_tags(m, cur_turn)}".rstrip())
    return rows


def _forge_prev_name(lookup, game, ent):
    """兆示 (Forge) 预览实体卡名: DISPLAY_ENTITY_ON_MOUSEOVER 指向的暂存区预览实体
    (打出/抽到兆示牌时引擎建的 3 个 PREMIUM 变体之一)。预览对我方不可见时无 cardId
    -> 返回 None, 调用方省略后缀 (不显示'兆示：未知')"""
    pid = _tag_int(ent, "DISPLAY_ENTITY_ON_MOUSEOVER") if ent else 0
    prev = game["entities"].get(pid) if pid > 0 else None
    cid = (prev["cardId"] if prev else "") or (prev.get("seen_cardid") if prev else "") or ""
    return _card_name(lookup, cid, prev) if cid else None


def _hand_rows(lookup, game, controller):
    rows = []
    hand = sorted((e for e in _in_zone(game, controller, "HAND")), key=lambda e: (e["zone_pos"], e["id"]))
    for i, h in enumerate(hand, 1):
        c = lookup.get(h["cardId"])
        cost = _num(h["tags"].get("COST"))
        if cost is None:
            cost = c.get("cost") if c and c.get("cost") is not None else 0
        ctype = "任务" if _is_quest_ent(h) else TYPE_ZH.get(_ctype(h), _ctype(h) or "未知")
        body = f"{_cn(_card_name(lookup, h["cardId"], h))}{cost}费"
        if _ctype(h) == "MINION":
            body += f" {_tag_int(h, 'ATK')}/{(_tag_int(h, 'HEALTH') or h['peak_hp']) - _tag_int(h, 'DAMAGE')}"
        marks = ""
        if _tag_int(h, "POWERED_UP") == 1:
            marks += " [已强化]"  # 条件触发强化已生效 (如牌库达标减费)
        if _tag_int(h, "LITERALLY_UNPLAYABLE") == 1:
            marks += " [不可打出]"  # 当前条件不满足, 本回合无法使用
        forge = _forge_prev_name(lookup, game, h)
        if forge:
            marks += f" <兆示：{_cn(forge)}>"
        rows.append(f"  {i}.{body} {ctype}{marks}")
    return rows


def _player_name(p):
    name = p["name"] or f"玩家{_tag_int(p, 'PLAYER_ID')}"
    return name.split("#")[0]  # 战网名 鸵鸟居士#5869 -> 鸵鸟居士


# ---------- 行动回顾渲染 ----------

def _ev_name(lookup, game, eid, fallback=""):
    """事件实体 -> 显示名: 优先最终 cardId 查中文卡名 (对手出牌等事后揭示也能取到);
    cardId 被藏回清空时退曾见名 (开局触发源/换掉的牌被引擎重新隐藏)"""
    ent = game["entities"].get(eid) if eid is not None else None
    if ent:
        if ent["cardId"]:
            return _card_name(lookup, ent["cardId"], ent)
        if ent.get("seen_cardid"):
            return _card_name(lookup, ent["seen_cardid"], ent)
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
    return _cn(nm) or "未知目标"


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
    """攻血快照 -> "a/b" 后缀 (紧邻「」名无缝, 不带前导空格); 无快照返回空"""
    return f"{stat[0]}/{stat[1]}" if stat else ""


def _power_desc(lookup, game, eid, name):
    """英雄技能描述: 经典技能按名/ID 内置, 其余查全量库; 去掉原文自带的'英雄技能'引导词"""
    ent = game["entities"].get(eid)
    desc = (HERO_POWER_DESC_BY_NAME.get(name) or HERO_POWER_TEXTS.get((ent.get("cardId") if ent else "") or "")
            or _ev_desc(lookup, game, eid))
    if desc.startswith("英雄技能"):  # 技能原文自带的引导词与行前缀重复
        desc = desc[len("英雄技能"):].strip()
    return desc


def _ent_disp_name(lookup, game, me, opp, eid):
    """事件实体显示名: 英雄按阵营称 我方英雄/对方英雄 (称谓不包「」), 其余查卡名并包「」"""
    ent = game["entities"].get(eid) if eid is not None else None
    if ent and _ctype(ent) == "HERO":
        if me and ent["controller"] == me["controller"]:
            return "我方英雄"
        if opp and ent["controller"] == opp["controller"]:
            return "对方英雄"
        return _ev_name(lookup, game, eid) or "英雄"
    return _cn(_ev_name(lookup, game, eid, "未知目标"))


def _heal_src_label(lookup, game, ev):
    """治疗来源描述: 英雄技能块=英雄技能 X, 触发/法术效果块=X效果; 判不出返回 None"""
    if ev.get("src_kind") == "heropower":
        nm = _ev_name(lookup, game, ev.get("src_id"), "") if ev.get("src_id") is not None else ""
        return f"英雄技能{_cn(nm)}".rstrip()
    return _src_label(lookup, game, ev)


def _single_event_text(lookup, game, me, opp, ev):
    eid = ev.get("eid")
    if ev["type"] == "play":
        name = _ev_name(lookup, game, eid, "未知卡牌")
        ct = ev.get("ctype")
        if ct == "HERO":
            return f"打出英雄牌{_cn(name)}"
        body = f"打出 {TYPE_ZH.get(ct, ct or '卡牌')}「{name}」"
        if ct == "MINION":
            body += _fmt_stat(ev.get("stat"))  # 打出时刻快照 (最终态可能已被 buff/打伤)
        # 描述挂"入手"一次: 我方牌在抽牌/获得/起手时已带, 打出不重复; 对方手牌不可见,
        # 对方打出行是该牌唯一展示点, 必带 (失控龙蛙教训: 任何牌的描述至少出现一次)
        if not (me and ev.get("actor") == me["controller"]):
            desc = _ev_desc(lookup, game, eid)
            if desc:
                body += f"<{desc}>"
        # 块显式 Target = 打出指向 (法术/武器/战吼选定的目标, 随从战吼同样回填, 块内新建实体也可被指向);
        # 随从指向未揭示实体 (如对方从自己手牌选牌) 时目标名不可知, 不渲染"未知目标"噪音
        tref = ev.get("target_ref")
        tgt = _ev_target(lookup, game, me, opp, tref)
        if tgt and not (ct == "MINION" and tgt == "未知目标"):
            body += f"→ {tgt}"
        tid = _ref_id(tref) if tref else None  # 主目标已在 → 中显示, 影响类追加目标去重防双写
        tgts = [x for x in (_ev_target(lookup, game, me, opp, str(t))
                            for t in ev.get("bc_tgts") or []
                            if not (tid and tid > 0 and str(t) == str(tid))) if x]
        if tgts:  # 战吼对其他实体的影响 (沉默/伤害/控制) -> 追加目标
            body += f" → {' ／ '.join(tgts)}"
        if ev.get("forge_pid"):
            # 兆示预览: 用打出时刻快照的指针 (引擎出牌块内已把 DISPLAY 清 0, 最终态查不到指向);
            # 预览无 cardId (对方的兆示不可见) 时不显示, 不报'未知'
            pent = game["entities"].get(ev["forge_pid"])
            cid = (pent["cardId"] if pent else "") or (pent.get("seen_cardid") if pent else "") or ""
            if cid:
                body += f"（兆示{_cn(_card_name(lookup, cid, pent))}）"
        return body
    if ev["type"] == "attack":
        ref = ev.get("atk_ref") or ""
        atk_id = _ref_id(ref)
        name = _ev_name(lookup, game, atk_id, fallback=ref if ref[:1].isalpha() else "") or "未知随从"
        tgt = _ev_target(lookup, game, me, opp, ev.get("tgt_ref"))
        # Target=0/-1 = 引擎记录的取消/无目标攻击块, 只显示攻击方, 不编造"未知目标"
        body = f"攻击{_cn(name)}{_fmt_stat(ev.get('atk_stat'))}"
        if not tgt:
            return body
        body = f"{body} → {_j(tgt, _fmt_stat(ev.get('tgt_stat')))}"
        tid = _ref_id(ev.get("tgt_ref") or "")
        tent = game["entities"].get(tid) if tid is not None else None
        if tent is not None and _tag_int(tent, "DORMANT") >= 1:
            body += "（休眠中）"  # 攻击目标仍处休眠 (囚禁类效果唤醒前的异常窗口)
        amt = ev.get("dmg_amt")
        if amt and not (ev.get("atk_stat") and amt == ev["atk_stat"][0]):
            body += f"（伤害 {amt}）"  # 实际伤害与攻击力不符 (光环/临时增幅) 时标注
        return body
    if ev["type"] == "power":
        name = _ev_name(lookup, game, eid, "未知技能")
        body = f"英雄技能{_cn(name)}"
        armor = ev.get("armor")
        if armor:  # 技能自带的护甲变化内联 (如 全副武装！（+2 护甲）), 不再单发护甲事件
            return f"{body}（+{armor} 护甲）"
        desc = _power_desc(lookup, game, eid, name)
        if desc:
            body += f"<{desc}>"
        return body
    if ev["type"] == "power_change":
        name = _ev_name(lookup, game, eid, "未知技能")
        body = f"技能变更{_cn(name)}"
        desc = _power_desc(lookup, game, eid, name)
        if desc:
            body += f"<{desc}>"
        return body
    if ev["type"] == "heal":
        name = _ent_disp_name(lookup, game, me, opp, eid)
        hp = ev.get("hp")
        src = _heal_src_label(lookup, game, ev)
        head = _j(src, "治疗 ") if src else "治疗"
        if hp:
            return _j(head, _j(name, f"血{hp[0]}→{hp[1]}"))
        return _j(head, name)
    if ev["type"] == "aura_cb":
        name = _ent_disp_name(lookup, game, me, opp, eid)
        vals = ev.get("vals")
        return _j(_j(f"光环移除{name}", "属性回调"), f"{vals[0]}→{vals[1]}") if vals else f"光环移除{name}"
    if ev["type"] == "armor":
        ent = game["entities"].get(eid)
        if ent and me and ent["controller"] == me["controller"]:
            name = "我方英雄"
        elif ent and opp and ent["controller"] == opp["controller"]:
            name = "对方英雄"
        else:
            name = _cn(_ev_name(lookup, game, eid, "未知英雄"))
        gain = ev.get("gain")
        return _j("获得护甲", _j(name, f"+{gain}")) if gain is not None else _j("获得护甲", name)
    if ev["type"] == "summon":
        name = _ev_name(lookup, game, eid) or "未知随从"
        body = f"召唤{_cn(name)}{_fmt_stat(ev.get('stat'))}"
        n = ev.get("count") or 1
        if n > 1:
            body += f" ×{n}"  # 同回合同来源同名召唤合并 (食尸鬼潮防刷屏)
        src = _src_label(lookup, game, ev)
        if src:
            body += f"（{src}）"
        else:
            desc = _ev_desc(lookup, game, eid)
            if desc:
                body += f"<{desc}>"
        return body
    if ev["type"] == "bounce":
        return f"回手{_cn(_ev_name(lookup, game, eid, '未知卡牌'))}"
    if ev["type"] == "equip":
        return f"装备武器{_cn(_ev_name(lookup, game, eid) or '未知武器')}"
    if ev["type"] == "death":
        body = f"死亡{_cn(_ev_name(lookup, game, eid, '未知卡牌'))}"  # 死亡体血量必归零, 攻血快照无决策价值不显示
        if ev.get("reborn"):
            rs = ev.get("reborn_stat")
            body += f"（复生，复活为 {rs[0]}/{rs[1]}）" if rs else "（复生）"
        return body
    if ev["type"] == "burn":
        return f"手牌已满，烧掉{_cn(_ev_name(lookup, game, eid, '未知卡牌'))}"
    if ev["type"] == "eff_damage":
        src = _src_label(lookup, game, ev) or "效果"
        tent = game["entities"].get(ev.get("tgt_id"))
        if tent is not None and _ctype(tent) == "HERO":
            tnm = ("我方英雄" if me and tent["controller"] == me["controller"]
                   else "对方英雄" if opp and tent["controller"] == opp["controller"] else "英雄")
        else:
            tnm = _cn(_ev_name(lookup, game, ev.get("tgt_id"), "未知目标"))
        amount = ev.get("amount")
        body = _j(_j(_j(src, "对"), tnm), "造成")
        body += f" {amount if amount is not None else '?'} 点伤害"
        if ev.get("lethal"):
            body += "（致命）"  # 目标当场血量归零 (斩杀链终点)
        return body
    if ev["type"] == "gain_src":
        src = _src_label(lookup, game, ev)
        tail = f"（{src}获得）" if src else ""
        if opp and ev.get("actor") == opp["controller"]:
            return f"获得 1 张牌{tail}"  # 对方手牌内容匿名, 口径同抽牌
        name = _ev_name(lookup, game, eid, "未知卡牌")
        desc = _ev_desc(lookup, game, eid)  # 获得是入手点, 描述挂一次
        return f"获得{_cn(name)}" + (f"<{desc}>" if desc else "") + tail
    if ev["type"] == "secret_play":
        ent = game["entities"].get(eid)
        kind = "任务" if ent and _is_quest_ent(ent) else "奥秘"  # SECRET 区含奥秘与任务, QUEST/SIDEQUEST tag 区分
        name = ev.get("name") or ""  # 打出时刻的名字/cardId 快照 (实体可能被任务链变形重建)
        if not name and ev.get("known"):
            name = _card_name(lookup, ev.get("card_id") or "", None)
        if not name:
            return f"打出 {kind}"  # 对方真奥秘匿名, 无名字也无描述
        body = f"打出 {kind}「{name}」"
        if not (me and ev.get("actor") == me["controller"]):  # 描述挂入手一次: 我方打出不带, 对方任务公开可见故带
            desc = _ev_desc(lookup, game, eid, card_id=ev.get("card_id"))
            if desc:
                body += f"<{desc}>"
        return body
    if ev["type"] == "secret_trigger":
        ent = game["entities"].get(eid)
        kind = "任务" if ent and _is_quest_ent(ent) else "奥秘"
        name = _ev_name(lookup, game, eid, f"未知{kind}")
        body = f"{kind}{'完成' if kind == '任务' else '触发'}{_cn(name)}"
        desc = _ev_desc(lookup, game, eid)
        if desc:
            body += f"<{desc}>"
        if kind == "任务":
            body += _quest_reward_hint(lookup, game, ev)
        return body
    if ev["type"] == "sog":
        name = _ev_name(lookup, game, ev.get("src"), "未知卡牌")
        parts = []
        by_ctl = {}
        for eid in ev.get("copies") or []:
            ent = game["entities"].get(eid)
            if ent:
                by_ctl.setdefault(ent["controller"], []).append(eid)
        for ctl in sorted((c for c in by_ctl if c is not None), key=lambda c: (c != (me["controller"] if me else None), c)):
            eids = by_ctl[ctl]
            nms = [_cn(nm) for nm in (_ev_name(lookup, game, eid) for eid in eids) if nm]
            tail = f"（{' ／ '.join(nms)}）" if len(nms) == len(eids) else ("（卡名未知）" if not nms else f"（{' ／ '.join(nms)} 等）")
            parts.append(f"复制 {len(eids)} 张洗入{_ev_side(game, me, opp, ctl)}牌库{tail}")
        pw = ev.get("power")
        if pw is not None:
            parts.append(f"替换英雄技能为{_cn(_ev_name(lookup, game, pw, '未知技能'))}")
        if not parts:
            return f"{_cn(name)}触发"
        return f"{_cn(name)}触发：{'，'.join(parts)}"
    if ev["type"] == "choose":
        eids = ev.get("eids") or []
        nms = [_cn(nm) for nm in (_ev_name(lookup, game, e) for e in eids) if nm]
        if nms:
            return "选择" + " ／ ".join(nms) + ("" if len(nms) == len(eids) else " 等")
        return f"选择 {len(eids)} 项"  # 选项全程未揭示 (对方发现), 只报动作
    if ev["type"] == "shuffle":
        eids = ev.get("eids") or []
        n = ev.get("count") or len(eids)
        body = f"洗入牌库 {n} 张"
        src = _src_label(lookup, game, ev)
        if src:
            body += f"（{src}）"
        nms = [_cn(nm) for nm in (_ev_name(lookup, game, e) for e in eids) if nm]
        if nms:
            body += "：" + " ／ ".join(nms) + ("" if len(nms) == len(eids) else " 等")
        return body
    if ev["type"] == "reveal":
        name = _ev_name(lookup, game, eid, "未知卡牌")
        src = _src_label(lookup, game, ev) or "触发"
        desc = _ev_desc(lookup, game, eid)  # 亮出常是该牌唯一可见点 (亡语抽施法类), 描述带上
        body = _j(_j(src, "亮出"), _cn(name) + (f"<{desc}>" if desc else ""))
        return body + ("（已施放）" if ev.get("cast") else "（仅亮出，未施放）")
    if ev["type"] == "fatigue":
        n = ev.get("count")
        return f"疲劳 第 {n} 次（英雄扣 {n} 血）" if n is not None else "疲劳"
    if ev["type"] == "deck_action":
        name = _ev_name(lookup, game, eid, "未知卡牌")
        spent = ev.get("spent")
        cut = _deck_cost_cut(lookup, game, eid)
        if ev.get("prepare"):
            # 行动方已在行前缀 [第 N 回合·我方/对方] 标明, 正文不重复
            body = (f"花费 {spent} 费 给 手牌中的{_cn(name)} 预备" if spent is not None
                    else f"给 手牌中的{_cn(name)} 预备")
        else:
            body = f"对 手牌中的{_cn(name)} 发动手牌动作"
            if ev.get("summary"):
                body += f"（{ev['summary']}）"
        if cut:
            body += f"（费用 {cut[0]}→{cut[1]}）"
        return body
    if ev["type"] == "dormant":
        name = _ev_name(lookup, game, eid, "未知随从")
        src = _src_label(lookup, game, ev)
        return _j(_j(src, "将"), f"{_cn(name)} [休眠]") if src else f"{_cn(name)}进入休眠"
    if ev["type"] == "awaken":
        return f"{_ev_name(lookup, game, eid, '未知随从')} 苏醒"


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
                    lines.append((t, actor, f"抽牌{_cn(nm)}<{desc}>" if desc else f"抽牌{_cn(nm)}"))
                else:
                    body = f"抽牌 {len(items) + unknown} 张"
                    if items:
                        parts = []
                        for nm, eid_j in items:
                            desc = _ev_desc(lookup, game, eid_j)
                            parts.append(f"{_cn(nm)}<{desc}>" if desc else _cn(nm))
                        body += f"（{' ／ '.join(parts)}）"
                    lines.append((t, actor, body))
            else:
                verb = "弃牌" if et == "discard" else "获得"
                for nm, eid_j in items:
                    if et == "discard":
                        lines.append((t, actor, f"{verb}{_cn(nm)}"))
                    elif opp_ctl is not None and actor == opp_ctl:
                        # 对方获得: 名字在选择类事件里已公开, 描述留给打出行 (唯一必展示点)
                        lines.append((t, actor, f"{verb}{_cn(nm)}"))
                    else:  # 获得是入手点, 描述挂一次
                        desc = _ev_desc(lookup, game, eid_j)
                        lines.append((t, actor, f"{verb}{_cn(nm)}<{desc}>" if desc else f"{verb}{_cn(nm)}"))
                if unknown:
                    lines.append((t, actor, f"{verb} {unknown} 张牌" if et == "gain" else f"{verb} {unknown} 张"))
            i = j
            continue
        lines.append((t, actor, _single_event_text(lookup, game, me, opp, ev)))
        i += 1
    return lines


def _opening_lines(lookup, game, me, opp):
    """开局结构化行: 起手 N 张 -> 保留 X 换掉 A、B（换入 C、D）-> 对方保留 M 换 K 张 -> 后手获得幸运币。
    我方带卡名, 对方只报张数; 无换牌动作不输出换行; 已是完整的 [开局·xx] 行"""
    op = game["opening"]
    out = []

    def names_of(eids):
        nms = []
        for eid in eids:
            nm = _ev_name(lookup, game, eid)
            if not nm:
                return None
            nms.append(_cn(nm))
        return nms

    def bits_of(eids):
        """卡名+描述片段 (入手点挂一次描述): 起手/换入列表用; 任一卡名缺失返回 None"""
        bits = []
        for eid in eids:
            nm = _ev_name(lookup, game, eid)
            if not nm:
                return None
            desc = _ev_desc(lookup, game, eid)
            bits.append(f"{_cn(nm)}<{desc}>" if desc else _cn(nm))
        return bits

    for p, mine in ((me, True), (opp, False)):
        if not p:
            continue
        ctl = p["controller"]
        dealt = op["dealt"].get(ctl) or []
        if not dealt:
            continue
        if mine:
            nms = bits_of(dealt)  # 起手=首次入手, 描述挂一次 (换牌阶段留牌建议的素材)
            out.append(f"[开局·我方] 起手 {len(dealt)} 张" + (f"（{' ／ '.join(nms)}）" if nms else ""))
        kept_raw = op["kept"].get(ctl)
        if kept_raw is None:
            continue  # 未提交保留选择 (秒投/异常局): 换掉无从谈起, 不输出换行
        out_e = [e for e in dealt if e not in set(kept_raw)]  # 换掉 = 起手中未保留的
        in_e = op["mull_in"].get(ctl) or []
        if not out_e:
            continue  # 全保留: 不输出换行
        if mine:
            out_n = names_of(out_e)
            body = f"[开局·我方] 保留 {len(dealt) - len(out_e)} 张换掉"
            body += _j("", " ／ ".join(out_n)) if out_n else _j("", f"{len(out_e)} 张")
            if in_e:
                in_n = bits_of(in_e)  # 换入=新入手, 描述挂一次
                body += f"（换入{' ／ '.join(in_n)}）" if in_n else f"（换入 {len(in_e)} 张）"
            out.append(body)
        else:
            out.append(f"[开局·对方] 保留 {len(dealt) - len(out_e)} 换 {len(out_e)} 张")
    # 后手获得幸运币: 按 FIRST_PLAYER 推 (硬币有换肤 variant, cardId 不可靠; 与 [后手+硬币] 标记同源)
    first = next((p for p in _players(game) if _tag_int(p, "FIRST_PLAYER") == 1), None)
    if first is not None and len(_players(game)) > 1:
        out.append("[开局·后手] 获得幸运币")
    return out


def _events_section(lookup, game, me, opp, turns=3, mulligan=False):
    """行动回顾: 开局结构化行 (起手/换牌/幸运币) + 事件行 (开局触发等), 按回合边界自动带最近
    turns 个回合 (我方上回合全部+对方上回合全部+我方本回合已发生), 不按条数截断;
    回合分节头只出现一次, 节内事件带 回合-序号 编号 (如 17-3) 方便引用"""
    lines = _event_lines(lookup, game, me, opp)
    out = ["=== 行动回顾 ==="]
    rendered = [(0, None, s) for s in _opening_lines(lookup, game, me, opp)]
    rendered += [(t, actor, txt) for t, actor, txt in lines]
    if not rendered:
        out.append("（暂无行动记录）")
        return out
    cur_t = game["turn"]
    if turns and cur_t:
        floor = cur_t - (turns - 1)
        rendered = [x for x in rendered if floor <= x[0] <= cur_t]
    if not rendered:
        out.append("（近期无行动记录）")
        return out
    last_key, seq = None, 0
    for t, actor, txt in rendered:
        if t == 0:  # 开局行自带 [开局·xx] 前缀且双方交错, 不并入回合分节
            out.append(txt)
            continue
        key = (t, actor)
        if key != last_key:
            out.append(f"[第 {t} 回合·{_ev_side(game, me, opp, actor)}]")
            last_key, seq = key, 0
        seq += 1
        out.append(f"{t}-{seq} {txt}")
    cur_actor = game["actor"]
    if cur_actor is not None and not mulligan and not any(t == cur_t and a == cur_actor for t, a, _ in lines):
        out.append(f"[第 {cur_t} 回合·{_ev_side(game, me, opp, cur_actor)}]（尚未行动或无动作）")
    return out


def _mana_text(ent, mulligan):
    """玩家实体的法力数值段: 可用/总（已用）; 过载追加简洁标注
    (OVERLOAD_LOCKED=当前已被锁, OVERLOAD_OWED=下回合将锁, 都为 0 不显示)"""
    if mulligan:
        return "未开始"
    res = _tag_int(ent, "RESOURCES")
    used = _tag_int(ent, "RESOURCES_USED")
    temp = _tag_int(ent, "TEMP_RESOURCES")
    txt = f"{res - used + temp}/{res}（已用 {used}）"
    locked, owed = _tag_int(ent, "OVERLOAD_LOCKED"), _tag_int(ent, "OVERLOAD_OWED")
    if locked or owed:
        txt += f" 过载{locked}" if locked else " 过载"
        if owed:
            txt += f"（下回合+{owed}）"
    return txt


def _mana_line(game, me, mulligan):
    if not me:
        return ""
    return f"我的法力 {_mana_text(me, mulligan)}"


def render_panel(game, start_line, total, lookup, class_names, player_arg=None, events_turns=3, debug=False):
    out = ["=== 炉石对局面板 ==="]
    if game is None or not game["entities"]:
        out.append("（尚未开始对局或日志为空）")
        if debug:
            out.append(f"# 实体总数 0 | 解析起始行 {start_line} | 日志总行 {total}")
        return "\n".join(out)

    players = _players(game)
    me, opp, unknown = detect_me(game, player_arg)
    mulligan = is_mulligan(game)
    sides = [(opp, "对方"), (me, "我方")] if me else [(p, None) for p in players]
    sides = [(p, lab) for p, lab in sides if p]

    # 对局元信息: 类型·赛制 | 构建号 (日志无元信息则整行省略)
    meta = game.get("meta") or {}
    gt, ft = meta.get("GameType", ""), meta.get("FormatType", "")
    meta_bits = []
    mode = GAME_TYPE_ZH.get(gt, gt)
    fmt = FORMAT_TYPE_ZH.get(ft, ft)
    if mode and fmt:
        meta_bits.append(f"{mode}·{fmt}")
    elif mode or fmt:
        meta_bits.append(mode or fmt)
    if meta.get("BuildNumber"):
        meta_bits.append(f"构建号 {meta['BuildNumber']}")
    if meta_bits:
        out.append(" | ".join(meta_bits))

    # 日志超限截断: 终局 packet 可能缺失, 不再输出胜负判定行避免误判
    if game.get("truncated"):
        kb = game.get("trunc_kb")
        out.append("【日志已截断" + (f"（{kb}KB 上限）" if kb else "") + "，终局信息可能缺失】")
    elif is_game_over(game):
        out.append("【对局已结束】")
        out.append(_endgame_line(game, me, opp))
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

    order = _order_labels(players)
    for p, label in sides:
        ctl = p["controller"]
        pname = _player_name(p)
        name = label or pname
        cls = _side_class(lookup, class_names, game, ctl)
        fatigue = _tag_int(p, "FATIGUE")
        corpses = _tag_int(p, "CORPSES")  # 尸体数 (DK/亡灵体系): 挂在玩家实体上, 无此 tag 或 0 不显示
        secrets = _secret_count(game, ctl)
        deck = len(_in_zone(game, ctl, "DECK"))  # 直接统计 DECK 区实体数, 复制牌导致的超编也如实反映
        order_tag = order.get(p["id"], "")
        hand_n = len(_in_zone(game, ctl, "HAND"))
        stat_bits = []
        if label != "我方":
            stat_bits.append(f"手牌 {hand_n}")  # 对方空手也是信息, 0 张照显
        if secrets:
            stat_bits.append(f"奥秘 {secrets}")
        stat_bits.append(f"牌库 {deck}")
        if corpses:
            stat_bits.append(f"尸体 {corpses}")
        if fatigue:
            stat_bits.append(f"疲劳 {fatigue}")
        head_txt = f"{name}：{pname}（{cls}）" if label else f"{pname}（{cls}）"
        if order_tag:
            head_txt += f"{order_tag} "
        # 法力是公开信息: 对方 (及未知我方时的各方) 状态行补法力/过载段, 我方法力在面板头已有不重复
        mana_txt = "" if label == "我方" else f" 法力 {_mana_text(p, mulligan)}"
        out.append(f"{head_txt}{' '.join(stat_bits)}{mana_txt}")
        out.append(_hero_line(lookup, game, ctl))
        out.extend(_quest_lines(lookup, game, ctl))
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

    if debug:
        out.append(f"# 实体总数 {len(game['entities'])} | 解析起始行 {start_line} | 日志总行 {total}")
    return "\n".join(out)


def cmd_board(args):
    log_path, use_stdin, player, events_turns, debug = None, False, None, 3, False
    for a in args:
        if a.startswith("--"):
            k, _, v = a[2:].partition("=")
            if k == "log":
                log_path = v
            elif k == "stdin":
                use_stdin = True
            elif k == "player":
                player = v
            elif k == "debug":
                debug = True
            elif k == "turns":
                events_turns = _num(v)
                if events_turns is None or events_turns < 0:
                    sys.exit("--turns 需要非负整数 (默认 3 = 最近 3 个回合, N=0 完全不输出行动回顾)")
            else:
                sys.exit(f"未知选项: --{k}")
        else:
            sys.exit(f"board 不接受位置参数: {a}\n"
                     "用法: board [--log=Power.log路径] [--stdin] [--player=玩家名] [--turns=N] [--debug]")
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
    print(render_panel(game, start_line, total, lookup, CLASS_NAMES, player, events_turns=events_turns, debug=debug))


