"""hs watch — 军师监听守护进程: tail Power.log, 回合/换牌触发时 POST 提示词到 IM 桥接器

用法:
  hs watch start [--config=配置路径] [--force]
  hs watch stop   [--config=路径]
  hs watch status [--config=路径] [--events=N]

所有配置统一在 ~/.hearthstone-cli/config.json 的 watch 节
(channel_id/url/token/log/mulligan_prompt/turn_prompt), CLI 不提供配置参数,
编辑文件后 start 生效; --config= 可指定其他配置路径。
watch.log / watch.pid 与配置文件同目录 (便于 --config 测试隔离)。
worker 本体: `python -m hearthstone_cli.watch_worker --config=...`, 标准库 only。
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from hearthstone_cli import board, config

DEFAULT_URL = "http://127.0.0.1:8088"
DEFAULT_CHANNEL = "1477362651859255326"
DEFAULT_TOKEN_ENV = "HS_WATCH_TOKEN"
DEFAULT_MULLIGAN_PROMPT = "换牌阶段开始了，手牌已经亮出来了，帮我看看怎么留牌"
DEFAULT_TURN_PROMPT = "轮到我的回合了，看看局面给我出个主意"
POST_SOURCE = "hs-watch"
POST_RETRIES = 3
POST_RETRY_WAIT = 2
BRIDGE_PATH = "/api/external/message"
# 触发词: 行内出现即唤醒解析 (轻量预筛, 真正判定靠 board.parse_power_log 全量重放)
TRIGGERS = ("CURRENT_PLAYER", "MULLIGAN_STATE")

USAGE = """用法: hs watch start [--config=路径] [--force]
      hs watch stop   [--config=路径]
      hs watch status [--config=路径] [--events=N]

配置: 编辑 ~/.hearthstone-cli/config.json 的 watch 节
      (channel_id/url/token/log/mulligan_prompt/turn_prompt), 编辑后 start 生效。
--log 支持三种: "auto"(默认, 自动发现最新 Hearthstone_*/Power.log)、日志目录、具体 Power.log 文件路径。
监听炉石 Power.log, 检测到换牌阶段/轮到我方回合时向 IM 桥接器 POST 提示词触发军师分析。
token 也可用环境变量 HS_WATCH_TOKEN; 同目录生成 watch.log(运行日志) 与 watch.pid。"""


# ---------- 配置 / PID ----------

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


# ---------- 分析与 POST ----------

def analyze(lines):
    """全量重放日志 -> {start_line, turn, phase}; phase: mulligan|turn|over|None(无需 POST)"""
    game, start_line, total = board.parse_power_log(lines)
    if not game or not game["entities"]:
        return None
    me, _opp, unknown = board.detect_me(game)
    if me is None or board._playstate(me) != "PLAYING":
        return None  # 无法判定我方 / 非进行中对局 (匹配等待/选牌界面等) 一律不触发
    mulligan = board.is_mulligan(game)
    over = board.is_game_over(game)
    g_ent = board._game_entity(game)
    turn = board._tag_int(g_ent, "TURN") if g_ent else 0
    cur = next((p for p in board._players(game) if board._tag_int(p, "CURRENT_PLAYER") == 1), None)
    my_turn = cur is me
    mulligan_done = board._norm(me["tags"].get("MULLIGAN_STATE", ""), board.MULLIGAN_BY_NUM) == "DONE"
    heroes_in_play = sum(1 for e in game["entities"].values()
                         if board._ctype(e) == "HERO" and e["zone"] == "PLAY")
    if over:
        phase = "over"
    elif mulligan and not unknown:  # 换牌阶段且我方手牌已亮
        phase = "mulligan"
    elif my_turn and not mulligan and mulligan_done and heroes_in_play >= 2:
        # 正式回合: 我方换牌已结束 + 双方英雄已上场 (排除匹配等待期的预备数据)
        phase = "turn"
    else:
        phase = None
    return {"start_line": start_line, "turn": turn, "phase": phase, "total": total}


def post_message(url, token, channel_id, content, timeout=10):
    body = json.dumps({"channel_id": str(channel_id), "content": content, "source": POST_SOURCE}).encode("utf-8")
    req = urllib.request.Request(
        url.rstrip("/") + BRIDGE_PATH, data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token or ''}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def post_retry(url, token, channel_id, content, log):
    last = ""
    for i in range(1, POST_RETRIES + 1):
        ok, last = post_message(url, token, channel_id, content)
        if ok:
            return True, last
        if i < POST_RETRIES:
            log("WARN", f"POST 失败({i}/{POST_RETRIES}) {last}, {POST_RETRY_WAIT}s 后重试")
            time.sleep(POST_RETRY_WAIT)
    return False, last


# ---------- worker 主循环 ----------

def _drain(f):
    """读完当前已缓冲的行 (同一批触发行只睡一次)"""
    while f.readline():
        pass


def _handle(ctx):
    """触发后: 全量重放 -> 防抖 -> POST"""
    lines = ctx["power_path"].read_text("utf-8", errors="replace").splitlines()
    info = analyze(lines)
    if not info or info["phase"] is None:
        return
    key = (info["start_line"], info["turn"], info["phase"])
    if info["start_line"] != ctx["last_start"]:  # 新对局 -> 重置防抖表
        ctx["seen"].clear()
        ctx["last_start"] = info["start_line"]
    if key in ctx["seen"]:
        return
    ctx["seen"].add(key)
    kind = "mulligan" if info["phase"] == "mulligan" else "turn"
    content = ctx["mulligan_prompt"] if kind == "mulligan" else ctx["turn_prompt"]
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    if not ctx["token"]:
        ctx["log"]("ERROR", f"{ts} kind={kind} key={key} -> token 为空, 无法 POST (配置文件 watch 节 token 或环境变量 {DEFAULT_TOKEN_ENV})")
        return
    ok, detail = post_retry(ctx["url"], ctx["token"], ctx["channel_id"], content, ctx["log"])
    if ok:
        ctx["log"]("EVENT", f"{ts} kind={kind} turn={info['turn']} key={key} -> POSTED {detail}")
    else:
        ctx["log"]("ERROR", f"{ts} kind={kind} turn={info['turn']} key={key} -> POST 失败 {detail}")


def tail_loop(power_path, ctx):
    """auto_dir 模式: None=固定文件; "auto"=扫描默认候选目录; Path=扫描指定目录。

    目录模式下每次轮询重新发现最新 Hearthstone_*/Power.log, 切换目标时重置防抖并从头读
    (新文件错过了开头就无法重放对局)。"""
    auto_dir = ctx.get("auto_dir")
    f, last_ino, last_size = None, None, -1
    while True:
        try:
            if auto_dir is not None:
                latest = board.find_latest_power(auto_dir if isinstance(auto_dir, Path) else None)
                if latest != power_path:
                    power_path = latest
                    ctx["power_path"] = latest  # _handle 读的是 ctx, 切换必须同步
                    if f:
                        f.close()
                        f = None
                    ctx["seen"].clear()
                    ctx["last_start"] = None
                    if power_path:
                        ctx["log"]("INFO", f"目标切换为 {power_path}")
            if power_path is None or not power_path.exists():  # 炉石未开, 静默等待
                if f:
                    f.close()
                    f = None
                time.sleep(2)
                continue
            st = power_path.stat()
            if f is None:
                f = open(power_path, "r", encoding="utf-8", errors="replace")
                if auto_dir is not None and time.time() - st.st_mtime <= 60:
                    # 目录模式且文件新鲜 (本局刚生成): 从头读全量, 换牌阶段在文件开头不能错过
                    f.seek(0)
                    ctx["log"]("INFO", f"开始监听 {power_path} (新文件从头读全量, size={st.st_size})")
                else:
                    # 固定文件模式首次, 或目录模式发现的是上一场残留的旧日志: 只看新增
                    f.seek(0, os.SEEK_END)
                    ctx["log"]("INFO", f"开始监听 {power_path} (seek 末尾, size={st.st_size})")
                last_ino, last_size = st.st_ino, st.st_size
            elif st.st_ino != last_ino or st.st_size < last_size:
                # 炉石重启: 日志清空重写 -> 重置 tail 与防抖状态
                f.close()
                f = open(power_path, "r", encoding="utf-8", errors="replace")
                f.seek(0)
                last_ino, last_size = st.st_ino, st.st_size
                ctx["seen"].clear()
                ctx["last_start"] = None
                ctx["log"]("INFO", "检测到日志清空/重建, 已重置监听与防抖状态")
            else:
                last_size = st.st_size
            line = f.readline()
            if not line:
                time.sleep(0.5)
                continue
            if any(t in line for t in TRIGGERS):
                _drain(f)
                time.sleep(2.5)  # 等日志落盘完整再解析 (回合开始的 RESOURCES/TURN 与 CURRENT_PLAYER 分批写入)
                _handle(ctx)
        except Exception as e:  # 绝不崩溃, 记日志继续跑
            if f:
                try:
                    f.close()
                except Exception:
                    pass
                f = None
            ctx["log"]("ERROR", f"主循环异常(已恢复监听): {type(e).__name__}: {e}")
            time.sleep(2)


def worker_main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cfg_path = config.path_for(argv)
    cfg = config.load(cfg_path, "watch")
    log_path = cfg_path.parent / "watch.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logfh = open(log_path, "a", encoding="utf-8")

    def log(tag, msg):
        try:
            logfh.write(f"[{tag}] {msg}\n")
            logfh.flush()
        except Exception:
            pass

    token = (cfg.get("token") or os.environ.get(DEFAULT_TOKEN_ENV) or "").strip()
    log_setting = (cfg.get("log") or "").strip()
    if log_setting.lower().endswith(".log"):
        power, auto_dir = Path(log_setting).expanduser(), None  # 显式文件模式
    elif log_setting and log_setting != "auto":
        power, auto_dir = None, Path(log_setting).expanduser()  # 指定目录模式
    else:
        power, auto_dir = None, "auto"  # 默认候选目录模式
    if power is None:
        power = board.find_latest_power(auto_dir if isinstance(auto_dir, Path) else None) or board.DEFAULT_LOG
    mode_txt = {"auto": "自动发现(默认候选)", str(auto_dir): f"目录发现({auto_dir})"}.get(
        str(auto_dir), "") or "固定文件"
    ctx = {
        "power_path": power,
        "auto_dir": auto_dir,
        "url": cfg.get("url") or DEFAULT_URL,
        "channel_id": cfg.get("channel_id") or DEFAULT_CHANNEL,
        "token": token,
        "mulligan_prompt": cfg.get("mulligan_prompt") or DEFAULT_MULLIGAN_PROMPT,
        "turn_prompt": cfg.get("turn_prompt") or DEFAULT_TURN_PROMPT,
        "seen": set(),       # 防抖表: {(CREATE_GAME起始行, TURN, 阶段)}
        "last_start": None,  # 当前对局起始行
        "log": log,
    }
    log("INFO", f"watch_worker 启动 pid={os.getpid()} mode={mode_txt} log={power} "
                f"url={ctx['url']} token={'有' if token else '空'}")
    tail_loop(power, ctx)


if __name__ == "__main__":
    worker_main()


# ---------- hs watch 命令入口 ----------

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


def _mask(tok):
    if not tok:
        return "（空）"
    return (tok[:4] + "****") if len(tok) > 8 else "****"


def _log_desc(cfg):
    v = (cfg.get("log") or "").strip()
    if v == "auto":
        return f"自动发现 (默认候选: {'; '.join(str(d) for d in board.DEFAULT_LOG_DIRS)})"
    if v and not v.lower().endswith(".log"):
        return f"目录发现 ({v})"
    return v or str(board.DEFAULT_LOG)


def _print_summary(cfg, pid, cfg_path):
    print(f"  PID       : {pid}")
    print(f"  配置文件  : {cfg_path}")
    print(f"  桥接地址  : {cfg.get('url') or DEFAULT_URL}")
    print(f"  频道 ID   : {cfg.get('channel_id') or DEFAULT_CHANNEL}")
    print(f"  Token     : {_mask(cfg.get('token'))}")
    print(f"  监听日志  : {_log_desc(cfg)}")
    print(f"  换牌提示词: {cfg.get('mulligan_prompt') or DEFAULT_MULLIGAN_PROMPT}")
    print(f"  回合提示词: {cfg.get('turn_prompt') or DEFAULT_TURN_PROMPT}")


def _watch_start(argv):
    o = _parse_opts(argv, {"config"}, {"force"})
    cfg_path = config.path_for(argv)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg = config.load(cfg_path, "watch")
    # 配置文件是用户的地盘, start 不写回; log 缺省只在内存兜底 (自动发现最新日志), 供展示与传参
    cfg.setdefault("log", "auto")

    pid_path = cfg_path.parent / "watch.pid"
    pid = _read_pid(pid_path)
    if pid and pid_alive(pid):
        if o.get("force"):
            kill_pid(pid)
            pid_path.unlink(missing_ok=True)
            print(f"已强制停止旧进程 (PID {pid})")
        else:
            sys.exit(f"监听进程已在运行 (PID {pid}), 先执行 hs watch stop 或加 --force")
    elif pid:
        pid_path.unlink(missing_ok=True)

    log_path = cfg_path.parent / "watch.log"
    creation = 0x00000008 | 0x08000000 if os.name == "nt" else 0  # DETACHED_PROCESS | CREATE_NO_WINDOW
    with open(log_path, "ab") as lf:
        proc = subprocess.Popen(
            [sys.executable, "-m", "hearthstone_cli.watch_worker", f"--config={cfg_path}"],
            stdin=subprocess.DEVNULL, stdout=lf, stderr=lf,
            creationflags=creation, close_fds=True)
    time.sleep(0.8)
    running = pid_alive(proc.pid)
    pid_path.write_text(str(proc.pid), "utf-8")
    if not running:
        print(f"警告: worker 启动后立即退出, 详见 {log_path}")
    print("军师监听已启动" if running else "军师监听启动异常")
    _print_summary(cfg, proc.pid, cfg_path)
    if not (cfg.get("token") or os.environ.get(DEFAULT_TOKEN_ENV)):
        print(f"提示: token 为空, 检测可用但不会 POST (在配置文件 watch 节填 token 或设环境变量 {DEFAULT_TOKEN_ENV})")


def _watch_stop(argv):
    o = _parse_opts(argv, {"config"}, set())
    cfg_path = config.path_for(argv)
    pid_path = cfg_path.parent / "watch.pid"
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
    return [l for l in lines if l.startswith("[EVENT]")][-n:]


def _watch_status(argv):
    o = _parse_opts(argv, {"config", "events"}, set())
    cfg_path = config.path_for(argv)
    cfg = config.load(cfg_path, "watch")
    pid_path = cfg_path.parent / "watch.pid"
    pid = _read_pid(pid_path)
    if pid and pid_alive(pid):
        print(f"状态: 运行中 (PID {pid})")
    else:
        print("状态: 已停止" + (f" (残留 PID 文件 {pid}, 进程已不在)" if pid else ""))
    if cfg:
        _print_summary(cfg, pid or "-", cfg_path)
    try:
        n = int(o.get("events", "5"))
    except ValueError:
        n = 5
    events = _tail_events(cfg_path.parent / "watch.log", n)
    print(f"最近触发事件 ({len(events)} 条):")
    for e in events:
        print("  " + e)


def cmd_watch(args):
    sub = args[0] if args else ""
    if sub == "start":
        _watch_start(args[1:])
    elif sub == "stop":
        _watch_stop(args[1:])
    elif sub == "status":
        _watch_status(args[1:])
    else:
        print(USAGE)
        sys.exit(0 if not args else 1)
