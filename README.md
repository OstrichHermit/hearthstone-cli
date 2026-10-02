# hearthstone-cli — Hearthstone Deck CLI for AI Agents

**面向 AI Agent 的炉石传说组卡命令行工具 —— 校验、编码、解码、筛卡、卡组库存档与版本体检，另可解析客户端日志输出对局面板、监听对局触发 AI 军师分析。**

A command-line deck building tool for Hearthstone designed for AI agents — validation, encoding, decoding, card filtering, deck archiving and patch-cycle health checks.

[English](README_EN.md) | [简体中文](README.md)

---

人类的组卡模拟器解决的是"可视化拖卡"，而 Agent 组卡需要的是秒级试错：列 30 张卡 → 校验 → 出卡组代码 → 按报错修正 → 再来一轮。这个工具就是为此而生。

## 功能特性

- **卡组代码编解码** — 完整支持炉石 deckstring 格式（含副牌库三元组），并对营地等平台在标准代码后附加的扩展字节做了容错
- **构筑规则校验** — 数量、同名限量（普通 2 张 / 传说 1 张）、职业限定、标准池白名单
- **动态卡组容量** — 裂魂者阿扎莉娜（套牌 20 张）、时空大盗拉法姆（40 张且恰含 10 张拉法姆）、常规 30 张
- **副牌库** — 乐队经理精英牛头人酋长的 3 张乐队，编码为 sideboard 三元组
- **多职业与游客机制** — 按 `classes` 数组识别多职业卡（如六职业共用的灭世者死亡之翼），并完整实现胜地历险记游客三条规则：游客仅解锁目的地职业的该扩展卡牌、每套限一名游客、不可嵌套
- **卡组库与版本体检** — 卡组本地存档，版本更新后一键体检，退环境卡逐条列出
- **卡组长图** — 一条命令把卡组渲染成可分享的卡组长图（中/英版各自独立）：法力曲线、稀有度配色（默认逐张列出，`--merge` 合并同名卡）、职业徽记、英雄与卡组代码，2x 渲染输出 1520px 宽，调用本地无头 Chrome/Edge
- **对局面板** — 解析炉石客户端日志 Power.log，输出当前对局的结构化面板：回合、法力、双方英雄血甲、场面随从（含嘲讽/圣盾等状态标签）、我方手牌费用攻血、对手手牌数与双方疲劳
- **军师监听** — `hs watch` 后台监听 Power.log，换牌阶段和轮到我方回合时向自建 IM 桥接器推送固定提示词，触发 AI 军师分析对局
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

开发模式用 `pip install -e .`（改动源码即时生效）。PyPI 发布：Coming soon。

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
hs watch start --channel=<Discord频道ID> --token=<桥接器token>   # 默认 --log=auto 自动发现
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
总第 10 手 | 我方第 5 回合 | 我的回合 | 我的法力 3/5（已用 2）
对方：打盹的考拉（猎人）手牌 7 疲劳 0
英雄：雷克萨 血 30/30 护甲 0 武器 无 技能 稳固射击(未用)
对方场面(2)：
  1. 游侠队长奥蕾莉亚 2/4 [本回合上场]
  2. 抛石鱼人 2/4 [本回合上场]
我方：鸵鸟居士（战士）疲劳 0
英雄：麦格尼·铜须 血 29/30 护甲 5 武器 无 技能 全副武装！(已用)
我方场面(0)：
我方手牌(6)：
  1. 时光领主埃博克 6费 7/5 随从
  2. 走进失落之城 1费 法术
  3. 放出鳄鱼 2费 法术
  4. 拉格纳罗斯，绝世烈火 8费 8/8 随从
  5. 强固 3费 法术
  6. 时光领主埃博克 6费 7/5 随从
=== 行动回顾 ===
[第 8 回合·我方] 抽牌 强固<获得3点护甲值。对一个敌方随从造成等同于你护甲值的伤害。>
[第 8 回合·我方] 打出 法术「控制局面」
[第 8 回合·我方] 死亡：异教低阶牧师 3/0
[第 8 回合·我方] 死亡：游侠新兵温蕾萨 2/0
[第 8 回合·我方] 死亡：抛石鱼人 1/0
[第 9 回合·对方] 抽牌 1 张
[第 9 回合·对方] 打出 随从「游侠队长奥蕾莉亚」 2/4<战吼：发现一张法术牌。如果你使用过希尔瓦娜斯或温蕾萨，每使用过一位，重复一次。>
[第 9 回合·对方] 获得 2 张牌
[第 9 回合·对方] 打出 随从「抛石鱼人」 2/4<战吼：获取一张 法力值消耗为（1）的石头。石头可以造成3点伤害。>
[第 10 回合·我方] 抽牌 时光领主埃博克<战吼：消灭你的对手上回合使用的 所有随从。>
[第 10 回合·我方] 英雄技能 全副武装！
# 实体总数 106 | 解析起始行 2 | 日志总行 8000
```

## 对局面板与军师监听（board / watch）

> **`hs board` / `hs watch` 仍在开发中**：炉石日志格式随版本变动，变身/衍生物/回手等复杂对局效果的覆盖还在持续完善。解析报错或面板数据不对时，欢迎提 [issue](https://github.com/OstrichHermit/hearthstone-cli/issues) 或直接 PR，一起把它打磨好！

`hs board` 从日志里最后一个 `CREATE_GAME` 起全量重放 packet，输出最终状态面板，适合直接喂给 AI 分析。我方默认按"手牌可见方"自动判定（只有客户端本人能看到手牌内容），判不准时用 `--player=玩家名` 手动指定；也支持 `--stdin` 从管道读日志，方便测试。输出末尾附行动回顾，按回合边界自动带最近三个回合——我方上回合全部、对方上回合全部、我方本回合已发生（`--events=N` 调整带过的回合数，`--events=0` 关闭）。效果描述（截断 48 字符）跟着牌的入场走一遍：我方随抽牌事件给出（起手发牌与换牌重抽记为"开局"回合），对方随打出的牌给出，召唤物双方都附，AI 无需查库即可理解新卡；攻击/死亡/打出/召唤事件附事件时刻的攻血快照（当前血已按受伤扣减），随从交换谁撞谁一目了然；对方抽牌只报张数不报卡名。换牌阶段面板同样输出开局发牌，可直接给留牌建议。


`hs watch start` 启动一个后台守护进程 tail Power.log，检测到换牌阶段或轮到我方回合时，向自建 IM 桥接器 `POST /api/external/message`（Bearer token 鉴权）注入固定提示词，由桥接器触发 Discord 军师频道的 AI 分析。说明：

- **桥接器是私有组件，不在本仓库内**（默认 `http://127.0.0.1:8088`）。不配置或连不上桥接器时，`hs watch` 单独使用只监听不发送——POST 失败自动重试 3 次后继续监听，不会崩溃，触发事件可用 `hs watch status --events=N` 查看
- 配置 merge 存于 `~/.hearthstone-cli/watch_config.json`，再次 `start` 不带参数沿用上次配置；`--force` 可在残留进程时强制重启
- 提示词可用 `--mulligan-prompt=` / `--turn-prompt=` 自定义，token 也可用环境变量 `HS_WATCH_TOKEN` 传入

## 标准池维护

标准池白名单在源码 `src/hearthstone_cli/deck.py` 里的 `STANDARD_SETS`。新版本上线后：跑 `hs update`，把新系列代码加进去，再 `hs check` 体检卡组库。

## 数据源

卡牌数据来自社区项目 [HearthstoneJSON](https://hearthstonejson.com/)（本地化文本遵循 CC BY 4.0）。构建提取自游戏文件，官方补丁上线当天或次日即可获取；预览季爆料的新卡要等补丁正式部署后才会入库。

## 免责声明

炉石传说是暴雪娱乐的商标。本项目与暴雪官方无关，仅供个人学习研究使用。

## 许可

[MIT](LICENSE)
