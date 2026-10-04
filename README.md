# hearthstone-cli — Hearthstone CLI Toolbox for AI Agents

**面向 AI Agent 的炉石传说命令行工具箱，双核心：组卡（校验 / 编解码 / 筛卡 / 卡组库存档 / 版本体检 / 长图）+ 对局分析（解析客户端日志输出实时对局面板与战况回放，监听对局触发 AI 军师）。**

A command-line toolbox for Hearthstone designed for AI agents, with two cores: deck building (validation, encoding, decoding, filtering, archiving, deck images) and match analysis (real-time board state & action replay parsed from client logs, plus an AI-counselor watcher).

[English](README_EN.md) | [简体中文](README.md)

---

人类的组卡模拟器解决的是"可视化拖卡"，而 Agent 组卡需要的是秒级试错：列 30 张卡 → 校验 → 出卡组代码 → 按报错修正 → 再来一轮。对局中 Agent 需要的则是结构化的完整战况：不是看一张截图，而是拿到双方状态、场面、手牌与逐回合行动的可解析文本。这两个问题，这个工具各给了一个答案。

## 功能特性

- **卡组代码编解码** — 完整支持炉石 deckstring 格式（含副牌库三元组），并对营地等平台在标准代码后附加的扩展字节做了容错
- **构筑规则校验** — 数量、同名限量（普通 2 张 / 传说 1 张）、职业限定、标准池白名单
- **动态卡组容量** — 裂魂者阿扎莉娜（套牌 20 张）、时空大盗拉法姆（40 张且恰含 10 张拉法姆）、常规 30 张
- **副牌库** — 乐队经理精英牛头人酋长的 3 张乐队，编码为 sideboard 三元组
- **多职业与游客机制** — 按 `classes` 数组识别多职业卡（如六职业共用的灭世者死亡之翼），并完整实现胜地历险记游客三条规则：游客仅解锁目的地职业的该扩展卡牌、每套限一名游客、不可嵌套
- **卡组库与版本体检** — 卡组本地存档，版本更新后一键体检，退环境卡逐条列出
- **卡组长图** — 一条命令把卡组渲染成可分享的卡组长图（中/英版各自独立）：法力曲线、稀有度配色（默认逐张列出，`--merge` 合并同名卡）、职业徽记、英雄与卡组代码，2x 渲染输出 1520px 宽，调用本地无头 Chrome/Edge
- **对局面板** — 解析炉石客户端日志 Power.log 全量重放，输出结构化实时面板：对局模式、总手数/回合/当前行动方、双方法力（含过载锁定）与先后手（后手标注硬币）、双方英雄血甲武器技能（含灌注/变形后的技能变更）、场面随从与地标（嘲讽/圣盾/风怒/冻结/休眠等状态标注）、我方手牌费用攻血（含兆示预览与已强化/不可打出标注）、双方牌库剩余/疲劳/尸体数、任务进度（与奥秘分流显示）、终局胜负与结束方式（斩杀/投降/疲劳）
- **战况回放** — 行动回顾事件流按回合重放双方每一步：出牌（我方裸名、对方附效果描述，战吼目标都有）、攻击（附目标与实际伤害）、英雄技能、抽弃牌、换牌保留替换、开局触发效果（如复制传说洗入牌库列全卡名）、亡语与触发结算（召唤带来源、伤害标致命、复生、治疗带来源）、发现/灾变类选择、预备减费、休眠囚禁与苏醒、亡语亮牌（区分已施放/仅亮出）、回合结束获得（带来源与效果描述）、手牌满烧牌（报卡名），同名合并防刷屏；AI 无需查库即可理解新卡
- **军师监听** — `hs watch` 后台监听 Power.log，换牌阶段和轮到我方回合时向自建 IM 桥接器推送固定提示词，触发 AI 军师分析对局
- **卡组收藏同步**（仅 Windows）— `hs collection` 从运行中的游戏内存读取收藏卡组，自动编码为卡组代码并同步到本地卡组库存档（export 手动导出 / watch 常驻监听自动同步）
- **对 Agent 友好** — 纯 JSON 输入、报错逐条列出便于自我修正、无任何交互式提示
- **本地双语卡牌库** — 中英双语卡牌数据源自 [HearthstoneJSON](https://hearthstonejson.com/)，补丁日一条命令刷新

## 持续维护

本项目处于活跃维护状态：炉石每个新版本（扩展包 / 平衡补丁）上线后，会同步更新本地标准卡牌库与标准池白名单，卡组体检随版本跟进。若数据源变更导致问题，欢迎提 issue。

## 安装

要求：Python 3.10+（Windows / macOS / Linux）

`hs image` 另需本机安装 Chrome 或 Edge（自动探测，可用环境变量 `CHROME_PATH` 指定）。

```bash
git clone https://github.com/OstrichHermit/hearthstone-cli.git
cd hearthstone-cli
pip install .
```

开发模式用 `pip install -e .`（改动源码即时生效）。也可直接从 PyPI 安装：`pip install hearthstone-cli`。

数据目录默认 `~/.hearthstone-cli/`（卡牌库、卡组库、卡组图都存这里），可用环境变量 `HS_DECK_HOME` 覆盖。装好后先跑一次 `hs update` 下载卡牌库。

### 安装为 Agent Skill（可选）

仓库内附带 Agent Skill（`skills/hs-deck/SKILL.md`），把它复制到你所用 AI Agent 的 skills 目录，Agent 即可自动掌握本工具的用法。以 Claude Code 为例：

```bash
cp -r skills/hs-deck ~/.claude/skills/hs-deck
```

## 用法

```bash
# 刷新卡牌库（自动下载最新中文+英文 collectible 卡及中文全量库，全量库含英雄技能/token 供对局面板查名与描述）
hs update

# 筛卡
hs filter --class=战士 --set=CORE --cost=<=3 --text=嘲讽

# 解码卡组代码
hs decode AAECAQcGo6AE...

# 校验并输出卡组代码
hs validate deck.json

# 卡组入库（支持代码或网页 URL）
hs save my-deck AAECAQcGo6AE...
hs save from-web https://example.com/deck-page

# 查看卡组库
hs list
hs show my-deck

# 版本更新后体检（省略名字 = 检查全部）
hs check

# 生成卡组长图
hs image my-deck                          # 按卡组库名字
hs image AAECAQcGo6AE... --name=Turtle    # 直接给代码
hs image my-deck --lang=en                # 英文版（--lang=both 一次出中英两版）
hs image my-deck --merge                  # 同名卡合并为一行
hs image my-deck --name=龟甲防战 --name-en=Turtle Warrior

# 解析当前对局面板（默认自动发现最新日志：游戏目录 Logs 下 Hearthstone_* 子目录及标准目录）
hs board
hs board --log=D:\games\Hearthstone\Logs\Power.log   # 指定日志路径（也可指向日志目录自动发现）
hs board --player=鸵鸟居士                            # 自动判定我方不准时手动指定

# 军师监听：换牌阶段/轮到我方回合时，向 IM 桥接器 POST 提示词触发 AI 分析
# 先编辑 ~/.hearthstone-cli/config.json 配好 watch 节（channel_id/url/token 等），再启动
hs watch start                                       # 配置好 config.json 的 watch 节后启动
hs watch status                                      # 查看运行状态与最近触发事件
hs watch stop
```

标准卡组含非标准池卡时默认拦截不出图，`--force` 可强制渲染。

`deck.json` 格式：

```json
{
  "format": "standard",
  "hero": "加尔鲁什·地狱咆哮",
  "cards": { "斩杀": 2, "#69535": 1 },
  "sideboard": { "owner": "乐队经理精英牛头人酋长", "cards": { "蓝鳃战士": 2 } }
}
```

卡名或 `#dbfId` 均可，副牌库可选。

报错逐条输出，Agent 可以按条机械修正：

```
校验失败:
  - 套牌必须30张, 当前27张
  - 奇利亚斯豪华版3000型 的系列 WHIZBANGS_WORKSHOP 不在当前标准池
```

`hs board` 输出的对局面板长这样（真实对局快照，对手昵称已脱敏）：

```
=== 炉石对局面板 ===
休闲·标准 | 构建号 253216
【对局已结束】
对局结束：我方胜利（斩杀，对方英雄阵亡）
总第 19 手 | 我方第 10 回合 | 我的回合 | 我的法力 10/10（已用 0）
对方：遛弯的树懒（牧师）[后手+硬币] 手牌 9 牌库 16 尸体 6 法力 1/9（已用 8）
英雄：「情报掮客拉祖尔」血 0/30 护甲 0 武器 无 技能「月亮的祝福」(已用)
对方场面(3)：
  1. 「心灵扫荡者」2/3
  2. 「受难的恐翼巨龙」5/5
  3. 「凯洛斯的蛋」0/3
我方：鸵鸟居士（战士）[先手] 牌库 20 尸体 4
英雄：「麦格尼·铜须」血 30/30 护甲 5 武器 无 技能「全副武装！」(未用)
任务「走进失落之城」9/10
我方场面(5)：
  1. 「奥卓克希昂」6/7
  2. 「拉格纳罗斯的士兵」4/2
  3. 「破链灾星霍格」10/10 [嘲讽]
  4. 「奥卓克希昂」6/1
  5. 「拉格纳罗斯的士兵」2/1
我方手牌(7)：
  1. 「屠灭」6费 法术
  2. 「龟甲旋风」4费 法术
  3. 「拉格纳罗斯，绝世烈火」8费 8/8 随从 <兆示：「拉格纳罗斯之手」>
  4. 「放出鳄鱼」2费 法术
  5. 「强固」3费 法术
  6. 「怒袭甲龙」3费 4/3 随从
  7. 「城防守卫」4费 法术
=== 行动回顾 ===
[第 18 回合·对方]
18-1 手牌已满，烧掉「暗言术：毁」
18-2 打出 随从「受难的恐翼巨龙」5/5<亡语：抽两张龙牌，其法力值消耗减少（1）点。>
18-3 英雄技能「月亮的祝福」<选择一张可用的牧师随从牌或法术牌置入你的手牌，其法力值消耗减少（>
18-4 选择：「心灵扫荡者」
18-5 获得「心灵扫荡者」
18-6 打出 随从「心灵扫荡者」2/3<战吼：如果你在本牌在你手牌中时使用过对手卡牌的复制，对所有敌方随从造成2点伤害。>
[第 19 回合·我方]
19-1 抽牌「城防守卫」<召唤两个0/6并具有嘲讽的守卫。守卫在受到伤害时会获得+1攻击力。>
19-2 攻击：「破链灾星霍格」10/10 → 对方英雄
19-3 攻击：「奥卓克希昂」6/1 → 对方英雄
19-4 死亡：「情报掮客拉祖尔」
```

## 对局面板与军师监听（board / watch）

> **质量保障**：board 的解析覆盖经过多轮真实对局的全量审计与逐项回归验收（数值对账、事件溯源、特殊局样本如秒投/截断/英雄牌变形）。炉石日志格式随版本变动，若新版出现解析问题，欢迎提 [issue](https://github.com/OstrichHermit/hearthstone-cli/issues) 或直接 PR。

`hs board` 从日志里最后一个 `CREATE_GAME` 起全量重放 packet，输出当前时刻的完整面板，适合直接喂给 AI 分析。我方默认按"手牌可见方"自动判定（只有客户端本人能看到手牌内容），判不准时用 `--player=玩家名` 手动指定；也支持 `--stdin` 从管道读日志，方便测试。

**面板层**：对局模式与构建号、总手数/回合/当前行动方、双方法力（`可用/总（已用 N）`，过载锁定单独标注）与先后手（后手标注+硬币）、双方英雄血/甲/武器/技能（技能被替换或灌注时显示新技能）、场面随从与地标（攻血 + 嘲讽/圣盾/风怒/冻结/休眠/潜行/扰魔等状态标注）、我方手牌费用攻血（含兆示预览 `<兆示：卡名>`、已强化/不可打出标注）、双方牌库剩余/疲劳/尸体数、任务进度槽（`任务「名」x/y`，与奥秘分流计数，完成报奖励）、终局胜负行（斩杀/投降/疲劳；日志被游戏客户端截断时明确提示且不误报胜负）。换牌阶段面板同样输出开局发牌，可直接给留牌建议。

**行动回顾**：按回合边界自动带最近三个回合——我方上回合全部、对方上回合全部、我方本回合已发生（`--turns=N` 调整带过的回合数，`--turns=0` 关闭），事件按日志原始顺序稳定排序：

- 卡牌效果描述全量不截断，按“入手一次”挂载：我方随抽牌/获得/起手/换入给出，我方打出不重复；对方手牌不可见，打出行是唯一展示点必带；召唤无来源时附描述。附事件时刻攻血快照、战吼目标；攻击附目标与实际伤害（含光环增幅后的真实数值）；英雄技能附效果与自带护甲；死亡附复生信息
- 开局段带换牌语义（起手 → 保留/换掉/换入 → 后手硬币）与 START_OF_GAME 触发效果（如"对战开始时复制传说"列全卡名洗入牌库）
- 引擎自动结算完整入流：亡语/触发的召唤带来源（同名合并 ×N 防刷屏）、亡语/触发伤害（致命标（致命））、治疗带来源、复生、休眠囚禁与苏醒、预备减费、发现/灾变类选择、洗入牌库汇总、回合结束获得（带来源与效果描述）、亡语亮牌（区分已施放/仅亮出）、手牌满烧牌（报卡名）
- 隐私设计：对方抽牌只报张数不报卡名

`hs watch start` 启动一个后台守护进程 tail Power.log，检测到换牌阶段或轮到我方回合时，向自建 IM 桥接器 `POST /api/external/message`（Bearer token 鉴权）注入固定提示词，由桥接器触发 Discord 军师频道的 AI 分析。说明：

- **桥接器是私有组件，不在本仓库内**（默认 `http://127.0.0.1:8088`）。不配置或连不上桥接器时，`hs watch` 单独使用只监听不发送——POST 失败自动重试 3 次后继续监听，不会崩溃，触发事件可用 `hs watch status --events=N` 查看
- 配置统一在 `~/.hearthstone-cli/config.json` 的 `watch` 节（`channel_id`/`url`/`token`/`log`/`mulligan_prompt`/`turn_prompt`），与 `hs collection` 共用同一个配置文件、按节管理；CLI 不提供配置参数，编辑文件后 `start` 生效；`--force` 可在残留进程时强制重启
- 提示词与 token 都在配置文件里改，token 也可用环境变量 `HS_WATCH_TOKEN` 传入

## 卡组收藏同步（可选，仅 Windows）

`hs collection` 通过一个内置的小型内存读取器（DeckExport）直接从运行中的炉石客户端读取收藏卡组，机制与 Hearthstone Deck Tracker 等社区工具同款：**只读游戏内存，不写入游戏进程**。读到的 standard/wild 卡组会自动编码为标准卡组代码，按 `hs save` 的存档格式写入卡组库，之后 `hs show` / `hs image` / `hs check` 等命令可直接使用。

前置条件：

- Windows + 炉石客户端正在运行
- dotnet SDK 9（`winget install Microsoft.DotNet.SDK.9`），仅构建读取器时需要
- 补丁版 HearthMirror 源码缓存（`~/.hearthstone-cli/native/src/`；上游 HearthMirror_Decompiled 仓库源码无法直接编译，需自行修复编译错误后放到该路径，或从装好本工具的机器复制）

常用命令：

```bash
# 手动导出一次（首次运行检测到读取器缺失时会自动构建）
hs collection export

# 只对比不写存档（调试用）
hs collection export --check

# 常驻监听：游戏内卡组一有变化自动同步（默认每 5 秒轮询一次）
# 先编辑 ~/.hearthstone-cli/config.json 的 collection 节（interval 轮询秒数 / sync_delete 删除同步开关），再启动
hs collection watch start
hs collection watch status                              # 运行状态 + 最近同步事件
hs collection watch stop

# 编辑配置文件后 --force 重启已在运行的守护使其生效
hs collection watch start --force

# 手动构建 / 修复内存读取器
hs collection build
```

说明：

- 同步默认只增不删：游戏里删除的卡组，本地存档保留不动；在配置文件把 `sync_delete` 设为 `true` 后随游戏同步删除（仅删除此前由同步写入的存档，手动 `hs save` 导入的不受影响）
- 同名卡组（游戏允许多槽同名）第 2 个起自动加 `-<deckId 后 4 位>` 后缀，命名稳定可复现
- 经典 / 竞技场等非标准 / 狂野格式的卡组会被跳过并列出
- 炉石未运行时 export 友好提示退出；watch 则静默等待，游戏开启后自动恢复同步
- 卡组内出现卡牌库不认识的卡时整组跳过，跑 `hs update` 刷新卡牌库后再同步
- 配置唯一入口是 `~/.hearthstone-cli/config.json`，watch 与 collection 各占一节、互不影响，CLI 不再提供配置参数（操作类 `--force` / `--check` / `--events=N` 与路径定位 `--config=路径` 除外）；watch.pid / watch.log / collection.pid / collection.log 与配置文件同目录，便于 `--config` 测试隔离。可复制仓库根目录的 `config.example.json` 起步。完整结构：

```json
{
  "watch": {
    "channel_id": "<Discord 频道 ID>",
    "url": "http://127.0.0.1:8088",
    "token": "<IM 桥接器 token>",
    "log": "auto",
    "mulligan_prompt": "换牌阶段提示词",
    "turn_prompt": "我方回合提示词"
  },
  "collection": {
    "interval": 5,
    "sync_delete": false
  }
}
```

- 旧的 `watch_config.json` / `collection_config.json` 在首次使用时自动迁移进统一文件的对应节（旧文件保留不删）

## 标准池维护

标准池白名单在源码 `src/hearthstone_cli/deck.py` 里的 `STANDARD_SETS`。新版本上线后：跑 `hs update`，把新系列代码加进去，再 `hs check` 体检卡组库。

## 数据源

卡牌数据来自社区项目 [HearthstoneJSON](https://hearthstonejson.com/)（本地化文本遵循 CC BY 4.0）。构建提取自游戏文件，官方补丁上线当天或次日即可获取；预览季爆料的新卡要等补丁正式部署后才会入库。

## 免责声明

炉石传说是暴雪娱乐的商标。本项目与暴雪官方无关，仅供个人学习研究使用。

## 许可

[MIT](LICENSE)
