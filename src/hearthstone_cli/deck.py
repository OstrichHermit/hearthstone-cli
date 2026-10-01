"""猪猪组卡器 — 面向 Agent 的炉石传说组卡工具

子命令:
  update                              从 HearthstoneJSON 刷新卡牌库 (中文+英文+中文全量)
  filter  [选项]                      筛卡 (--class/--set/--cost/--type/--text/--name)
  decode  <卡组代码>                  解码卡组代码为卡牌清单
  validate <deck.json>                校验卡组合法性并输出卡组代码 (stdin 用 -)
  save <名字> <代码或URL> [来源备注]   解析卡组并存入卡组库 (decks/)
  list                                列出卡组库
  show <名字>                         查看存档卡组明细
  check [名字]                        体检存档卡组 (版本更新后查失效卡; 省略名字=全部)
  fetch <URL>                         抓取网页中的卡组代码 (打印, 不入库)
  image <名字或代码> [--lang=zh|en|both] [--name=标题] [--name-en=英文标题] [--force] [--merge] [--out=路径.png]
                                      生成卡组长图 PNG (本地 Chrome/Edge 无头渲染)
                                      标准卡组含非标准池卡时拦截不出图 (--force 强制渲染)
                                      默认同名卡不合并逐张列出, --merge 则合并同名卡
  board   [--log=Power.log路径] [--stdin] [--player=玩家名]
                                      解析炉石客户端日志 Power.log, 输出当前对局面板 (供 AI 军师分析)
                                      默认读 %LOCALAPPDATA%\\Blizzard\\Hearthstone\\Logs\\Power.log, --stdin 从管道读
  watch   start [--channel=ID] [--url=URL] [--token=TOKEN] [--log=路径] [--force]
                                      启动军师监听守护进程 (tail Power.log, 换牌/我方回合时 POST 提示词到 IM 桥接器)
          stop / status               停止监听 / 查看状态与最近触发事件 (status 可加 --events=N)

deck.json 格式:
  {
    "format": "standard" | "wild",
    "hero": "加尔鲁什·地狱咆哮",          # 或 "#7" (dbfId)
    "cards": {"斩杀": 2, "#7": 1},       # 卡名或 #dbfId -> 数量
    "sideboard": {"owner": "乐队经理精英牛头人酋长", "cards": {"某卡": 3}}  # 可选
  }

数据目录: ~/.hearthstone-cli (卡牌库/卡组库/卡组图, 可用环境变量 HS_DECK_HOME 覆盖)
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

def _data_home():
    """数据根目录: 环境变量 HS_DECK_HOME > ~/.hearthstone-cli; 首次运行自动建目录"""
    env = os.environ.get("HS_DECK_HOME")
    root = Path(env).expanduser() if env else Path.home() / ".hearthstone-cli"
    root.mkdir(parents=True, exist_ok=True)
    (root / "decks").mkdir(parents=True, exist_ok=True)
    (root / "image").mkdir(parents=True, exist_ok=True)
    return root


BASE = _data_home()
DB_PATH = BASE / "cards_zh.json"
DB_EN_PATH = BASE / "cards_en.json"
DB_FULL_PATH = BASE / "cards_full_zh.json"  # 全量库: 含英雄技能/token 等非 collectible 卡
DB_URL = "https://api.hearthstonejson.com/v1/latest/zhCN/cards.collectible.json"
DB_URL_EN = "https://api.hearthstonejson.com/v1/latest/enUS/cards.collectible.json"
DB_URL_FULL = "https://api.hearthstonejson.com/v1/latest/zhCN/cards.json"
IMAGES_DIR = BASE / "image"

CARD_TYPES = {"MINION", "SPELL", "WEAPON", "LOCATION", "HERO"}

# 标准池白名单 (迅猛龙年2025.4起): 2027年春退环境
# 10.21 上线"黑暗帝国"后需新增对应 set 代码
STANDARD_SETS = {
    "CORE",                    # 核心系列 (轮换子集)
    "EMERALD_DREAM",           # 翡翠梦境 2025
    "THE_LOST_CITY",           # 失落之城 2025
    "TIME_TRAVEL",             # 穿越时间流 2026上半年
    "CATACLYSM",               # 大地的裂变 2026上半年
    "ESCAPEFROM_VIOLET_HOLD",  # 逃离紫罗兰监狱 2026下半年
    "BE",                      # 黑帝国古神系列 2026.9 (克苏恩/纯净圣母等4张传说)
}

# 特殊构筑规则卡 dbfId
AZALINA = 126055      # 裂魂者阿扎莉娜: 套牌20张
RAFAAM = 119432       # 时空大盗拉法姆: 套牌40张, 其中10张拉法姆
TAUREN = 90749        # 乐队经理精英牛头人酋长: 副牌库(乐队)3张

CLASS_NAMES = {
    "WARRIOR": "战士", "SHAMAN": "萨满", "ROGUE": "潜行者", "PALADIN": "圣骑士",
    "HUNTER": "猎人", "DRUID": "德鲁伊", "WARLOCK": "术士", "MAGE": "法师",
    "PRIEST": "牧师", "DEMONHUNTER": "恶魔猎手", "DEATHKNIGHT": "死亡骑士",
    "NEUTRAL": "中立",
}
CLASS_CN_TO_EN = {v: k for k, v in CLASS_NAMES.items()}
CLASS_CN_TO_EN["萨满祭司"] = "SHAMAN"  # 游客卡文本用全称

CLASS_NAMES_EN = {
    "WARRIOR": "Warrior", "SHAMAN": "Shaman", "ROGUE": "Rogue", "PALADIN": "Paladin",
    "HUNTER": "Hunter", "DRUID": "Druid", "WARLOCK": "Warlock", "MAGE": "Mage",
    "PRIEST": "Priest", "DEMONHUNTER": "Demon Hunter", "DEATHKNIGHT": "Death Knight",
    "NEUTRAL": "Neutral",
}


def load_db():
    if not DB_PATH.exists():
        sys.exit(f"卡牌库不存在: {DB_PATH}，先运行 update 子命令")
    return json.loads(DB_PATH.read_text(encoding="utf-8"))


def load_db_en():
    if not DB_EN_PATH.exists():
        sys.exit(f"英文卡牌库不存在: {DB_EN_PATH}，先运行 update 子命令")
    return json.loads(DB_EN_PATH.read_text(encoding="utf-8"))


def load_full_db():
    """全量卡牌库 (含英雄技能/皮肤/token 等): 懒加载 + 裁剪到 board 所需字段控制内存。

    供 board 查技能/token 的名字与描述; 缺文件返回空 dict (hs update 前不阻塞面板)。"""
    if not DB_FULL_PATH.exists():
        return {}
    raw = json.loads(DB_FULL_PATH.read_text(encoding="utf-8"))
    return {c.get("id"): {"name": c.get("name") or "", "text": c.get("text") or "",
                          "cost": c.get("cost"), "cardClass": c.get("cardClass") or ""}
            for c in raw if c.get("id")}


def playable(db):
    return [c for c in db if c.get("collectible") and c.get("type") in CARD_TYPES]


def find_hero(cards, spec):
    if spec.startswith("#"):
        hid = int(spec[1:])
        for c in cards:
            if c["dbfId"] == hid and c.get("type") == "HERO":
                return c
        sys.exit(f"找不到英雄 dbfId {hid}")
    for c in cards:
        if c.get("type") == "HERO" and c.get("name") == spec:
            return c
    sys.exit(f"找不到英雄: {spec}")


def resolve(card_map, pool, hero_class, fmt):
    """卡名/dbfId -> [(dbfId, count, card)]，处理同名多版本"""
    by_name = defaultdict(list)
    for c in pool:
        by_name[c["name"]].append(c)
    out = []
    for key, cnt in card_map.items():
        if key.startswith("#"):
            c = next((x for x in pool if x["dbfId"] == int(key[1:])), None)
            if not c:
                out.append(("ERR", key, cnt, f"dbfId {key[1:]} 不存在"))
            else:
                out.append(("OK", c, cnt, ""))
            continue
        hits = by_name.get(key, [])
        if not hits:
            out.append(("ERR", key, cnt, "卡名不存在"))
            continue
        if fmt == "standard":
            hits = [h for h in hits if h.get("set") in STANDARD_SETS]
        if not hits:
            out.append(("ERR", key, cnt, "该名字的卡不在当前环境卡池"))
            continue
        if len(hits) > 1:
            # 多版本同名卡: 标准池版本优先, 再选最新(dbfId最大); 游戏内各版本互通
            in_std = [h for h in hits if h.get("set") in STANDARD_SETS]
            pick = max(in_std or hits, key=lambda h: h["dbfId"])
            out.append(("OK", pick, cnt, ""))
            continue
        out.append(("OK", hits[0], cnt, ""))
    return out


def varint(n):
    buf = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            buf.append(b | 0x80)
        else:
            buf.append(b)
            return bytes(buf)


def encode_deck(hero_id, fmt_num, cards, sideboard):
    """cards: [(dbfId,count)], sideboard: [(dbfId,count,ownerId)]"""
    data = bytearray()
    data += varint(0) + varint(1) + varint(fmt_num) + varint(1) + varint(hero_id)
    for copies in (1, 2):
        grp = sorted(d for d, c in cards if c == copies)
        data += varint(len(grp))
        for i in grp:
            data += varint(i)
    multi = sorted((d, c) for d, c in cards if c >= 3)
    data += varint(len(multi))
    for d, c in multi:
        data += varint(d) + varint(c)
    data += varint(len(sideboard))
    for d, c, o in sideboard:
        data += varint(d) + varint(c) + varint(o)
    return base64.b64encode(bytes(data)).decode()


def read_varint(data, pos):
    result, shift = 0, 0
    while True:
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def print_card(cost, name, cnt, dbf, t, set_=""):
    print(f"{cost:>2}|{name}|x{cnt}|{t}|#{dbf}|{set_}")


def cmd_update():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    for path, url in ((DB_PATH, DB_URL), (DB_EN_PATH, DB_URL_EN), (DB_FULL_PATH, DB_URL_FULL)):
        print("下载中...", url)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        cards = json.loads(data)  # 完整性校验: 坏数据不落盘
        tmp = path.with_suffix(".json.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)  # 原子替换: 中途失败不会损坏现有牌库
        print(f"完成: {len(cards)} 条 -> {path}")


def cmd_filter(args):
    db = playable(load_db())
    cls = CLASS_CN_TO_EN.get(args.get("class"), args.get("class"))
    out = []
    for c in db:
        if cls and c.get("cardClass") != cls and cls != "ALL":
            continue
        if args.get("set") and c.get("set") != args["set"].upper():
            continue
        if args.get("type") and c.get("type") != args["type"].upper():
            continue
        cost = c.get("cost", 0)
        if args.get("cost") and not eval(str(cost) + args["cost"].replace("<=", "<=").replace(">=", ">=")):
            continue
        text = c.get("text") or ""
        if args.get("text") and args["text"] not in text:
            continue
        if args.get("name") and args["name"] not in c.get("name", ""):
            continue
        out.append(c)
    out.sort(key=lambda x: (x.get("cost", 0), x["name"]))
    print(f"共 {len(out)} 张")
    for c in out:
        print_card(c.get("cost", 0), c["name"], 1, c["dbfId"], c["type"], c.get("set", ""))


DECKS_DIR = BASE / "decks"
CODE_RE = r"AAE[A-Za-z0-9+/=]{20,}"


def parse_deck_code(code, lookup):
    """卡组代码 -> (fmt, heroes, cards[(dbfId,count)], sb[(dbfId,count,owner)], extra_note)"""
    raw = base64.b64decode(code)
    pos = 0
    _res, pos = read_varint(raw, pos)
    _ver, pos = read_varint(raw, pos)
    fmt, pos = read_varint(raw, pos)
    hero_count, pos = read_varint(raw, pos)
    heroes = []
    for _ in range(hero_count):
        h, pos = read_varint(raw, pos)
        heroes.append(h)
    cards = []
    for copies in (1, 2):
        n, pos = read_varint(raw, pos)
        for _ in range(n):
            d, pos = read_varint(raw, pos)
            cards.append((d, copies))
    n, pos = read_varint(raw, pos)
    for _ in range(n):
        d, pos = read_varint(raw, pos)
        c, pos = read_varint(raw, pos)
        cards.append((d, c))
    sb = []
    extra_note = ""
    if pos < len(raw):
        ok = True
        probe = pos
        try:
            n, probe = read_varint(raw, probe)
            if n > 30:
                ok = False
            else:
                tmp = []
                for _ in range(n):
                    d, probe = read_varint(raw, probe)
                    c, probe = read_varint(raw, probe)
                    o, probe = read_varint(raw, probe)
                    if d not in lookup or o not in lookup or c > 3:
                        ok = False
                        break
                    tmp.append((d, c, o))
                if ok:
                    sb = tmp
        except (IndexError, ValueError):
            ok = False
        if not ok:
            extra_note = f" (尾部有 {len(raw) - pos} 字节未知附加段, 已忽略 — 部分平台会在标准代码后追加扩展数据)"
    return fmt, heroes, cards, sb, extra_note


def fetch_code_from_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", "ignore")
    codes = re.findall(CODE_RE, html)
    return codes


def cmd_decode(code):
    db = load_db()
    lookup = {c["dbfId"]: c for c in db}
    fmt, heroes, cards, sb, extra_note = parse_deck_code(code, lookup)
    fmt_name = {1: "狂野", 2: "标准", 3: "经典"}.get(fmt, str(fmt))
    print(f"format={fmt_name} hero_count={len(heroes)} main={sum(c for _, c in cards)}张 sideboard={sum(c for _, c, _ in sb)}张{extra_note}")
    for h in heroes:
        info = lookup.get(h, {})
        print(f"HERO: #{h} {info.get('name')} ({CLASS_NAMES.get(info.get('cardClass'), info.get('cardClass'))})")
    print("-- 主卡组 --")
    rows = sorted(
        (lookup.get(d, {}).get("cost", 99), lookup.get(d, {}).get("name", "???"), c, d,
         lookup.get(d, {}).get("type", "?"), lookup.get(d, {}).get("set", "?"))
        for d, c in cards
    )
    for r in rows:
        print_card(*r)
    if sb:
        print("-- 副牌库 --")
        for d, c, o in sorted(sb, key=lambda x: (lookup.get(x[0], {}).get("cost", 99), lookup.get(x[0], {}).get("name", ""))):
            info = lookup.get(d, {})
            owner = lookup.get(o, {}).get("name", f"#{o}")
            print_card(info.get("cost", 99), info.get("name", "???"), c, d, info.get("type", "?"), f"owner:{owner}")


def check_spec(spec):
    """校验核心: 返回 {errors, resolved, sb_resolved, sb_owner, sb_total, hero, fmt, fmt_num, total}"""
    db = load_db()
    pool = playable(db)
    fmt = spec.get("format", "standard")
    fmt_num = {"standard": 2, "wild": 1}.get(fmt)
    if not fmt_num:
        sys.exit("format 必须是 standard 或 wild")
    hero = find_hero(pool, spec["hero"])
    hero_cls = hero["cardClass"]

    errors = []
    resolved = []
    for status, card, cnt, msg in resolve(spec["cards"], pool, hero_cls, fmt):
        if status == "ERR":
            errors.append(f"[{card} x{cnt}] {msg}")
        else:
            resolved.append((card, cnt))

    sb_owner = None
    sb_resolved = []
    sb = spec.get("sideboard")
    if sb:
        for status, card, cnt, msg in resolve(sb["cards"], pool, hero_cls, fmt):
            if status == "ERR":
                errors.append(f"[副牌 {card} x{cnt}] {msg}")
            else:
                sb_resolved.append((card, cnt))
        for status, card, _cnt, msg in resolve({sb["owner"]: 1}, pool, hero_cls, fmt):
            if status == "ERR":
                errors.append(f"[副牌owner {sb['owner']}] {msg}")
            else:
                sb_owner = card

    # 卡类合法性: 职业限定 + 数量限制 (total 只计主卡组, 副牌库单独计)
    # 多职业卡: cardClass 之外看 classes 数组 (如灭世者死亡之翼六职业共用)
    # 游客机制(胜地历险记): 带"X游客"随从仅解锁 X 职业该扩展包的卡; 每套限一名; 不可嵌套别职业游客
    tourist = {}  # 解锁的职业 -> 游客卡所属扩展包 set
    for c, _ in resolved:
        m = re.search(r"<b>([^<]{1,6})游客</b>", c.get("text") or "")
        if m and m.group(1) in CLASS_CN_TO_EN:
            tourist[CLASS_CN_TO_EN[m.group(1)]] = c.get("set")
    if len(tourist) > 1:
        errors.append(f"每套卡组只能带一名游客, 当前带了 {len(tourist)} 名 ({'、'.join(CLASS_NAMES.get(k, k) for k in tourist)})")
    total = 0
    name_count = defaultdict(int)
    for c, cnt in resolved + sb_resolved:
        cc = c.get("cardClass")
        classes = c.get("classes") or []
        if cc != "NEUTRAL" and cc != hero_cls and hero_cls not in classes and tourist.get(cc) != c.get("set"):
            names = "、".join(CLASS_NAMES.get(x, x) for x in (classes or [cc]))
            scope = f"(游客仅解锁 {CLASS_NAMES.get(cc, cc)} 的 {tourist.get(cc)} 系列)" if cc in tourist else ""
            errors.append(f"{c['name']} 只限 {names} 职业, 与英雄 {hero['name']} ({CLASS_NAMES.get(hero_cls)}) 不符{scope}")
        if re.search(r"<b>[^<]{1,6}游客</b>", c.get("text") or "") and cc != hero_cls and cc in tourist:
            errors.append(f"{c['name']} 是 {CLASS_NAMES.get(cc, cc)} 的游客牌, 不可通过游客机制嵌套加入")
        if c.get("type") == "HERO" and c.get("set") == "HERO_SKINS":
            errors.append(f"{c['name']} 是英雄皮肤, 不能放进卡组")
        name_count[c["name"]] += cnt
    for c, cnt in resolved:
        total += cnt

    for name, cnt in name_count.items():
        rarity = next((c.get("rarity") for c, _ in resolved + sb_resolved if c["name"] == name), "")
        limit = 1 if rarity == "LEGENDARY" else 2
        if name == "时空大盗拉法姆" and any(c["dbfId"] == RAFAAM for c, _ in resolved):
            limit = 10
        if cnt > limit:
            errors.append(f"{name} x{cnt} 超量 ({'传说' if limit == 1 else '非传说'}最多{limit}张)")

    # 动态容量规则
    main_ids = {c["dbfId"] for c, _ in resolved}
    if AZALINA in main_ids:
        if total != 20:
            errors.append(f"含裂魂者阿扎莉娜: 套牌必须恰好20张, 当前{total}张")
    elif RAFAAM in main_ids:
        raf_cnt = next((cnt for c, cnt in resolved if c["dbfId"] == RAFAAM), 0)
        if total != 40:
            errors.append(f"含时空大盗拉法姆: 套牌必须恰好40张, 当前{total}张")
        if raf_cnt != 10:
            errors.append(f"含时空大盗拉法姆: 拉法姆必须恰好10张, 当前{raf_cnt}张")
    else:
        if total != 30:
            errors.append(f"套牌必须30张, 当前{total}张")

    # sideboard 规则
    sb_total = sum(c for _, c in sb_resolved)
    if sb_resolved:
        if not sb_owner:
            errors.append("副牌库缺少 owner")
        elif sb_owner["dbfId"] != TAUREN:
            errors.append(f"副牌库 owner {sb_owner['name']} 不支持副牌机制")
        else:
            if sb_owner["dbfId"] not in main_ids:
                errors.append("带副牌必须主卡组里包含 乐队经理精英牛头人酋长")
            if sb_total != 3:
                errors.append(f"乐队必须恰好3张, 当前{sb_total}张")
            if fmt == "standard":
                errors.append("注意: 乐队经理精英牛头人酋长(传奇音乐节)已退标准, 只能狂野使用")

    # 标准池校验 (仅 standard)
    if fmt == "standard":
        for c, _ in resolved + sb_resolved:
            if c.get("set") not in STANDARD_SETS:
                errors.append(f"{c['name']} 的系列 {c.get('set')} 不在当前标准池")

    sb_total = sum(c for _, c in sb_resolved)
    return {
        "errors": errors, "resolved": resolved, "sb_resolved": sb_resolved,
        "sb_owner": sb_owner, "sb_total": sb_total, "hero": hero,
        "hero_cls": hero_cls, "fmt": fmt, "fmt_num": fmt_num, "total": total,
        "spec": spec,
    }


def print_check_result(r, exit_on_fail=True):
    if r["errors"]:
        print("校验失败:")
        for e in r["errors"]:
            print("  - " + e)
        if exit_on_fail:
            sys.exit(1)
        return False
    return True


def cmd_validate(path):
    spec = json.loads(sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8"))
    r = check_spec(spec)
    if not print_check_result(r):
        return
    hero, resolved, sb_resolved = r["hero"], r["resolved"], r["sb_resolved"]
    sb_owner, sb_total, fmt, fmt_num, total = r["sb_owner"], r["sb_total"], r["fmt"], r["fmt_num"], r["total"]

    cards_ids = [(c["dbfId"], cnt) for c, cnt in resolved]
    sb_ids = [(c["dbfId"], cnt, sb_owner["dbfId"]) for c, cnt in sb_resolved] if sb_resolved else []
    code = encode_deck(hero["dbfId"], fmt_num, cards_ids, sb_ids)

    print(f"校验通过 | {fmt} | 英雄: {hero['name']} | 主卡组{total}张" + (f" | 副牌{sb_total}张" if sb_resolved else ""))
    print(f"卡组代码: {code}")
    curve = defaultdict(int)
    for c, cnt in resolved:
        curve[min(c.get("cost", 0), 7)] += cnt
    print("法力曲线: " + " ".join(f"{k}:{curve[k]}" for k in range(8)))
    print("-- 卡组清单 --")
    rows = sorted((c.get("cost", 0), c["name"], cnt, c["dbfId"], c["type"], c.get("set", "")) for c, cnt in resolved)
    for r in rows:
        print_card(*r)
    if sb_resolved:
        print("-- 副牌库 --")
        for c, cnt in sb_resolved:
            print_card(c.get("cost", 0), c["name"], cnt, c["dbfId"], c["type"], c.get("set", ""))


def load_archive(name):
    p = DECKS_DIR / f"{name}.json"
    if not p.exists():
        sys.exit(f"卡组库中不存在: {name} (list 查看)")
    return json.loads(p.read_text(encoding="utf-8")), p


def cmd_save(name, src, note=""):
    code = src if src.startswith("AAE") else None
    if not code:
        codes = fetch_code_from_url(src)
        if not codes:
            sys.exit("URL 页面里没找到卡组代码 (SPA 页面可能需要浏览器渲染, 可手动复制代码)")
        code = codes[0]
        if len(codes) > 1:
            print(f"页面发现 {len(codes)} 段代码, 取第一段")
    db = load_db()
    lookup = {c["dbfId"]: c for c in db}
    fmt, heroes, cards, sb, _ = parse_deck_code(code, lookup)
    hero = lookup.get(heroes[0], {}) if heroes else {}
    fmt_name = {1: "wild", 2: "standard"}.get(fmt, "wild")

    def rows(pairs):
        out = []
        for d, c in sorted(pairs, key=lambda x: (lookup.get(x[0], {}).get("cost", 99), lookup.get(x[0], {}).get("name", ""))):
            info = lookup.get(d, {})
            out.append({"dbfId": d, "name": info.get("name", "???"), "count": c,
                        "cost": info.get("cost", 0), "type": info.get("type", ""),
                        "rarity": info.get("rarity", ""), "set": info.get("set", "")})
        return out

    arch = {
        "name": name, "code": code, "format": fmt_name,
        "hero": hero.get("name", ""), "hero_dbfId": hero.get("dbfId"), "class": hero.get("cardClass", ""),
        "cards": rows(cards),
        "source": note or ("URL: " + src if not src.startswith("AAE") else "手动导入"),
        "saved_at": time.strftime("%Y-%m-%d %H:%M"),
    }
    if sb:
        owner = lookup.get(sb[0][2], {}).get("name", "")
        arch["sideboard"] = {"owner": owner, "cards": rows([(d, c) for d, c, _ in sb])}
    DECKS_DIR.mkdir(parents=True, exist_ok=True)
    p = DECKS_DIR / f"{name}.json"
    p.write_text(json.dumps(arch, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已存档: {p} | {fmt_name} | {hero.get('name')} | 主{sum(c['count'] for c in arch['cards'])}张" +
          (f" + 副牌{sum(c['count'] for c in arch['sideboard']['cards'])}张" if sb else ""))


def cmd_list():
    if not DECKS_DIR.exists():
        print("卡组库为空")
        return
    files = sorted(DECKS_DIR.glob("*.json"))
    if not files:
        print("卡组库为空")
        return
    for p in files:
        a = json.loads(p.read_text(encoding="utf-8"))
        n = sum(c["count"] for c in a.get("cards", []))
        print(f"{a['name']} | {a['format']} | {a['hero']}({a['class']}) | {n}张 | {a.get('source','')} | {a.get('saved_at','')}")


def cmd_show(name):
    a, _ = load_archive(name)
    print(f"{a['name']} | {a['format']} | {a['hero']}({a['class']}) | 来源: {a.get('source','')} | 存档: {a.get('saved_at','')}")
    print(f"卡组代码: {a['code']}")
    print("-- 主卡组 --")
    for c in a["cards"]:
        print_card(c["cost"], c["name"], c["count"], c["dbfId"], c["type"], c["set"])
    if a.get("sideboard"):
        print(f"-- 副牌库 (owner: {a['sideboard']['owner']}) --")
        for c in a["sideboard"]["cards"]:
            print_card(c["cost"], c["name"], c["count"], c["dbfId"], c["type"], c["set"])


def cmd_check(name=None):
    if not DECKS_DIR.exists():
        sys.exit("卡组库为空")
    targets = sorted(DECKS_DIR.glob("*.json"))
    if name:
        targets = [DECKS_DIR / f"{name}.json"]
    all_ok = True
    for p in targets:
        a = json.loads(p.read_text(encoding="utf-8"))
        spec = {
            "format": a["format"], "hero": f"#{a['hero_dbfId']}",
            "cards": {f"#{c['dbfId']}": c["count"] for c in a.get("cards", [])},
        }
        if a.get("sideboard"):
            spec["sideboard"] = {"owner": a["sideboard"]["owner"],
                                 "cards": {f"#{c['dbfId']}": c["count"] for c in a["sideboard"]["cards"]}}
        r = check_spec(spec)
        ok = not r["errors"]
        all_ok = all_ok and ok
        head = f"{a['name']} ({a['format']} {a['hero']}): " + ("通过" if ok else "存在问题")
        print(head)
        if not ok:
            for e in r["errors"]:
                print("  - " + e)
        elif name:
            print(f"卡组代码: {a['code']}")
    if not all_ok and not name:
        sys.exit(1)


def cmd_fetch(url):
    codes = fetch_code_from_url(url)
    if not codes:
        sys.exit("URL 页面里没找到卡组代码 (营地等 SPA 页面可能需要浏览器渲染)")
    print(f"找到 {len(codes)} 段卡组代码:")
    for c in codes:
        print(f"  {c}")


# ---------- 卡组图 (image) ----------

RAR = {
    "FREE":      {"c": "#ffffff"},
    "COMMON":    {"c": "#ffffff"},
    "RARE":      {"c": "#2e9bff"},
    "EPIC":      {"c": "#c85aff"},
    "LEGENDARY": {"c": "#ffab00"},
}
RARITY_RANK = {"FREE": 0, "COMMON": 0, "RARE": 1, "EPIC": 2, "LEGENDARY": 3}
CLASS_ICONS = {
    "Warrior": "⚔️", "Mage": "🔮", "Hunter": "🏹", "Warlock": "👁️",
    "Priest": "✨", "Rogue": "🗡️", "Paladin": "⚜️", "Shaman": "⚡",
    "Druid": "🍃", "Demon Hunter": "😈", "Death Knight": "💀",
}
TYPE_ZH = {"MINION": "随从", "SPELL": "法术", "WEAPON": "武器", "LOCATION": "地标", "HERO": "英雄"}
TYPE_EN = {"MINION": "Minion", "SPELL": "Spell", "WEAPON": "Weapon", "LOCATION": "Location", "HERO": "Hero"}
DUST_COST = {"COMMON": 40, "RARE": 100, "EPIC": 400, "LEGENDARY": 1600}


def find_chrome():
    cands = []
    env = os.environ.get("CHROME_PATH")
    if env:
        cands.append(env)
    for exe in ("chrome", "google-chrome", "chromium", "msedge"):
        p = shutil.which(exe)
        if p:
            cands.append(p)
    if os.name == "nt":
        cands += [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ]
    for p in cands:
        if p and Path(p).exists():
            return p
    sys.exit("找不到 Chrome / Edge 浏览器 (可用环境变量 CHROME_PATH 指定路径)")


def shot_html(chrome, html_path, png_path):
    url = html_path.as_uri()
    base = [chrome, "--headless", "--disable-gpu", "--hide-scrollbars"]
    kw = {"capture_output": True, "timeout": 90, "encoding": "utf-8", "errors": "replace"}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    dom = subprocess.run(base + ["--dump-dom", url], **kw)
    m = re.search(r"<title>(\d+)</title>", dom.stdout or "")
    if not m:
        sys.exit("渲染失败: 未能获取页面高度 (Chrome 输出异常)")
    height = int(m.group(1))
    subprocess.run(base + [f"--screenshot={png_path}", f"--window-size=760,{height}",
                           "--force-device-scale-factor=2", url], **kw)
    if not png_path.exists():
        sys.exit("渲染失败: 截图未生成")
    return height


def build_image_html(deck, lang):
    T = {
        "zh": {"curve": "法力曲线", "std": deck["format_zh"], "cards": "卡牌", "dust": f"合成 {deck['dust']} 尘", "brand": "hs-deck-cli · 数据: HearthstoneJSON"},
        "en": {"curve": "Mana Curve", "std": deck["format_en"], "cards": "Cards", "dust": f"{deck['dust']} Dust", "brand": "hs-deck-cli · Data: HearthstoneJSON"},
    }[lang]
    title = deck["deck_name"] if lang == "zh" else deck["deck_name_en"]
    hero = deck["hero_zh"] if lang == "zh" else deck["hero_en"]
    cls = deck["class_zh"] if lang == "zh" else deck["class_en"]
    buckets = {i: 0 for i in range(0, 8)}
    for r in deck["cards"]:
        buckets[min(r["cost"], 7)] += r["count"]
    mx = max(buckets.values())
    rows_n = (len(deck["cards"]) + 1) // 2
    icon = CLASS_ICONS.get(deck["class_en"], "⚔️")

    def row(r):
        c = RAR[r["rarity"]]["c"]
        typ = r["type_zh"] if lang == "zh" else r["type_en"]
        nm = r["name"] if lang == "zh" else r["name_en"]
        tail = f"{c}e0" if r["rarity"] in ("COMMON", "FREE") else f"{c}a8"
        bg = (f"background:linear-gradient(90deg, {c}00 0%, {c}00 48%, {c}30 66%, {c}80 85%, {tail} 100%),"
              "linear-gradient(90deg, rgba(253,243,216,.9), rgba(253,243,216,.9));"
              'box-shadow:0 1px 3px rgba(90,70,40,.18);')
        if r["rarity"] == "LEGENDARY":
            cnt_html = '<span class="star">⭐</span>'
        elif r["count"] > 1:
            cnt_html = f'<span class="num">{r["count"]}</span>'
        else:
            cnt_html = ""
        return f'''<div class="card" style="{bg}">
  <div class="gem"><div class="hex-in"></div><b>{r["cost"]}</b></div>
  <div class="cname">{nm}<span class="ctype">{typ}</span></div>
  <div class="cnt">{cnt_html}</div>
</div>'''

    bars = "".join(
        f'<div class="bar"><span class="bv">{v}</span><div class="bwrap"><div class="bfill" style="height:{max(int(v / mx * 50), 6)}px"></div></div><span class="bl">{"7+" if k == 7 else k}</span></div>'
        for k, v in buckets.items()
    )

    return f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ width:760px; min-height:100px; position:relative;
  font-family:"Microsoft YaHei","Segoe UI",sans-serif; color:#4a3a26;
  background:linear-gradient(180deg, #f8f3e7 0px, #f8f3e7 160px, #f6e2b4 190px, #f6e2b4 100%); }}
.frame {{ position:absolute; inset:8px; border:2px solid #b09055; border-radius:12px; pointer-events:none; }}
.frame2 {{ position:absolute; inset:14px; border:1px solid #cbb98e; border-radius:8px; pointer-events:none; }}
.corner {{ position:absolute; width:26px; height:26px; border:3px solid #9a7b42; pointer-events:none; z-index:5; }}
.c-tl {{ top:4px; left:4px; border-right:none; border-bottom:none; border-radius:10px 0 0 0; }}
.c-tr {{ top:4px; right:4px; border-left:none; border-bottom:none; border-radius:0 10px 0 0; }}
.c-bl {{ bottom:4px; left:4px; border-right:none; border-top:none; border-radius:0 0 0 10px; }}
.c-br {{ bottom:4px; right:4px; border-left:none; border-top:none; border-radius:0 0 10px 0; }}
.wrap {{ padding:14px 26px 17px; display:flex; flex-direction:column; }}

.top {{ display:flex; align-items:stretch; gap:18px; padding:8px 4px 14px;
  border-bottom:1px solid #cbb98e; }}
.head-l {{ flex:1; min-width:0; display:flex; gap:14px; align-items:center; }}
.sigil {{ width:86px; height:86px; flex:none; border-radius:50%;
  background:radial-gradient(circle at 35% 28%, #f7e7c2, #e3c892 60%, #cfa96e);
  border:3px solid #b09055; box-shadow:0 0 12px rgba(176,144,85,.35);
  display:flex; align-items:center; justify-content:center; font-size:40px; line-height:1; }}
.tinfo {{ min-width:0; }}
h1 {{ font-size:31px; color:#6e4410; letter-spacing:2px; line-height:1.18;
  display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:2; overflow:hidden;
  padding-bottom:4px; margin-bottom:-4px;
  text-shadow:0 1px 0 rgba(255,255,255,.7); }}
.sub {{ font-size:13px; color:#8a7452; margin-top:4px; letter-spacing:.5px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.meta {{ display:flex; gap:8px; margin-top:9px; flex-wrap:wrap; }}
.chip {{ font-size:12px; color:#6a5535; padding:2.5px 11px; border-radius:14px; white-space:nowrap;
  background:#f0e0b4; border:1px solid #c4ab74; }}
.chip b {{ color:#4a3a26; }}

.curve {{ width:318px; flex:none; display:flex; flex-direction:column; justify-content:flex-end;
  padding:10px 14px 8px; border:1px solid #cbb98e; border-radius:10px;
  background:linear-gradient(180deg, #f3ecdc, #ece2cb); }}
.curve-head {{ display:flex; justify-content:space-between; align-items:baseline; margin-bottom:6px; }}
.curve-head h3 {{ font-size:12px; color:#8a6d3b; letter-spacing:2px; margin:0; }}
.chart {{ display:flex; align-items:flex-end; gap:7px; height:86px; }}
.bar {{ flex:1; display:flex; flex-direction:column; align-items:center; gap:3px; height:100%; }}
.bv {{ font-size:12px; color:#6a5535; font-weight:bold; height:15px; line-height:15px; flex:none; }}
.bwrap {{ flex:1; min-height:0; width:100%; display:flex; justify-content:center; align-items:flex-end; }}
.bfill {{ width:82%; border-radius:4px 4px 2px 2px;
  background:radial-gradient(circle at 30% 6%, rgba(255,255,255,.65) 0%, rgba(255,255,255,0) 30%),
    radial-gradient(circle at 34% 10%, #a8d4ff 0%, #3f83d6 55%, #10365f 100%);
  box-shadow:0 1px 3px rgba(20,65,126,.35); min-height:6px; }}
.bl {{ font-size:11px; color:#8a7452; height:15px; line-height:15px; flex:none; }}
.brand {{ text-align:right; font-size:10px; line-height:15px; color:#a08c62; font-family:"Segoe UI",sans-serif; letter-spacing:.5px; }}

.cards {{ margin-top:14px; display:grid; grid-auto-flow:column; grid-template-columns:1fr 1fr; grid-template-rows:repeat({rows_n}, 50px); gap:6px 14px; }}
.card {{ display:flex; align-items:center; gap:11px; height:50px; padding:0 12px 0 6px; border-radius:10px;
  min-width:0; overflow:hidden; }}
.gem {{ width:34px; height:38px; flex:none; margin-left:2px; position:relative;
  filter:drop-shadow(0 1px 3px rgba(20,65,126,.4)); }}
.hex-in {{ position:absolute; inset:0; clip-path:polygon(50% 0%, 100% 25%, 100% 75%, 50% 100%, 0% 75%, 0% 25%);
  background:radial-gradient(circle at 30% 18%, rgba(255,255,255,.8) 0%, rgba(255,255,255,0) 32%),
    radial-gradient(circle at 34% 22%, #a8d4ff 0%, #3f83d6 50%, #10365f 100%); }}
.gem b {{ position:absolute; inset:0; z-index:2; display:flex; align-items:center; justify-content:center;
  transform:translateY(-1px); font-size:16px; color:#fff; text-shadow:0 1px 3px #001f3d; }}
.cname {{ flex:1; min-width:0; font-size:17px; line-height:1.4; color:#3a2e1c; font-weight:bold; white-space:nowrap;
  overflow:hidden; text-overflow:ellipsis; }}
.ctype {{ font-size:11.5px; color:#8a7452; margin-left:9px; letter-spacing:.5px; font-weight:normal; }}
.cnt {{ display:flex; align-items:center; justify-content:center; width:34px; height:100%; flex:none; }}
.star {{ font-size:19px; line-height:1; display:block; transform:translateY(-2px);
  filter:drop-shadow(0 1px 2px rgba(60,40,10,.75)); }}
.num {{ font-size:19px; color:#fff; font-weight:bold; display:block; line-height:1; margin-right:0;
  -webkit-text-stroke:3px #000; paint-order:stroke fill;
  filter:drop-shadow(0 1px 2px rgba(60,40,10,.75)); }}

.code {{ margin-top:14px; background:#e3ce95; border:1px solid #c4ab74; border-radius:9px;
  padding:11px 14px; font-family:Consolas,monospace; font-size:12px; color:#4a3a26;
  word-break:break-all; text-wrap:balance; text-align:center; }}
</style></head><body>
<div class="wrap">
  <div class="top">
    <div class="head-l">
      <div class="sigil">{icon}</div>
      <div class="tinfo">
        <h1>{title}</h1>
        <div class="sub">{cls} · {hero}</div>
        <div class="meta">
          <span class="chip">{T["std"]}</span>
          <span class="chip">{deck["total"]} {T["cards"]}</span>
          <span class="chip">{T["dust"]}</span>
        </div>
      </div>
    </div>
    <div class="curve">
      <div class="curve-head"><h3>{T["curve"]}</h3><span class="brand">{T["brand"]}</span></div>
      <div class="chart">{bars}</div></div>
  </div>
  <div class="cards">{"".join(row(r) for r in deck["cards"])}</div>
  <div class="code">{deck["code"]}</div>
</div>
<script>document.title = document.body.scrollHeight;</script>
</body></html>'''


def cmd_image(src, lang="zh", name=None, name_en=None, out=None, force=False, merge=False):
    if src.startswith("AAE"):
        code, arch_name = src, None
    else:
        arch_name = src
        code = load_archive(src)[0]["code"]
    lookup_zh = {c["dbfId"]: c for c in load_db()}
    lookup_en = {c["dbfId"]: c for c in load_db_en()}
    fmt, heroes, cards, sb, _ = parse_deck_code(code, lookup_zh)

    if fmt == 2 and not force:
        bad = []
        for d, cnt in cards:
            c = lookup_zh.get(d, {})
            if c.get("set") not in STANDARD_SETS:
                bad.append(f"{c.get('name', '#' + str(d))} x{cnt} (set: {c.get('set')})")
        if bad:
            print("出图被拦截: 标准卡组含非标准池卡 (--force 可强制渲染):")
            for b in bad:
                print("  - " + b)
            sys.exit(1)

    rows = []
    for d, cnt in cards:
        cz, ce = lookup_zh.get(d, {}), lookup_en.get(d, {})
        for _ in range(1 if merge else cnt):
            rows.append({
                "name": cz.get("name", f"#{d}"),
                "name_en": ce.get("name") or cz.get("name", f"#{d}"),
                "count": cnt if merge else 1, "cost": cz.get("cost", 0),
                "type_zh": TYPE_ZH.get(cz.get("type"), cz.get("type", "")),
                "type_en": TYPE_EN.get(cz.get("type"), cz.get("type", "")),
                "rarity": cz.get("rarity", ""), "set": cz.get("set", ""),
            })
    rows.sort(key=lambda r: (r["cost"], RARITY_RANK.get(r["rarity"], 0), r["name"]))

    hero_card = lookup_zh.get(heroes[0], {}) if heroes else {}
    cc = hero_card.get("cardClass")
    if not cc or cc == "NEUTRAL":
        cnt_by_cls = defaultdict(int)
        for d, cnt in cards:
            cnt_by_cls[lookup_zh.get(d, {}).get("cardClass")] += cnt
        cnt_by_cls.pop("NEUTRAL", None)
        cc = max(cnt_by_cls, key=cnt_by_cls.get) if cnt_by_cls else "NEUTRAL"
    class_zh, class_en = CLASS_NAMES.get(cc, cc), CLASS_NAMES_EN.get(cc, cc)
    hero_zh = hero_card.get("name") or class_zh
    hero_en = (lookup_en.get(heroes[0], {}).get("name") if heroes else "") or hero_zh

    base = name or arch_name
    if base:
        dn_zh = base
        dn_en = name_en or (base if base.isascii() else f"{class_en} Deck")
    else:
        dn_zh, dn_en = f"{class_zh}卡组", name_en or f"{class_en} Deck"

    deck = {
        "deck_name": dn_zh, "deck_name_en": dn_en,
        "hero_zh": hero_zh, "hero_en": hero_en,
        "class_zh": class_zh, "class_en": class_en,
        "format_zh": "标准" if fmt == 2 else "狂野",
        "format_en": "Standard" if fmt == 2 else "Wild",
        "code": code, "total": sum(r["count"] for r in rows),
        "dust": sum(DUST_COST.get(r["rarity"], 0) * r["count"] for r in rows if r["set"] != "CORE"),
        "cards": rows,
    }
    print(f"{dn_zh} | {deck['format_zh']} | {class_zh}({hero_zh}) | {deck['total']}张 | 合成{deck['dust']}尘")

    chrome = find_chrome()
    langs = ["zh", "en"] if lang == "both" else [lang]
    for lg in langs:
        if out:
            op = Path(out)
            png = op.with_name(f"{op.stem}-{lg}{op.suffix}") if len(langs) > 1 else op
        else:
            safe = re.sub(r'[\\/:*?"<>|]', "_", base or dn_zh)
            if merge:
                safe += "-merge"
            png = IMAGES_DIR / f"{safe}-{lg}.png"
        html = png.with_suffix(".html")
        png.parent.mkdir(parents=True, exist_ok=True)
        html.write_text(build_image_html(deck, lg), encoding="utf-8")
        h = shot_html(chrome, html, png)
        print(f"已生成: {png} (1520x{h * 2})")
    if sb:
        print(f"提示: 该卡组含副牌库 {sum(c for _, c, _ in sb)} 张, 卡组图仅展示主卡组")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)
    cmd, rest = args[0], args[1:]
    if cmd == "update":
        cmd_update()
    elif cmd == "decode":
        cmd_decode(rest[0])
    elif cmd == "validate":
        cmd_validate(rest[0] if rest else "-")
    elif cmd == "save":
        if len(rest) < 2:
            sys.exit("用法: save <名字> <代码或URL> [来源备注]")
        cmd_save(rest[0], rest[1], rest[2] if len(rest) > 2 else "")
    elif cmd == "list":
        cmd_list()
    elif cmd == "show":
        cmd_show(rest[0])
    elif cmd == "check":
        cmd_check(rest[0] if rest else None)
    elif cmd == "fetch":
        cmd_fetch(rest[0])
    elif cmd == "image":
        kv, flags, pos = {}, set(), []
        for a in rest:
            if a.startswith("--"):
                k, _, v = a[2:].partition("=")
                k = k.replace("-", "_")
                if k in ("lang", "name", "name_en", "out"):
                    kv[k] = v
                elif k in ("force", "merge"):
                    flags.add(k)
                else:
                    sys.exit(f"未知选项: --{k}")
            else:
                pos.append(a)
        if not pos:
            sys.exit("用法: image <名字或代码> [--lang=zh|en|both] [--name=标题] [--name-en=英文标题] [--force] [--merge] [--out=输出路径.png]")
        if kv.get("lang", "zh") not in ("zh", "en", "both"):
            sys.exit("--lang 只能是 zh / en / both")
        cmd_image(pos[0], force=("force" in flags), merge=("merge" in flags), **kv)
    elif cmd == "board":
        from hearthstone_cli import board as _board
        _board.cmd_board(rest)
    elif cmd == "watch":
        from hearthstone_cli.watch_worker import cmd_watch
        cmd_watch(rest)
    elif cmd == "filter":
        kv = {}
        for a in rest:
            if a.startswith("--"):
                k, _, v = a[2:].partition("=")
                kv[k] = v
        cmd_filter(kv)
    else:
        print(f"未知命令: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
