"""统一配置 — hs watch 与 hs collection 共用一个 config.json, 按节管理

结构 (两节互不影响, merge 保存):
  {
    "watch":      {channel_id, url, token, log, mulligan_prompt, turn_prompt},
    "collection": {interval, sync_delete}
  }

legacy 迁移: config.json 不存在或对应节缺失时, 尝试读同目录旧文件
watch_config.json / collection_config.json, 其平铺内容作为该节初始值并写回
统一文件 (旧文件保留不删); 都没有则返回空 dict, 任何异常吞掉返回空 dict。
标准库 only。
"""
import json
from pathlib import Path

from hearthstone_cli.deck import BASE  # 数据根目录 (环境变量 HS_DECK_HOME 可覆盖)

DEFAULT_PATH = BASE / "config.json"


def path_for(argv):
    """扫描 argv 里的 --config=路径, 无则默认 ~/.hearthstone-cli/config.json"""
    for a in argv:
        if a.startswith("--config="):
            return Path(a.split("=", 1)[1]).expanduser()
    return DEFAULT_PATH


def _read(p):
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        return {}


def load(path, section):
    """读统一配置的 section 节返回 dict; 节缺失时从旧 {section}_config.json 迁移"""
    data = _read(path)
    if not isinstance(data, dict):
        data = {}
    if isinstance(data.get(section), dict):
        return data[section]
    legacy = _read(path.parent / f"{section}_config.json")  # 旧平铺配置文件
    if not isinstance(legacy, dict) or not legacy:
        return {}
    save(path, section, legacy)  # 首次使用自动迁移进统一文件 (旧文件保留)
    return legacy


def save(path, section, data):
    """merge 写回 section 节 (保留其他节), 父目录不存在则创建"""
    full = _read(path)
    if not isinstance(full, dict):
        full = {}
    full[section] = data
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(full, ensure_ascii=False, indent=2), "utf-8")
