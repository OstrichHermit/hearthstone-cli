"""hs collection — 从运行中的炉石客户端内存同步收藏卡组到本地卡组库 (仅 Windows)

用法:
  hs collection export [--check] [--config=路径]
                                        导出一次 (读取器缺失时自动构建; --check 只对比不写)
  hs collection build                   手动构建 DeckExport 内存读取器 (需 dotnet SDK 9)
  hs collection watch start [--config=路径] [--force]
  hs collection watch stop / status [--config=路径] [--events=N]

机制与 Hearthstone Deck Tracker 同款: 只读游戏内存, 不写游戏。读到 standard/wild 卡组后
自动编码为卡组代码, 按 hs save 存档格式写入 decks/; 游戏内删除的卡组本地默认保留,
配置开 sync_delete 后随游戏同步删除 (手动 save 导入的存档不受影响)。
配置统一在 ~/.hearthstone-cli/config.json 的 collection 节 (interval 轮询秒数 /
sync_delete 删除同步开关), CLI 不提供配置参数, 编辑文件后 start 生效,
改配置后 --force 重启生效; 与 hs watch 共用文件分节管理;
同目录 collection.log / collection.pid。
worker 本体: `python -m hearthstone_cli.collection --worker`, 标准库 only。
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

from hearthstone_cli import config
from hearthstone_cli.deck import BASE, DB_FULL_PATH, DECKS_DIR, encode_deck, load_db

NATIVE_BIN = BASE / "native" / "bin"          # DeckExport 部署目录 (产物可独立运行)
NATIVE_SRC = BASE / "native" / "src"          # 补丁版 HearthMirror 源码缓存
EXPORT_EXE = NATIVE_BIN / "DeckExport.exe"
MIRROR_PROJ = NATIVE_SRC / "HearthMirror.csproj"
SCRY_DLL = BASE / "native" / "lib" / "untapped-scry-dotnet.dll"
DOTNET_CANDS = [r"C:\Program Files\dotnet\dotnet.exe", r"C:\Program Files (x86)\dotnet\dotnet.exe"]
FMT_NAMES = {1: "wild", 2: "standard"}        # 镜像 formatType: 1=狂野 2=标准, 其余跳过
STATUS_ZH = {"new": "新增", "update": "更新", "same": "一致"}

USAGE = """用法: hs collection export [--check] [--config=路径]
                                    从运行中的游戏导出收藏卡组到卡组库
      hs collection build           手动构建 DeckExport 内存读取器 (需 dotnet SDK 9)
      hs collection watch start [--config=路径] [--force]
                                    启动卡组同步守护 (游戏卡组变化时自动写存档)
      hs collection watch stop / status [--config=路径] [--events=N]

仅 Windows。只读游戏内存不写游戏 (与 HDT 同款机制), standard/wild 卡组自动编码为卡组代码,
按 hs save 存档格式写入 decks/; 游戏内删除的卡组默认本地保留, 配置开 sync_delete 后随游戏
同步删除 (仅删除此前由同步写入的存档, 手动 save 导入的不受影响)。
配置: 编辑 ~/.hearthstone-cli/config.json 的 collection 节 (interval 轮询秒数 /
sync_delete 删除同步开关), 编辑后 start 生效, 改配置后 --force 重启生效;
与 watch 共用文件分节管理; 同目录 collection.log / collection.pid。"""


# ---------- PID (照 watch_worker; 配置读写统一走 config 模块) ----------

def _read_pid(p):
    try:
        return int(p.read_text().strip())
    except Exception:
        return None


def pid_alive(pid):
    """进程存活检测: Windows 用 ctypes 查询 (os.kill(pid,0) 在 Windows 会误杀进程, 禁用)"""
    if not pid or pid <= 0:
        return False
    try:
        import ctypes
    except Exception:
        return False
    if hasattr(ctypes, "windll"):
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return code.value == 259  # STILL_ACTIVE
            return False
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def kill_pid(pid):
    if os.name == "nt":
        r = subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True)
        return r.returncode == 0
    try:
        os.kill(pid, 15)
        return True
    except OSError:
        return False


def _parse_opts(argv, values, flags):
    out = {}
    for a in argv:
        if a.startswith("--"):
            k, eq, v = a[2:].partition("=")
            if k in values:
                if not eq:
                    sys.exit(f"选项 --{k} 需要 =值")
                out[k] = v
            elif k in flags:
                out[k] = True
            else:
                sys.exit(f"未知选项: --{k}\n{USAGE}")
        else:
            sys.exit(f"不接受位置参数: {a}\n{USAGE}")
    return out


# ---------- 构建 DeckExport 读取器 ----------

def _find_dotnet():
    for p in DOTNET_CANDS:
        if Path(p).exists():
            return p
    return shutil.which("dotnet")


def _deckexport_proj():
    """仓库内 DeckExport.csproj (editable 安装时 src/.. 即仓库根; 兼容 BASE 下副本)"""
    repo = Path(__file__).resolve().parents[2]
    for p in (repo / "native" / "DeckExport" / "DeckExport.csproj",
              BASE / "native" / "DeckExport" / "DeckExport.csproj"):
        if p.exists():
            return p
    return None


def build_native():
    """构建链: 补丁版 HearthMirror 源码 -> DLL -> DeckExport -> 部署到 native/bin; 成功返回 True"""
    if os.name != "nt":
        print("hs collection 仅支持 Windows")
        return False
    if not MIRROR_PROJ.exists():
        print(f"缺少补丁版 HearthMirror 源码缓存: {MIRROR_PROJ}")
        print("说明:")
        print("  - 上游 HearthMirror_Decompiled 仓库的源码无法直接编译, 需自行修复编译错误后放到上述路径")
        print("  - 缓存缺失时, 可从其他装好本工具的机器复制整个 native/src 目录")
        return False
    dotnet = _find_dotnet()
    if not dotnet:
        print("找不到 dotnet SDK (需要 9.0), 先安装: winget install Microsoft.DotNet.SDK.9")
        return False
    if not SCRY_DLL.exists():
        print(f"缺少依赖 {SCRY_DLL} (HearthMirror 的运行时依赖), 请先补齐")
        return False
    NATIVE_BIN.mkdir(parents=True, exist_ok=True)

    def run(cmd, cwd=None):
        print("  $ " + " ".join(str(c) for c in cmd))
        r = subprocess.run([str(c) for c in cmd], capture_output=True, **({"cwd": str(cwd)} if cwd else {}))
        if r.returncode != 0:
            print("  构建失败:")
            print((r.stderr or r.stdout or b"").decode("utf-8", "replace")[-2000:])
            return False
        return True

    print("[1/4] 构建 HearthMirror (补丁版源码缓存)...")
    if not run([dotnet, "build", MIRROR_PROJ, "-c", "Release"]):
        return False
    mirror_dll = MIRROR_PROJ.parent / "bin" / "Release" / "net9.0" / "HearthMirror.dll"
    if not mirror_dll.exists():
        print(f"  构建产物缺失: {mirror_dll}")
        return False
    shutil.copy2(mirror_dll, NATIVE_BIN / "HearthMirror.dll")
    print(f"  OK -> {NATIVE_BIN / 'HearthMirror.dll'}")

    proj = _deckexport_proj()
    if not proj:
        print("找不到 DeckExport.csproj (仓库 native/DeckExport 下), 无法构建读取器")
        return False
    print("[2/4] 构建 DeckExport ...")
    if not run([dotnet, "build", proj, "-c", "Release", f"-p:HearthMirrorDll={NATIVE_BIN / 'HearthMirror.dll'}"],
               cwd=proj.parent):
        return False
    out_dir = proj.parent / "bin" / "Release" / "net9.0"
    if not (out_dir / "DeckExport.exe").exists():
        print(f"  构建产物缺失: {out_dir / 'DeckExport.exe'}")
        return False

    print("[3/4] 部署产物到 native/bin ...")
    n = 0
    for f in sorted(out_dir.iterdir()):
        if f.is_file():
            shutil.copy2(f, NATIVE_BIN / f.name)
            n += 1
    print(f"  OK, 复制 {n} 个文件 -> {NATIVE_BIN}")

    print("[4/4] 校验产物...")
    if not EXPORT_EXE.exists():
        print(f"  异常: {EXPORT_EXE} 不存在")
        return False
    print("构建完成, 产物清单:")
    for f in sorted(NATIVE_BIN.iterdir()):
        if f.is_file():
            print(f"  {f.name}")
    return True


def _ensure_reader():
    """DeckExport.exe 就位; 缺失时能建则自动建, 不能建打印指引并退出"""
    if EXPORT_EXE.exists():
        return
    if MIRROR_PROJ.exists():
        print("DeckExport 读取器不存在, 自动构建中...")
        if build_native():
            return
        sys.exit("自动构建失败, 运行 hs collection build 查看详情")
    sys.exit(f"缺少 DeckExport 读取器且没有源码缓存, 无法自动构建:\n"
             f"  {MIRROR_PROJ}\n"
             f"手动准备:\n"
             f"  1. dotnet SDK 9: winget install Microsoft.DotNet.SDK.9\n"
             f"  2. 补丁版 HearthMirror 源码 (上游 HearthMirror_Decompiled 需自行修复编译错误),\n"
             f"     放到上述路径后运行 hs collection build")


# ---------- 导出处理 (export 与 worker 共用) ----------

def run_export(timeout=30):
    """运行 DeckExport.exe -> (status, decks); status: ok|no_process|no_data|timeout|bad_output|error:*"""
    kw = {"capture_output": True, "timeout": timeout}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        r = subprocess.run([str(EXPORT_EXE)], **kw)
    except subprocess.TimeoutExpired:
        return "timeout", None
    except OSError as e:
        return f"error: {e}", None
    out = (r.stdout or b"").decode("utf-8-sig", "replace").strip()
    try:
        data = json.loads(out.splitlines()[-1] if out else "{}")
    except Exception:
        return "bad_output", None
    return data.get("status", "bad_output"), data.get("decks")


def _id_maps():
    """cardId -> dbfId 映射 + dbfId -> 卡牌信息 (collectible 库为主, 全量库补英雄皮肤等)"""
    id2dbf, dbf2info = {}, {}
    for c in load_db():
        cid = c.get("id")
        if cid:
            id2dbf.setdefault(cid, c["dbfId"])
        dbf2info.setdefault(c["dbfId"], c)
    try:
        raw = json.loads(DB_FULL_PATH.read_text(encoding="utf-8")) if DB_FULL_PATH.exists() else []
    except Exception:
        raw = []
    for c in raw:
        cid, dbf = c.get("id"), c.get("dbfId")
        if cid and cid not in id2dbf:
            id2dbf[cid] = dbf
        if dbf is not None and dbf not in dbf2info:
            dbf2info[dbf] = c
    return id2dbf, dbf2info


def _safe_name(name):
    """卡组名 -> 存档文件名: Windows 非法字符与首尾空格/点替换删除, 空名兜底 unnamed"""
    s = re.sub(r'[\\/:*?"<>|]', "_", (name or "").strip()).strip(" .")
    return s or "unnamed"


MANIFEST_PATH = BASE / "sync_manifest.json"   # 历史所有同步写过的存档文件名清单 (删除同步的依据)


def _load_manifest():
    try:
        return set(json.loads(MANIFEST_PATH.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _save_manifest(names):
    MANIFEST_PATH.write_text(json.dumps(sorted(names), ensure_ascii=False, indent=2), "utf-8")


def process_decks(decks, write=True, sync_delete=False):
    """镜像 decks JSON -> 逐卡组编码并存档; 返回 {results, warnings, skipped, deleted}。

    results: [{"name","file","status"}], status: new|update|same (对照已存档的 code);
    warnings: 卡组已导出但副牌被丢弃等; skipped: ["名字 (原因)"];
    deleted: sync_delete 开启时被删除的存档路径列表 (仅删清单里登记过的同步产物)。
    删除基准 = 本次 plan 全集 (含 skipped: 数据问题不等于游戏内删除, 不误删)。"""
    id2dbf, dbf2info = _id_maps()
    ts = time.strftime("%Y-%m-%d %H:%M")

    # 同名卡组 (游戏允许多槽同名): 按 deckId 升序, 第 1 个用裸名, 之后加 -<deckId后4位>, 命名稳定可复现
    groups = defaultdict(list)
    for d in decks:
        groups[_safe_name(d.get("name"))].append(d)
    plan = []
    for lst in groups.values():
        lst.sort(key=lambda d: d.get("id") or 0)
        for i, d in enumerate(lst):
            fname = _safe_name(d.get("name")) if i == 0 else f"{_safe_name(d.get('name'))}-{str(d.get('id') or 0)[-4:]}"
            plan.append((d, fname))

    def rows(pairs):
        def cost(x):
            v = dbf2info.get(x[0], {}).get("cost")
            return v if isinstance(v, int) else 99
        out = []
        for d_, c_ in sorted(pairs, key=lambda x: (cost(x), dbf2info.get(x[0], {}).get("name", ""))):
            info = dbf2info.get(d_, {})
            out.append({"dbfId": d_, "name": info.get("name", "???"), "count": c_,
                        "cost": info.get("cost", 0), "type": info.get("type", ""),
                        "rarity": info.get("rarity", ""), "set": info.get("set", "")})
        return out

    results, warnings, skipped, deleted = [], [], [], []
    plan_fnames = {fname for _, fname in plan}
    old_manifest = _load_manifest()
    gone = old_manifest - plan_fnames          # 曾同步写入、游戏里已不存在的存档
    if write:
        if sync_delete:
            for stale in sorted(gone):
                p = DECKS_DIR / f"{stale}.json"
                try:
                    p.unlink()
                    deleted.append(str(p))
                except OSError:
                    pass
            _save_manifest(plan_fnames)  # 已删的移出清单, 保留现存
        else:
            _save_manifest(old_manifest | plan_fnames)  # 只追加: 关闭删除期间消失的, 开启后仍可识别
    for d, fname in plan:
        name = d.get("name") or "unnamed"
        fmt_num = d.get("formatType")
        if fmt_num not in FMT_NAMES:
            skipped.append(f"{name} (格式 formatType={fmt_num} 非标准/狂野)")
            continue
        hero_dbf = id2dbf.get(d.get("hero"))
        if not hero_dbf:
            skipped.append(f"{name} (英雄 {d.get('hero')} 不在卡牌库)")
            continue
        cards, bad = [], None
        for c in d.get("cards") or []:
            dbf = id2dbf.get(c.get("id"))
            if not dbf:
                bad = c.get("id")
                break
            cards.append((dbf, int(c.get("count") or 1)))
        if bad:
            skipped.append(f"{name} (卡牌 {bad} 不在卡牌库, 先跑 hs update)")
            continue

        sb, bad_owner = [], None
        for owner_id, lst in (d.get("sideboards") or {}).items():
            odbf = id2dbf.get(owner_id)
            if not odbf:
                bad_owner = owner_id
                break
            for c in lst:
                dbf = id2dbf.get(c.get("id"))
                if not dbf:
                    bad_owner = owner_id
                    break
                sb.append((dbf, int(c.get("count") or 1), odbf))
        if bad_owner:
            warnings.append(f"{name} 副牌 owner {bad_owner} 不在卡牌库, 已丢弃副牌 ({sum(c for _, c, _ in sb)} 张)")

        hero_info = dbf2info.get(hero_dbf, {})
        arch = {
            "name": name, "code": encode_deck(hero_dbf, fmt_num, cards, sb),
            "format": FMT_NAMES[fmt_num],
            "hero": hero_info.get("name", ""), "hero_dbfId": hero_dbf,
            "class": hero_info.get("cardClass", ""),
            "cards": rows(cards), "source": f"游戏同步 {ts}", "saved_at": ts,
        }
        if sb:
            arch["sideboard"] = {"owner": dbf2info.get(sb[0][2], {}).get("name", ""),
                                 "cards": rows([(a, b) for a, b, _ in sb])}

        DECKS_DIR.mkdir(parents=True, exist_ok=True)
        p = DECKS_DIR / f"{fname}.json"
        try:
            old = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            old = None
        status = "new" if not old else ("same" if old.get("code") == arch["code"] else "update")
        if write and status != "same":
            p.write_text(json.dumps(arch, ensure_ascii=False, indent=2), encoding="utf-8")
        results.append({"name": name, "file": str(p), "status": status})
    return {"results": results, "warnings": warnings, "skipped": skipped, "deleted": deleted}


def _collection_export(argv):
    o = _parse_opts(argv, {"config"}, {"check"})
    if os.name != "nt":
        sys.exit("hs collection 仅支持 Windows")
    _ensure_reader()
    status, decks = run_export()
    if status == "no_process":
        print("炉石未运行 — 先启动炉石客户端进入主界面后再试")
        sys.exit(2)
    if status == "no_data":
        print("读不到卡组数据 — 游戏可能在加载中, 稍后再试")
        sys.exit(3)
    if status == "timeout":
        sys.exit("DeckExport 运行超时 (30s), 稍后再试")
    if status != "ok" or decks is None:
        sys.exit(f"DeckExport 输出异常 (status={status})")

    check = bool(o.get("check"))
    sync_delete = bool(config.load(config.path_for(argv), "collection").get("sync_delete"))
    res = process_decks(decks, write=not check, sync_delete=sync_delete and not check)
    tag = "预览 (--check, 不写存档)" if check else "导出完成"
    print(f"{tag}: 共 {len(decks)} 个卡组, 匹配 {len(res['results'])} 个, 跳过 {len(res['skipped'])} 个"
          + (f", 删除 {len(res['deleted'])} 个" if res["deleted"] else ""))
    for r in res["results"]:
        print(f"  [{STATUS_ZH[r['status']]}] {r['name']} -> {r['file']}")
    for f in res["deleted"]:
        print(f"  [删除] {f}")
    for w in res["warnings"]:
        print(f"  [警告] {w}")
    for s in res["skipped"]:
        print(f"  [跳过] {s}")


# ---------- worker 主循环 (照 watch_worker.tail_loop: 绝不崩溃) ----------

def worker_main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cfg_path = config.path_for(argv)
    cfg = config.load(cfg_path, "collection")
    interval = max(2, int(cfg.get("interval") or 5))
    sync_delete = bool(cfg.get("sync_delete"))
    log_path = cfg_path.parent / "collection.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logfh = open(log_path, "a", encoding="utf-8")

    def log(tag, msg):
        try:
            logfh.write(f"[{tag}] {msg}\n")
            logfh.flush()
        except Exception:
            pass

    if not EXPORT_EXE.exists():
        log("INFO", "DeckExport 读取器缺失, 尝试自动构建...")
        if not build_native():
            log("ERROR", "自动构建失败, worker 退出 (运行 hs collection build 查看详情)")
            return
    log("INFO", f"collection worker 启动 pid={os.getpid()} interval={interval}s "
                f"sync_delete={'on' if sync_delete else 'off'} exe={EXPORT_EXE}")

    last_hash = None  # 上次导出的卡组快照 (json hash), 有变化才写存档
    while True:
        try:
            status, decks = run_export()
            if status == "ok" and decks is not None:
                h = hashlib.sha256(
                    json.dumps(decks, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
                if h != last_hash:
                    last_hash = h
                    res = process_decks(decks, write=True, sync_delete=sync_delete)
                    for r in res["results"]:
                        log("CHANGE", f"{r['name']} [{STATUS_ZH[r['status']]}] -> {r['file']}")
                    for f in res["deleted"]:
                        log("DELETE", f)
                    for w in res["warnings"]:
                        log("WARN", w)
                    for s in res["skipped"]:
                        log("INFO", f"跳过 {s}")
            # no_process / no_data: 炉石未开或加载中, 静默等待
        except Exception as e:  # 绝不崩溃, 记日志继续跑
            log("ERROR", f"主循环异常(已恢复): {type(e).__name__}: {e}")
        time.sleep(interval)


# ---------- hs collection watch 命令入口 ----------

def _watch_start(argv):
    o = _parse_opts(argv, {"config"}, {"force"})
    cfg_path = config.path_for(argv)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg = config.load(cfg_path, "collection")
    # 配置文件是用户的地盘, start 不写回; 缺省值只在内存兜底, 供展示
    try:
        interval = max(2, int(cfg.get("interval") or 5))
    except (TypeError, ValueError):
        interval = 5
    sync_delete = bool(cfg.get("sync_delete"))

    pid_path = cfg_path.parent / "collection.pid"
    pid = _read_pid(pid_path)
    if pid and pid_alive(pid):
        if o.get("force"):
            kill_pid(pid)
            pid_path.unlink(missing_ok=True)
            print(f"已强制停止旧进程 (PID {pid})")
        else:
            sys.exit(f"卡组同步进程已在运行 (PID {pid}), 先执行 hs collection watch stop 或加 --force")
    elif pid:
        pid_path.unlink(missing_ok=True)

    if os.name != "nt":
        sys.exit("hs collection 仅支持 Windows")
    _ensure_reader()

    log_path = cfg_path.parent / "collection.log"
    creation = 0x00000008 | 0x08000000 if os.name == "nt" else 0  # DETACHED_PROCESS | CREATE_NO_WINDOW
    with open(log_path, "ab") as lf:
        proc = subprocess.Popen(
            [sys.executable, "-m", "hearthstone_cli.collection", "--worker", f"--config={cfg_path}"],
            stdin=subprocess.DEVNULL, stdout=lf, stderr=lf,
            creationflags=creation, close_fds=True)
    time.sleep(0.8)
    running = pid_alive(proc.pid)
    pid_path.write_text(str(proc.pid), "utf-8")
    if not running:
        print(f"警告: worker 启动后立即退出, 详见 {log_path}")
    print("卡组同步监听已启动" if running else "卡组同步监听启动异常")
    print(f"  PID      : {proc.pid}")
    print(f"  配置文件 : {cfg_path}")
    print(f"  轮询间隔 : {interval} 秒")
    print(f"  删除同步 : {'开启' if sync_delete else '关闭 (游戏内删除的卡组本地保留)'}")
    print(f"  读取器   : {EXPORT_EXE}")
    print(f"  日志     : {log_path}")


def _watch_stop(argv):
    o = _parse_opts(argv, {"config"}, set())
    cfg_path = config.path_for(argv)
    pid_path = cfg_path.parent / "collection.pid"
    pid = _read_pid(pid_path)
    if pid and pid_alive(pid):
        ok = kill_pid(pid)
        pid_path.unlink(missing_ok=True)
        print(f"已停止 (PID {pid})" if ok else f"停止失败 (PID {pid}), 请手动 taskkill /PID {pid} /F")
    else:
        pid_path.unlink(missing_ok=True)
        print("本来就没在跑")


def _tail_events(log_path, n):
    try:
        data = log_path.read_bytes()[-65536:]
    except OSError:
        return []
    lines = data.decode("utf-8", "replace").splitlines()
    return [l for l in lines if l.startswith(("[CHANGE]", "[DELETE]", "[ERROR]", "[WARN]"))][-n:]


def _watch_status(argv):
    o = _parse_opts(argv, {"config", "events"}, set())
    cfg_path = config.path_for(argv)
    cfg = config.load(cfg_path, "collection")
    pid_path = cfg_path.parent / "collection.pid"
    pid = _read_pid(pid_path)
    if pid and pid_alive(pid):
        print(f"状态: 运行中 (PID {pid})")
    else:
        print("状态: 已停止" + (f" (残留 PID 文件 {pid}, 进程已不在)" if pid else ""))
    print(f"  配置文件 : {cfg_path}")
    print(f"  轮询间隔 : {cfg.get('interval', 5)} 秒")
    print(f"  删除同步 : {'开启' if cfg.get('sync_delete') else '关闭'}")
    print(f"  读取器   : {EXPORT_EXE}" + ("" if EXPORT_EXE.exists() else " (缺失)"))
    try:
        n = int(o.get("events", "5"))
    except ValueError:
        n = 5
    events = _tail_events(cfg_path.parent / "collection.log", n)
    print(f"最近同步事件 ({len(events)} 条):")
    for e in events:
        print("  " + e)


def cmd_collection(args):
    sub = args[0] if args else ""
    if sub == "export":
        _collection_export(args[1:])
    elif sub == "build":
        if not build_native():
            sys.exit(1)
    elif sub == "watch":
        w = args[1] if len(args) > 1 else ""
        if w == "start":
            _watch_start(args[2:])
        elif w == "stop":
            _watch_stop(args[2:])
        elif w == "status":
            _watch_status(args[2:])
        else:
            print(USAGE)
            sys.exit(0 if not args[1:] else 1)
    else:
        print(USAGE)
        sys.exit(0 if not args else 1)


if __name__ == "__main__":
    if "--worker" in sys.argv:
        worker_main()
    else:
        cmd_collection(sys.argv[1:])
