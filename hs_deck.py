"""猪猪组卡器 — 面向 Agent 的炉石传说组卡工具

子命令:
  update                              从 HearthstoneJSON 刷新卡牌库
  filter  [选项]                      筛卡 (--class/--set/--cost/--type/--text/--name)
  decode  <卡组代码>                  解码卡组代码为卡牌清单
  validate <deck.json>                校验卡组合法性并输出卡组代码 (stdin 用 -)
  save <名字> <代码或URL> [来源备注]   解析卡组并存入卡组库 (decks/)
  list                                列出卡组库
  show <名字>                         查看存档卡组明细
  check [名字]                        体检存档卡组 (版本更新后查失效卡; 省略名字=全部)
  fetch <URL>                         抓取网页中的卡组代码 (打印, 不入库)

deck.json 格式:
  {
    "format": "standard" | "wild",
    "hero": "加尔鲁什·地狱咆哮",          # 或 "#7" (dbfId)
    "cards": {"斩杀": 2, "#7": 1},       # 卡名或 #dbfId -> 数量
    "sideboard": {"owner": "乐队经理精英牛头人酋长", "cards": {"某卡": 3}}  # 可选
  }
"""
import base64
import json
import os
import re
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

DB_PATH = Path(r"D:\AgentWorkspace\tools\hs\cards_zh.json")
DB_URL = "https://api.hearthstonejson.com/v1/latest/zhCN/cards.collectible.json"

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


def load_db():
    if not DB_PATH.exists():
        sys.exit(f"卡牌库不存在: {DB_PATH}，先运行 update 子命令")
    return json.loads(DB_PATH.read_text(encoding="utf-8"))


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
    print("下载中...", DB_URL)
    req = urllib.request.Request(DB_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    json.loads(data)  # 完整性校验: 坏数据不落盘
    tmp = DB_PATH.with_suffix(".json.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, DB_PATH)  # 原子替换: 中途失败不会损坏现有牌库
    db = load_db()
    print(f"完成: {len(db)} 条 -> {DB_PATH}")


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


DECKS_DIR = Path(r"D:\AgentWorkspace\tools\hs\decks")
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
