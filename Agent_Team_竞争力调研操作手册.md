# Agent Team 竞争力调研操作手册

> **性质**：无用户访谈条件下的公开二次研究 + 站内代理指标操作手册。  
> **目的**：逼近三个问题——谁可能周活、今天用什么替代、我们可能强在哪（强多少标为未知直至有原型）。  
> **版本**：v0.5 | 日期：2026-08-13（增补 §19：各家编排形态 + 如何达到 Claude Teams / Kimi Swarm 水位）  
> **关联**：Agent_Team_需求分析、Agent_Team_架构设计、Agent_Team_Host_Bridge架构  
> **说明**：本文已自包含调研结果，可整份复制到远程文档；外链均为完整 URL。

---

## 0. 使用规则（先读）

1. **本手册产出的是假说与证伪条件，不是竞争力结论。** 表填满之前，禁止对外宣称「我们的切入点是 X」。
2. **只收带行为的证据。** 「Agent Team 很有前景」一类观点句不入库。
3. **对象不在同一层，禁止做成八家功能打分表。** 先按 §1 分层，再采证。
4. **近邻优先级**：同台的 AgentTeams / QwenPaw ≥ 编码运行时（Claude Code /同台冲突卡 | 与 AgentTeams/QwenPaw 的差异假说 + 证伪条件 | **Done** | §7 |
| 切口候选 | ≤3 个切口；每个含替代、近邻威胁、证伪条件 | **Done** | §10 |
| 站内代理指标 | 有什么写什么；无数据则显式写「缺失」 | **Done**：全部缺失 | §11 |
| 一页纸结论 | §13 模板填完 | **Done** | §13 |
| Kimi Claw/Swarm 对照 | 跨机拉起 + 编排可见意向对照 | **Done**（2026-07-23） | §17 |
| Attach / Bridge 绑定形态 | 同进程 TUI 回显？Bridge 绑 Agent 还是 Host？ | **Done**（2026-08-12） | §18 |
| 各家编排形态 | Claude Teams / Swarm / 房间 / 路由；如何达水位 | **Done**（2026-08-13） | §19 |

**Week 2 结束时若无法提出任一未被近邻覆盖的切口 → 结论写「延期或缩范围」，不要硬开全量 Agent Team。**  
本轮结论倾向：**缩范围（W1 Bridge 续本机）**；非全量云房间 Team。

---

## 1. 分层模型（研究框架）

| 层 | 含义 | 观察对象 | 用户实际在买什么 |
|----|------|----------|------------------|
| **A. 编码运行时 / IDE** | 写代码的主工具 | Claude Code（含 Agent Teams）、Codex、Cursor、Trae / trae-agent | 本机或云端编码执行 |
| **B. 通用 Age形态 | 不可声称 |
|---|--------|--------------------------|----------|
| Q1 | 谁周活会来 | 候选人群假说 + 站内可触达性打分 | 周活人数、转化率 |
| Q2 | 今天用什么替代 | 任务 → 现行做法地图（按加权频次） | 「用户都会用我们」 |
| Q3 | 强在哪、强多少 | ≤3 个差异化切口 + 证伪条件 | 「强 30%」类数值 |
| Q4（强制） | 与 AgentTeams/QwenPaw 关系 | 共存叙事或主动避开的清单 | 忽略同台冲突 |

本轮公开逼近结果摘要见 **§13**。

---

## 3. 资产与约束（写入每条假说）

### 3.1 我方资产（写成假说，不写成优势）

| 资产 | 获客假说编号 | 怎么验证 | 常见自欺 | 本轮备注 |
|------|--------------|----------|----------|----------|
| 网站 / 平台流量 | G1 | 相关入口点击 → 试用 → 次日/七日回访 | 有 PV ≠ 要 Agent Team | 站内指标 **缺失**（§11）；不可用 G1 推 Team |
| 阿里云少量免费分时机器 | G2 | 用户来是ä---------|---------------|----------------------|----------|
| 站内行为 / 漏斗 | `internal` | **1.0** | Q1 |
| Issue / Discussions 中重复出现的失败与用法 | `issue` | 0.8 | Q2 摩擦 |
| 中文社区带步骤的讨论（V2EX / 即刻 / 掘金 / 阿里云社区等） | `cn_community` | 0.8 | 通道与国内场景 |
| 可复现的非营销评测（同题多工具、有失败记录） | `review_repro` | 0.5 | 场景目录、对照维度 |
| 大 V / 通稿向评测 | `kol_review` | **0.3**（见 §4.3） | 仅场景枚举 |
| HN / Reddit | `hn_reddit` | 0.4 | A/B 层辅证；与钉/微重叠通常低 |
| 官方博客 / Changelog / 峰会稿 | `vendor` | 0.2 | 厂商押注方向 |
| PR（非用户向） | `pr` | 0.1 | 仅看「反复修什么」 |

### 4.2 各对象检索入口（操作时逐个勾选）

#### P0 — 同台近邻

| 对象 | 优先入口 | Issue / 反馈关键词 | 本轮勾选 |
|------|----------|-------------------|----------|
| AgentTeams（原 HiClaw） | https://github.co----|
| Claude Code / Agent Teams | https://code.claude.com/docs/en/agent-teams ；相关 GitHub / Forum | agent teams, teammate, mailbox, disconnect, remote, sleep | ✅ |
| Codex | OpenAI Codex 文档 / 社区 / 对比文 | cloud, automation, parallel, sandbox, PR | ✅ |
| Cursor | Forum / Changelog / 对比文 | background agent, multi-agent, remote | ✅ |
| Trae / trae-agent | 产品反馈区；https://github.com/bytedance/TRAE-agent | agent team（若无真实 team 用法则少花时间） | ✅ 已停（无 IM team 周活用法） |

#### P1 — 通道 / 通用运行时

| 对象 | 优先入口 | 关键词 | 本轮勾选 |
|------|----------|--------|----------|
| OpenClaw | 其 GitHub / 社区 | channel, gateway, slack, whatsapp, skill, unreliable | ✅ |
| Hermes | https://github.com/NousResearch/hermes-agent ；对比评测 | memory, skill, vs openclaw, multi-agent | ✅ |

#### P1 — 外部 C 层参照

| 对象 | 优先入口 | 关键词 | 本轮勾选 |
|------|----------|--------|----------|/Coze/QwenPaw 钉钉相关反馈 | ✅ |
| 飞书 | 同上替换为 Lark/飞书 | ✅ |
| 微信 | 个人向 Claw/Agent 遥控、公众号/社群讨论（注意：实现成本与合规另计，本阶段只采需求信号） | ✅ |

#### 大 V / 评测（降权收）

搜索模板（可复制）：

```text
"AgentTeams" OR "HiClaw" OR "QwenPaw" OR "CoPaw" 评测 OR 体验 OR 对比
"Claude Code" "Agent Teams" review OR vs Codex OR Cursor
Hermes vs OpenClaw
Coze 本地 Agent 体验 OR bridge 断连
Cursor Background Agent vs Claude Code
钉钉 Agent 机器人 协作
```

英文辅检索：

```text
Claude Code Agent Teams limitations
Codex vs Claude Code multi-agent
OpenClaw disconnect OR sleep
Hermes Agent vs OpenClaw reliability
Coze local agent bridge
```

### 4.3 大 V / 评测降权规则（强制）

| 条件 | 处理 |
|------|------|
| `source_type=kol_review` | 默认 `wau_evidence_weight=0.3` |
| 文中有可复现步骤 **且** 记录失败/局限 | 可提到 `0.5`，并改 `review_repro` |
| 仅ætype,source_url,product,layer,quote_or_paraphrase,scene,substitute,friction,channel,freq_guess,overlap_us,wau_evidence_weight,notes
```

| 字段 | 允许值 / 说明 |
|------|----------------|
| `id` | `E001` 起编 |
| `date_found` | `YYYY-MM-DD`（发现日，非原文日亦可，原文日写 notes） |
| `source_type` | Code / Codex / Cursor / Trae / OpenClaw / Hermes / Coze / Other |
| `layer` | `A` / `B` / `C` / `Other` |
| `quote_or_paraphrase` | ≤200 字；尽量贴近原话；翻译可，标注「译」 |
| `scene` | 短标签，如 `remote_fix_bug` / `parallel_review` / `im_dispatch` / `cloud_worker` |
| `substitute` | 用户**当时**怎么做：`ssh` / `coze_bridge` ` / `openclaw` / … |
| `friction` | 短标签：`sleep_disconnect` / `pair_fail` / `cost` / `context_loss` / `permission` / `install_friction` / … |
| `channel` | `terminal` / `ide` / `web` / `dingtalk` / `feishu` / `wechat` / `slack` / `oe` |
| `freq_guess` | `weekly_plus` / `monthly` / `rare` / `unknown`（无依据必须 `unknown`） |
| `overlap_us` | 与我方用户重叠：`high` / `mid` / `low`（国内云+平台+办公 IM → 偏高；纯英文 HN 编码闲聊 → 偏ä

### 5.2 加权频次（场景与替代排序用）

对某一 `scene` 或 `substitute`：

```text
score = Σ (wau_evidence_weight × overlap_factor × freq_factor)
```

| 因子 | 取值 |
|------|------|
| `overlap_factor` | high=1.0, mid=0.6, low=0.3 |
| `freq_factor` | weekly_plus=1.0, mont文笔。**

### 5.3 最低样本量门禁

| 阶段 | 门禁 | 本轮 |
|------|------|------|
| Week 1 结束 | 总摘录 ≥50；A/B/C 每层 ≥10；`kol_review` 占比 ≤40% | **通过**：初版 58；2026-07-23 增补 Kimi 后 **70**patch` / `sleep_disconnect` / `im_room_coord` 可称高频 |
| 提出切口 | 该切口依赖的 scene+substitute 合计加权分进入 Top，且 `overlap_us` 不以 low 为主 | W1 ä本手册 §5.4–§13 已内嵌结论与关键证据；完整 58 条统计如下。

| 维度 | 分布 |
|------|------|
| 层 | A=19，B=22，C=17 |
| `source_type` | vendor 24，issue 18，cn_community 7，kol_review 4，review_repro 3，hn_reddit 1，pr 1 |
| 产品（条数） | AgentTeams 13，OpenClaw 10ïRL，可直接复制打开）**：

| id | 产品 | 层 | 行为要点 | 来源 |
|----|------|----|----------|------|
| E001 | AgentTeams | C | Human 进房仍无权限需审批 | https://github.com/agentscope-ai/AgentTeams/issues/954 |
| E002 | Agagentscope-ai/HiClaw/issues/784 |
| E003 | AgentTeams | C | 简单清理陷入诊断死循环 | https://github.com/agentscope-ai/AgentTeams/issues/974 |
| E013 | QwenPaw | B | 飞书/钉钉多步工具任务消息刷屏 | https://github.com/age | 兼容 Claude Code 仍 Planned | https://github.com/agentscope-ai/QwenPaw |
| E020–E021 | ClaudeCode | A | Teams mailbox 卡住需 Esc/击键 | https://github.com/anthropics/claude-code/issues/34668 、https://github.com/anthropics/claude
| E022 | ClaudeCode | A | resume 不恢复 teammates | https://code.claude.com/docs/en/agent-teams |
| E027–E028 | Codex | A | 沙箱禁网 / 自动化权限静默回落 | https://github.com/openai/codex/issues/12867 、https://github.com/openai/codex/issues/15310 |
| E031 | Cursor | A | Cloud Agents s/cloud-agent |
| E036–E039 | OpenClaw | B | 休眠后通道挂 / 会话静默重置 | https://github.com/openclaw/openclaw/issues/80605 等 |
| E040–E041 | OpenClaw | B | 钉钉多 Agek-Real-AI/dingtalk-openclaw-connector/ |
| E042–E044 | OpenClaw/Other | B | 微信个人遥控；weclaw→本机 Claude | https://agent.csdn.net/6a17f90c10ee7a33f27616ec.html 、https://dqtx.cc/posts/aihacks/weclaw/ |
| E047–E049 | Coze | C | 官ættps://docs.coze.cn/cozespace/local_agent 、https://developer.cloud.tencent.com/article/2681646 |
| E059 | KimiClaw | B | 云端一键 OpenClaw，合盖不停 | https://www.kimi.com/zh-cn/resources/kimi-claw-introduction |
| E061–E062 | KimiSwarm | C | 子代理并行 + UI 可见每位进展；最多~300 | https://www.kimi.com/zh-cn/help/agent/agent-swarm |
| E06//www.kimi.com/blog/agent-swarm |
| E065 | KimiSwarm | A | Code Swarm：无 DAG、无横向通信、文件冲突靠拆分 | https://blog.csdn.net/weixin_43236007/article/details/16270229 Claw 群聊：Conductor+多端预关联+Thread 可见 | https://www.kimi.com/zh-cn/help/kimi-claw/kimiclaw-group-chat |
| E068 | KimiClaw | C | 须先关联 Claw 再入群，非任意容器即时 spawn | 同上 |
| E070 | KimiClaw | B | 本机 OpenClaw 入群依赖在线；@ 无响应先查私聊 | 同上 |

 H-钉：办公研发群需要 @ Agent 远程下令

- 状态：**弱支持**（行为证据足，但「周活会来我们」未证）
- 支持摘录 id：
  - E013 / E014：飞书/钉钉下发多步任务，工具过程消息成摩擦（说明**已在办公 IM 下指令**）— https://github.com/agentscope-ai/://github.com/DingTalk-Real-AI/dingtalk-openclaw-connector/
  - E010：AgentTeams 峰会口径含钉钉群 — https://www.alibabacloud.com/blog/agentteams-and-claude-tag-both-enter-group-cparadigm-or-a-new-narrative_603358
  - E056：同生态编码 CLI 亦接钉钉 — https://qwenlm.github.io/qwen-code-docs/en/users/features/channels/dingtalk/
- 反对摘录 id：
  - E009：AgentTeams 用 Matrix「消除钉钉/飞书审批开销」→ 部分用户可能**避开**企业钉钉审批 — https:/不能是「再做一个钉钉 Bot」。
- 证伪条件：相关行为摘录（非 vendor）持续 <3 且站内无钉钉意向；或用户来钉钉只为闲聊从不碰本机执行端（G3 失败）

### H-飞：飞书群存在同等或更强需求

-以飞书为主 → 调整一期优先级

### H-微：非办公个人遥控更依赖微信

- 状态：**弱支持 → 对「一期只钉钉」构成覆盖缺口警告**
- 支持 id：
help.aliyun.com/zh/simple-application-server/use-cases/openclaw-integrated-wechat
  - E044：weclaw 桥接微信→本机 Claude/Codex — https://dqtx.cc/posts/aihacks/weclaw/
  - E054：叙事「钉飞已有，普通人更要微信」
- 对产品含义：**一期不做微信时，必须写明覆盖缺口**-------|
| H-钉 | 弱支持 | 可做入口，须差异化执行端 |
| H-飞 | 弱支持 | P1 |
| H-微 | 弱支持（缺口） | 一期不做则结论写「非办公个人场景主动放弃」 |

---

## 7. 同台冲突卡（AgentTeams / QwenPaw）— 本轮填写

### 用户可能如何理解三者

| 产品 //github.com/agentscope-ai/agentteams） |
| QwenPaw | 「装在本机/云上的个人助理，钉钉飞书微信都能聊，还能当 AgentTeams 的 Worker」（E015、E012；https://gi从平台/钉钉把指令接到**我已经在用的** Claude/Codex/Cursor 本机会话」——若做成又一个云房间则与上两者无法区分 |

### 公开材料中已覆盖、ænPaw：E015、E016）
- OpenClaw/QwenPaw/Hermes 作为可编排 Worker runtime（E012）
- 通道消息降噪（E013、E014）
- 「纯 Web 多 Agent 时间线 / 云房间」——与近邻 **最撞**

### 公开材料中仍空、且与我方资产匹配的缝（候选）

| 缝 | 依据 | 资产 |
|----|--s resume 丢 teammates（E022） | G1、G3；执行层复用 A |
| **休眠/断连后的可靠远程** | OpenClaw sleep 挂通道（E036–E039）；Coze 官方要求不休眠（E047） | 工程差异，非流量故事 |
| **微信个人遥控本机撞车（E010） | G2 — 降权 |

### 共存叙事假说（主叙事仅 1 条）

- **叙事**：「QwenPaw / AgentTeams = 云端房间与个人/企业助理 runtime；我们 = **远程续上用户已有 Claude / Codex / Cursor 本机会话**（BrentTeams 当编码主入口；或 Coze/QwenPaw 交付等价「附着已有会话」；或站内无人配对本机 CLI

### 决策

- [ ] 可差异化共存
- [x] **必须缩范围避开*办公子集」优于「全量 Agent Team 房间平台」。若做跨机拉起（W4），须对照 Kimi Swarm/群聊写清差异（§17），避免「也能看见编排」空话。

---

## 8. 分层矩阵 — 本轮填写

> 协作单元枚举：`single` / `subagent` / `lead_worker` / `im_room` / `cloud_paral---|------|----------|----------------|
| AgentTeams | C | Human 进房仍无权限（E001）；Manager↔Worker 白名单漏配（E002）；诊断死循环（E003） | `lead_worker` + `im_room` | 云端容器 Worker + Matrix/Element | Matrix 为主；宣称钉/飞/企微等（E010、E011） | `permission`、（E013、E014）；可作 AgentTeams Worker（E012） | `mixed` | 本机或云 runtime | 钉钉推荐、飞书、微信等（E015、E016） | 通道噪声、`install_friction` | **高ailbox 卡住（E020、E021）；resume 丢 teammates（E022） | `lead_worker` / `subagent` | **本机终端为主** | terminal / IDE | `context_loss`、`cost`、`permission` | 低产品重叠、高**执行层复用** |
| Codex | A | 云沙箱并行 PR（E026）；断网致 push/自动化失败（E027、E02ursor | A | Cloud Agents 关本机开 PR（E031）；并发 8/归档占槽（E033）；GitHub-only（E034） | `cloud_parallel` | Cursor 云 VM | ide / web | `cost`、`install_friction` | 中低；执行面≠用户本机 cwd |
| Trae | A | 研ç （证据不足） | **低**；无真实 IM team 用法→停 |
| OpenClaw | B | 休眠挂通道/会话重置（E036–E039）；钉钉多 Agent（E040、E041）；微信无私聊çt`、`context_loss` | **高**：钉钉+长期跑着；微信缺口强 |
| Hermes | B | 从 OpenClaw 迁出求稳（E045）；作 AgentTeams 编码 Worker（E012） | `single` / Worker | 本机或容器 | terminal / 经通道 | 可靠性叙事（æ（E049） | `im_room` + bridge | **用户本机** | web（扣子） | `sleep_disconnect`、`pair_fail` | **高威胁缝**：远程本机已占位；差异须落在续会话/钉钉办公 | web | 会员门槛；关联本机仍 sleep（E070） | 中：常驻助手 |
| Agent Swarm | B/C | 主 Agent 自组织~300 子 Agent；UI 见每位进展（E061–E062）；peer/DAG 弱（E063、E065） | `lead_worker`（**平台内**） | Kimi 平台算力 | web | `cost`；非用户自有容器 | **高撞编排可见**；**低撞跨机 spawn** |
| Claw 群聊 | C |端可见**；缺口=即时拉起新环境 |

---

## 9. 场景×替代表 — 本轮填写

> 数据源：evidence 初版 n=58，Kimi 增补后 n=70。**只允许从本表 Top 格子çctions | overlap 为主 | 是否进入切口候选 |
|------|-------|------:|-----------------|---------------|--------------|------------------|
| 1 | `im_dispatch` | 5.58 | `dingtalk_bot`、`ask_colleague`、`openclaw`、`filter_manual` | `install_friction`、`permission`、通道噪声 | high | *`manual_config`、`manual_cleanup`、`claude_teams` | `permission`、`context_loss` | high | **是**（近邻已深） |
| 3 | `cloud_worker` | 1.19 | `wait_retry`、`ci_console`、`manual_config` | `permission`、`install_friction` | mid | 慎入（撞 AgentTeams / G2） |
| 4 | `parallel_review` | 1.ext_loss`、`cost` | mid | 否（A 层已覆盖） |
| 5 | `session_resume` | 1.08 | `spawn_new`、`manual_file_recover`、`start_new_chat` | `context_loss`、`sleep_disconnect` | midp_disconnect` | 0.99 | `restart_gateway`、`re_pair`、`hermes` | `sleep_disconnect` | mid/low | **是** |
| 7 | `remote_resume_cli` | 0.81 | `coze_bridge`、`weclaw`、`openclaw_shtall_friction`、`pair_fail` | high | **是**（Coze/weclaw 已占） |
| 8 | `cloud_pr` | 0.68 | `manual_push`、`wait_office`、`local_branch` | `permission` | mid | 否 |
| 9 | `cloud_automation` | 0.60 | `manual_intervene`、`manual_config`  | 否 |
| 10 | `cloud_parallel` | 0.48 | `wait_retry` | `cost` | mid | 否 |
| 11 | `parallel_coding` | 0.25 | `subagent`、`openclaw` | （偏 vendor） | mixed | 否 |
| 12 | `remote_fix_bug` | 0.20 | `coze_bridge` | `sleep_disconnect` | high | 并入 rank7 |

### Top 替代（跨场景加权）

| rank | substitute | score | 含义 |
|------|------------|--| 卡住就等/重试/Esc/击键 |
| 2 | `filter_manual` | 1.60 | 关工具/思考消息或忍刷屏 |
| 3 | `manual_config` | 1.32 | 手改白名单/json/沙箱 |
| 4 | `ask_col| 钉钉机器人/@ Agent |
| 6 | `telegram` | 0.80 | 用 TG 等替代微信遥控 |
| 7 | `subagent` | 0.78 | 子代理代替 Teams |
| 8 | `coze_bridge` / `weclaw` | — | 远程触达本机 Claude/Codex（E044、E047–E050） |

-----------------------|------|
| `im_dispatch` | 是 | 可称高频品类需求 |
| `sleep_disconnect` | 是 | 可称高频摩擦 |
| `remote_resume_cli` | 勉强 | 可进切口，须承认 Coze/weclaw 已占 |
| `im_room_coord` | 是 | 高频，但 **AgentTeams 主场** |

---

## 10. 切口候选卡ge/ACP）+ 钉钉办公入口

- 依赖 scene / substitute：`remote_resume_cli`(7) + `im_dispatch`(1) + `sleep_disconnect`(6) / `session_resume`(5)；替代=`coze_bridge`、`weclaw`、`dingtalk_bot`、`re_pair`
- 目标用户假说（Q1）：已（**周活人数未知**）
- 现行替代（Q2）：Coze Local Agent（E047–E049）、weclaw/微信（E044、E042）、SSH/回工位、OpenClaw 钉钉 Bot（E040）
- 我们可èG1）——**强多少未知**
- 近邻威胁：AgentTeams/QwenPaw **未**闭环续本机 Claude（E017 Planned）；Coze 已占远程本机叙事；QwenPaw 钉钉可分流「只想 IM 聊天」
- 依赖资产：G1、G3（G2 非必须）
 QwenPaw/AgentTeams 90 天内交付等价 Bridge+钉钉
- 最小验证：Wizard of Oz——钉钉收指令，续用户本机已开 Claude 会话；对照是否拒绝改用 Coze
- 状态：**候选（优先）**

### Wedge W2：本机 Lead + 平台免费分时机作 Worker

- 依赖：`cloud_worker`(3)；替äer 撞车）；G2 易成算力补贴自欺
- 证伪：用户只为白嫖机器；或说不出非价格差异
- 状态：**候选（降权 / 易抛弃）**

### Wedge W3：纯 Web å»认最差切口
- 状态：**抛弃**

### Wedge W4：跨机/容器 Endpoint Spawn + 编排管理台

- 依赖：`cross_machine_spawn`、`orchestrate_visibility`、`parallel_spawn`（E061–E070）
- 目标用户假说：Lead 要把子任 群聊（预关联多端+Thread）；AgentTeams 云 Worker；SSH/k8s/Cursor Cloud
- 我们可能强在哪（定性）：执行面=真实 endpoint（含临时容器生命周期）+ ç¿间 → 撞 AgentTeams
- 证伪条件：用户不在乎执行面归属；实现退化为预注册群聊；跨机断连无解；与 AgentTeams 说不清
- 最小验证：对照「群聊预注册」vs「临时容器 spawn」；对照「只要 Swarm 进度条」vs「还要本机/指定容器」
- 状态：*| C 层云房间 ≠ 本机会话续命 | Coze 已占；钉钉子集小；不做微信则个人场景空 | **倾向成立（W1）** |
| 本机 Lead + 平台免费机 Worker | 匹配 Gt 时间线 | 匹配 G1 导流 | 与 AgentTeams/QwenPaw **最撞**；Kimi Swarm 已强化可见并行 | **假缝（W3）** |
| 跨机/容器拉起 + 编排台 | Swarm/群聊未覆盖「任意环境即时 spawn」 | 若实质预注册则同构 Cla指标 | 近 30 天数值 | 备注 |
|------|--------------|------|
| 平台相关页 UV / PV | **缺失** | 需 ModelScope / 站内分析拉取 Agent、Studio、QwenPaw 相关页 |
| Agent / 项目 / 会话活跃用户数 | **缺失** | 同上 |
| Bridge 的入口列表 | **计划入口（无数据）** | 候选：ModelScope Agent/应用页、Studio、文档「本地连接」、钉钉开放能力；QwenPaw 亦指向 Studio（E058ïm」。

---

## 12. 两周日程（可勾选）

### Week 1 — 建图，不选切口

- [x] Day 1：建目录 `research/agent_team/`；复制本手册模板文件；通读 §0–§1
ode / Codex / Cursor 采证（目标 ≥15 条）
- [x] Day 3：OpenClaw + Hermes（目标 ≥8 条）
- [x] Day 4：Coze Local Agent + 国内钉/飞/微通道专项（目标 ≥8 条）
- [x] Day 4：Trae 快速扫描（无 team 真实用法则停）
- [x] Day 5：大 V/评测降权收录（计入æayer_matrix.md` + 场景聚类草稿  
- [x] **禁止**：宣布切入点（仅保留假说与证伪条件）

### Week 2 — 逼近切口

- [x] Day 6–7：算加权分；完成 `scene_substitute.md` Top5+
- [x] Day 7：填完 `channel_hypotheses.md` 三卡
- [x] Day 8：填完 `vs_agentscope.md`ï）只表决：
  - 保留哪个 Wedge
  - 哪些场景从需求文档降级
  - 是否启动最小原型 / Wizard of Oz
- [x] Day 10 输出一页纸结论（模板 §13）— **草证据规模

- 摘录数 / 各层数 / kol 占比：**58** / A19·B22·C17 / **6.9%**
- 来源类型：issue 18、vendor 24、cn_community 7、review_repro 3、kol_review 4、hn_reddit 1、pr 1

### Q1 候选人群假说（非周活承诺）

1. **办公研发**：已在钉钉/飞书群给 Bot/Agent ä工位仍驱动本机仓库（E048、E044）
3. **个人微信遥控用户**：与 (1) 重叠有限；一期不做微信则**主动放弃**（E042、E043）

### Q2 Top 替代（加权）

1. `wait_retry` / 手动击键（E020、E021）
2. `fiidge`、`weclaw`（E047、E044）
6. Cursor Cloud / Codex 沙箱（E031、E026）
7. AgentTeams 企业房间（E008）

### Q3 / 切口

- 保留：**W1**（远程续本机 CLI + 钉钉办公入口）
- 新增候选：**W4**（跨机/容器 endpoint ）
- 强多少：**未知**（需原型 / Wizard of Oz）

### Q4 同台关系

- **必须缩范围避开**再造 AgentTeams 式云房间 / QwenPaw 式全能 IM 助理
- 共存叙事ï¼§14.5）

### 通道

| 通道 | 状态 |
|------|------|
| 钉 | 弱支持，可作一期入口 |
| 飞 | 弱支持，P1 |
| 微 | 弱支持；**一期不做 = 非办公个人场景Agent 时间线、与 Claude Code「编排炫技」对标
- 删除/勿当卖点：未进 Wedge 的「多端点 EAS+GPU」除非单独验证 G2
- vs 竞品：去掉「默认 vs Coze」单打；**补上 vs AgentTeams/QwenPaw**；Coze 改为 Bridge 对照对象

### 下一步唯一动作（只选一个）

- [ [ ] 延期，不立项

---

## 14. 评审纪律（防讨好与自欺）

1. 谁提出切口，谁必须先读出**近邻已覆盖**的证据；不能只读支持证据。  
2. 禁止çº场景主动放弃」。  
5. 若与 AgentTeams 差异写不清：默认选项是**延期或缩成 Bridge 远程续命**，不是加大 Team 功能范围。

---

## 15. 目录初始化命令（可选）

在仓库根目录执行：

```esearch/agent_team/scene_substitute.md
touch research/agent_team/channel_hypotheses.md
touch research/agent_team/vs_agentscope.md
touch research/agent_team/wedges.md
touch research/agent_team/internal_signals.md
touch research/agent_team/conclusion.md
```

将 §5.1 表头写入 `evidence.csv` 第一行后开始录入。  
**本轮调研结果已全部写入本文 §5.4–§13，复制远程文档时只贴本文件即å----------|------|
| §2 用户画像 | 无加权证据支持的角色（如产品/测试指挥本机）标「未验证，不进一期指标」 |
| §3 核心场景 | 仅保留进入 Wedge 或 Top 场景的；其余降 P2+ → 本轮优先 W1 飞书状态」一句实话 |
| 多端点 EAS+GPU | 若走 W4，可作为一类 endpoint；勿单独当「炫技卖点」而无编排/生命周期 |

**回写需求文档须另开变更说明；本手册本身不自动改需求结论。**

---

## 17. Kimi Claw / Agent Swarm / Claw 群聊（2026-07-23|
|------|--------|----------|
| Kimi Claw | 云端托管 OpenClaw：常驻助手、技能、定时任务 | https://www.kimi.com/zh-cn/resources/kimi-claw-introduction |
| Agent Swarm | 同一任务内主 Agent 自组织大量 sub-agent 并行；U入口 https://www.kimi.com/agent-swarm |
| Claw 群聊 | Kimi Conductor + 多个已关联 Claw（云/PC/Android）；Thread 拆任务 | https://www.kimi.com/zh-cn/help/kimi-claw/e 子 Agent | CLI 主从并行；过程不回流；可审批派发；不嵌套 | https://moonshotai.github.io/kimi-code/zh/customization/agents.html |

产品线并列说明：https://www.kimi.com/zh-cn/help/agent/agent-overview

### 17.2 与意向能力对照

| 意向点 | Agent Swarm | Claw 群è¡端 | 弱：平台内 worker，非用户自有容器池 | **强但预注册**：云端 KimiClaw + 本机 OpenClaw + Android 可同群（E067） | 单实例 |
| 运行时「新环境即时 spawn」 | 平台内动态 sub-agent（E062） | **弱围观只读（E069） | 单助手过程 |
| 人可管理 | 高额度/Beta | @ 路由、群规、/stop、邀请移除（E069） | 重启/修复 |
| 子 Agent 互聊 / DAG | roadmap æ¿程灌回主上下文 | 分片，只报结论（E064）；CLI 中间不回流（E066） | Thread 不污染主群记忆 | — |

### 17.3 行为要点（带 URL）

1. 云端 ClUI 是卖点：https://www.kimi.com/zh-cn/help/agent/agent-swarm（可看子代理进展与结果）  
3. Swarm 自承仍缺 peer 通信、并行宽度可控：https://www.kimi.com/blog/agent-swarm  
4. Code Swarm 工程权衡（无 DAils/162702297  
5. 跨设备靠群聊 + 预关联 Worker，不是任意容器 RPC spawn：https://www.kimi.com/zh-cn/help/kimi-claw/kimiclaw-group-chat  
6. 本机端入群仍受在线/休眠影响：同上 FAQ（E070）

### 17.4 对我方的操作含义

- **只做「看得见的多 Agent 时间线」且执行全在平台内** → 与 Kimi Swarm **正面撞车**ï：差异应写在 **Endpoint Registry + 生命周期（创建/销毁/镜像/cwd/权限/日志回传）**，编排台展示的是**真实执行端状态**，不是模型内并行切ç¼我们若做 Team，须明示选 A）跨环境 spawn 协议，或 B）缩成 Bridge（W1），勿用「也能看见编排」空话开战。

### 17.5 证伪条件（W4）

1. 用户只要进度条，不在乎跑在谁家机器 → Swarm 已å´不清「非又一个云房间」。

### 17.6 建议最小验证

1. 对照 Claw 群聊：同一任务，预置两台 Claw vs Lead 对临时容器 spawn——问是否在乎「临时环境」。  
2. 对照 Swarm：只要编排时间线、执行全在云——问是否还要本机/指定容器。

---

## 18. 同进程 Attach 与 Bridge 绑定形态（2026-08-12）

> **结论先写**：公开可远程 IM 控制的产品，**几乎都不支持**把指令注入用户正在盯着的交互式 CLI/TUI；Bridge 多数要么绑「一条执行通道≈一个 Agent」，要么绑常驻 Gateway/实例——**几乎没有「配对认 Host，再挂多个可 attach Agent」**这一分层。  
> 关联纠正：[Agent_Team_Host_Bridge架构.md](Agent_Team_Host_Bridge架构.md)。

### 18.1 必须先分清的三种「attach」

| 能力 | 含义 | 用户感知例子 |
|------|------|--------------|
| **Live attach（同进程 / 同 TUI）** | 远程输入写进正在跑的交互进程，交互页同步回显 | 本地已开 `claude`，钉钉发指令后，**同一块 CLI 屏上看到该命令** |
| **Session attach（会话续上）** | 另起进程，经 ACP `session/load` 等加载同一会话历史 | 上下文可能续上，但**不是**那个交互页 |
| **Fresh spawn** | Bridge/通道新开 Agent 进程 | 全新会话；与用户已开的 CLI 无关 |

**验收口径（假说证伪用）**：若用户问「远程发来的命令能不能在本机 Claude CLI 交互页看见？」——调研范围内答案为 **不能**。能卖的差异最多是 **Session attach（明示）**，不是 Live attach。

### 18.2 各产品：是否同进程附着交互 CLI？

| 产品 | 同进程 Live attach？ | 实际模型 | 证据 / 依据 |
|------|----------------------|----------|-------------|
| **扣子 Local Agent** | **否** | Frontier↔ACP；**spawn 新子进程**，不附着用户已在跑的会话 | E049；https://developer.cloud.tencent.com/article/2681646 |
| **Claude Code** | **否**（对交互式 `claude`） | Agent Teams = Lead/队友邮箱；远程 IM 不是注入 Lead TUI。ACP `session/load` 至多是**另一进程续会话** | E020–E023；本机 ACP 路径见 bridge discovery 注释 |
| **Codex** | **否** | 交互 TUI 与 `codex-acp` **不同进程**；云端/自动化另开 | discovery：`Interactive Codex TUI is a different process` |
| **Cursor** | **否** | IDE Composer / 应用内 Agent chat **不可 ACP attach**；Cloud Agent = 独立 VM；CLI `agent acp` 另开 | discovery：`In-app Agent chat is not ACP-attachable` |
| **Trae** | 无关 | 研究向 CLI，无「IM 附着本机交互会话」产品路径 | E051 |
| **OpenClaw / Hermes / QwenPaw** | **否**（相对编码 CLI） | IM → **Gateway / 常驻 runtime**，不是附着用户开着的 Claude/Cursor 交互页 | E015、E036–E041、E045 |
| **Kimi Claw** | **否** | 云端 Claw 或关联本机 OpenClaw；预注册 Worker | E067–E070 |
| **AgentTeams** | **否** | Matrix/房间 + 容器 Worker；Manager 调度 | E008–E011 |
| **Kimi Swarm** | **否** | 平台内多子代理，不碰本机交互进程 | E061–E066 |

**场景复述（可写进 FAQ）**：本地先开 `claude` 交互式 → 再用扣子/钉钉/OpenClaw「远程连上」发指令 → **交互页看不到远程命令**；常见行为是连另一套 gateway，或 **新开** ACP/agent 进程（可能带同一 session id 的历史，也可能全新）。

### 18.3 各产品 Bridge：绑 Agent 还是绑 Host？

| 产品 | Bridge / 连接绑什么 | 一句话 |
|------|---------------------|--------|
| **扣子 Local Agent** | **偏 Agent（1:1 执行通道）** | `coze-bridge` 配对后 ≈「这台机上的那个本地 Agent」；派活 → spawn。与旧方案「Bridge = 那个 Agent」同构 |
| **OpenClaw** | **偏 Host（Gateway）** | 本机常驻 Gateway；通道挂网关，再路由到一个或多个 Agent。休眠断的是 gateway |
| **Hermes** | **偏 Host** | 同层：常驻 runtime/通道，非「每 Agent 一条 bridge」 |
| **QwenPaw / CoPaw** | **偏 Host（一实例多通道）** | 一进程接钉/飞/微信；Console/TUI 直连同一 runtime |
| **Kimi Claw** | **绑已关联的 Claw 实例** | 云 / 本机 OpenClaw / Android；群聊先选入群 Claw。本机侧仍靠 OpenClaw 插件 |
| **AgentTeams** | **绑 Worker / 容器端点** | Controller 调度独立 Worker；不是用户笔记本上的 Host Bridge |
| **Claude / Codex / Cursor** | **基本无「远程 IM Bridge」层** | 会话/云 Agent 是产品内对象；无「配对 host 再挂多 runtime」 |
| **我方纠正后目标** | **明确绑 Host** | 配对认机器（`bridge_id`）；Agent 挂在 Bridge 上可 @；一条 WS 多 Agent；默认 Session attach，失败明示 |

### 18.4 怎么读（对切口的含义）

1. **有 sidecar 的（扣子）**：连接对象看起来像本机，产品语义却是 **「开一条执行通道 = 一个本地 Agent」**，不是「登记这台电脑，再选 @coder / @reviewer」。
2. **有 Gateway 的（OpenClaw / Hermes / QwenPaw）**：更接近 **绑 Host/进程**，但执行的是 **自己的 runtime**，不是发现并附着本机已开的 Claude/Codex **交互**会话。
3. **云房间 / Swarm / AgentTeams**：绑平台侧 Worker/房间，不是用户 Host Bridge。
4. **差异假说（须原型验证，勿对外量化）**：竞品缺的是 **Host Bridge + 多 Agent + Session attach 明示**；**不是**「远程命令回显到交互式 CLI」（该能力调研范围内不存在，不宜当卖点）。

### 18.5 与 Host Bridge 纠正的对应关系

| 竞品常见形状 | 若我方照抄 | 结果 |
|--------------|------------|------|
| Coze：bridge ≈ 单 Agent 通道 + spawn | `/endpoints/pair` 1:1 + 默认 fresh | 打掉相对 Coze 的差异点 |
| OpenClaw：Gateway 绑 Host，但只管自有 Agent | 只做常驻 daemon、不发现已有会话 | 变成又一个通道 runtime，非「续上已有编码会话」 |
| **目标形状** | 一机一 Bridge WS；Discovery → Bind → Session attach\|fresh 明示 | 见 Host Bridge 专章成功标准 |

### 18.6 证伪条件

1. 扣子 / QwenPaw / OpenClaw 任一交付 **Live attach**（远程指令出现在用户已开 Claude/Codex TUI）→ Live 差异假说作废；仍可比 Session attach 可靠性与钉钉办公入口。  
2. 扣子改为「配对认机器 + 多 Agent 挂载 + 默认附着已有会话」→ Host Bridge 叙事与近邻同构，须另找切口。  
3. Wizard of Oz：用户只要「远程能跑」，不在乎是否同一会话 / 是否看见 CLI → Session attach 卖点降权，缩成可靠 bridge + 健康态即可。

---

## 19. 各家多 Agent 编排形态，以及如何达到 Claude Teams / Kimi Swarm 水位（2026-08-13）

> **结论先写**：Claude Teams 与 Kimi Swarm「看起来都很好」，但编排的是**不同对象**。照抄任一产品面都会撞车（手册 §7 / §17）。能吸收的是**机制**：独立上下文 + 显式协调总线 + 给人看的编排可见；不能吸收的是「再做一个本机小队 TUI」或「再做一个平台内 300 子代理进度条」。  
> 官方主源：https://code.claude.com/docs/en/agent-teams ；https://www.kimi.com/zh-cn/help/agent/agent-swarm ；https://www.kimi.com/zh-cn/help/kimi-claw/kimiclaw-group-chat

### 19.0 三种「编排」禁止混谈

| 类型 | 在编排什么 | 典型代表 | 与我方「编排台」关系 |
|------|------------|----------|----------------------|
| **A. 会话协作编排** | 多个 Agent 会话怎么拆任务、互发消息、人怎么点进某一个 | **Claude Agent Teams** | 可借鉴隔离与任务表；不是 IM/跨机 |
| **B. 平台内并行编排** | 一个任务下 spawn 大量子代理，UI 看每位进展 | **Kimi Swarm** | 可借鉴「可见」；执行面在他家云 |
| **C. 执行端/运维编排** | Worker 起在哪、在线否、生命周期 | **AgentTeams**、Cursor Cloud | **我方编排台本义**（Host Bridge §4） |

文档里的「编排台」= **给人看的 C/B 观察面**（Web 上看派到哪、谁在跑、成没成）。Claude 出名的是 **A**，不是这张台。

### 19.1 Claude Code Agent Teams（会话协作编排）

资料：https://code.claude.com/docs/en/agent-teams （文档口径约 v2.1.178+；实验特性，默认关，需 `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`）。

#### 它在编排什么

**一个 Lead 会话 + 多个独立 Teammate 会话**，同一台机器并行；共享任务表 + 文件邮箱协调。不是远程 IM 派到另一台机器。

```
你 ↔ Lead（主会话）
      ├─ 共享 Task List（pending / in_progress / completed，可有依赖）
      ├─ Mailbox（~/.claude/teams/{team}/inboxes/{agent}.json）
      └─ Teammates（各自独立 context window）
            ├─ 可互相 SendMessage
            └─ 人可直接进某个 teammate 说话（不必事事经 Lead）
```

#### 与 Subagent 的差别（官方对照，勿混）

| | Subagents | Agent Teams |
|--|-----------|-------------|
| 上下文 | 自己的窗口，结果回主 Agent | 完全独立窗口 |
| 通信 | **只能向主 Agent 汇报** | **队友之间可直接互聊** |
| 协调 | 主 Agent 全管 | 共享任务表 + 自协调 |
| 适合 | 短任务、只要结果 | 要讨论、对抗、并行探索 |
| Token | 较低（摘要回流） | **很高**（每人一份完整 Claude） |

「编排很有名」= 从「主叫子、子只回主」升级成「小队 + 任务板 + 互信箱」，且人能点进某一个队友的 transcript。

#### 给人怎么展示

- **In-process（默认）**：主终端下方 agent panel；↑↓ 选队友 → Enter 看 transcript / 直接发话；Esc 打断；Ctrl+T 任务列表。Idle 行会折叠/隐藏，**进程仍在**。
- **Split panes**：tmux / iTerm2 每队友一块 pane；一眼看所有输出。

#### 上下文怎么隔离（与「主会话被污染」直接相关）

官方写死：

- 每个 teammate **自己的 context window**。
- Spawn 时加载：项目 CLAUDE.md、MCP、skills + Lead 给的 **spawn prompt**。
- **不继承 Lead 的对话历史**。
- 中间思考/工具过程 **不会**自动灌进 Lead 主 transcript；Lead 收到的是消息与任务状态。
- 协调靠：自动送达消息、idle 通知、共享任务表、点名 messaging。

#### 协调机制

- 任务表：Lead 建任务；可指派或 teammate 自领；文件锁防抢单。
- 依赖：A 完成才 unblock B。
- Plan approval：可要求 teammate 先出只读 plan，Lead 批了才能改代码。
- 权限：spawn 时继承 Lead；权限弹窗浮到 Lead 由**人**批；队友不能互相「代批」绕过。
- Hooks：`TeammateIdle` / `TaskCreated` / `TaskCompleted` 可当质量闸。

#### 已知坑（证据）

- mailbox / Waiting for results 卡住，要 Esc/击键（E020、E021）。
- 任务完成状态滞后 → 依赖堵死，要手动 nudge（E023、E024）。
- in-process `/resume` **不恢复 teammates**（E022；官方 Limitations）。
- 一会话一 team、无嵌套、关停慢、token 线性上涨。
- Lead 有时自己开干不委派。

#### 一句话定性

Claude 编排 = **本机多 Claude 会话的小队操作系统**（任务板 + 邮箱 + panel/tmux）。强在协作语义和隔离；弱在远程/IM/跨机执行端。

### 19.2 Kimi Agent Swarm / Code Swarm（平台内并行编排）

资料：https://www.kimi.com/zh-cn/help/agent/agent-swarm ；https://www.kimi.com/blog/agent-swarm ；CLI：https://moonshotai.github.io/kimi-code/zh/customization/agents.html ；评测：https://blog.csdn.net/weixin_43236007/article/details/162702297

#### 编排模型

- 主 Agent（指挥官）对一个用户任务 **动态 spawn 大量同平台子代理**（宣传可到 ~300，E062）。
- **上下文分片**：子 Agent 各记「小本子」，**只把关键结论报指挥官**（E064）。
- CLI：子过程思考/工具 **不回流主历史**（E066）。

#### 给人看什么（产品卖点，E061）

任务清单；子代理并行；推理链路、工具与网址；**每位**进展与结果；关页后台继续。这是「编排可见」的高水位，接近我方说的编排台 UX，但数据是**平台内 worker**，不是用户机器。

#### 缺口

- 执行面在 Kimi 算力，非用户 cwd / 自有容器。
- peer 通信、DAG、并行宽度可控仍弱（E063、E065）。
- Code Swarm：常无「A 完再启 B」硬依赖边；无子 Agent 横向通信。

**和 Claude 比**：Claude = 队友互聊 + 本机独立会话；Swarm = 规模并行 + UI 可见 + 结论回流、过程隔离。

### 19.3 其余产品（只写编排语义）

| 产品 | 编排核心 | 协调总线 | 上下文 | 人怎么看 |
|------|----------|----------|--------|----------|
| **Kimi Claw 群聊** | Conductor + **已关联** Claw（云/PC/Android） | Thread 拆任务 | Thread ≠ 主群记忆 | Thread / 侧栏（E067–E069）；须先关联再入群 |
| **AgentTeams** | Manager → Leader → Workers | Matrix 房间 @mention | 分房 / 分容器；制品 MinIO | 进房间看；治理/HITL |
| **OpenClaw / Hermes / QwenPaw** | Gateway 多 persona | `channel+peer → agentId` 绑定 | 各 Agent 自有 memory | IM 按机器人分气泡；**路由不是小队** |
| **Cursor Cloud / Codex 云** | 每任务独立 VM/沙箱 | 平台作业队列 | 任务隔离 | Agents 列表 / PR / 日志 |
| **扣子 Local** | 单本地通道 | bridge spawn | 新进程，非多 Agent 编排 | 扣子侧一条聊 |

### 19.4 对照总表

| 产品 | 类型 | 「有名」在哪 | 勿照搬成我方主句 |
|------|------|--------------|------------------|
| Claude Teams | A | 本机多 Agent **协作语义**最完整 | 不要对外说「我们做 Claude Teams」 |
| Kimi Swarm | B | **编排可见**水位 | 不要对外说「我们做 300 子代理进度条」 |
| AgentTeams | C | 企业房间 + 容器生命周期 | 不要再造 Matrix 房间 |
| 我方目标 | C 为主，吸收 A/B **机制** | 真实执行端 + 编排台看执行端状态 | 见定位文档不可拆包 |

### 19.5 当前实现与「污染 / 覆盖」的对应（行为，非竞品）

用户可见症状：`@codex 你找北京美景` 卡片显示处理完毕，正文却是另一 Agent「无法调用其他 agent」；主对话被派工任务再次执行；同时派工时主输出被盖。

对照代码路径（`ms_agent/team/context.py`、`ingress.py`、SSE `team.stream`）：

| 层 | 现状 | 竞品对应机制 |
|----|------|----------------|
| 存储 | 项目 Timeline 混记人 + 各 Agent + system | Claude：Timeline ≠ teammate transcript |
| 派工上下文 | `project_timeline` 最近 20 条原样进每次 `merge_prompt` | Swarm：过程不回流；只报结论 |
| 展示 | 多路 `team.stream` 易写入同一 assistant 缓冲 | Claude panel / Swarm 每位一轨 |
| 路由 | `@codex` 同时可能再跑 default lead | OpenClaw：@ 只达被提及者 |

**规则（吸收后应写入实现）**：多 Agent 会话 = **编排时间线（共享、只读展示）** + **每 Agent 私有 runtime session（写入模型）**；回流只允许结构化结论 / 任务状态，禁止整段子 Agent token 流进主历史。

### 19.6 如何达到 Claude Teams / Kimi Swarm「那个水平」

> 不是复制产品，是达到**用户可感知的两水位**：协作时主上下文干净、并行时人能盯住每一位。执行面仍走真实 endpoint（Host Bridge），否则变成 Swarm/房间仿制品（手册 §7 已判假缝）。

#### 水位拆成可验收的体验，而不是功能清单

| 水位 | 用户可感知 | 对标 | 我方必须同时保住 |
|------|------------|------|------------------|
| **Claude 协作水位** | 派给 `@codex` 的活，不会在 Lead/`@me` 里再做一遍；人能点开 `@codex` 看它自己的流；Lead 只看到回执/摘要 | 独立窗口 + mailbox + 任务表 | 派到**真实** Claude/Codex 会话或云端点，不是平台内虚空 teammate |
| **Swarm 可见水位** | 同时派 2+ Agent 时，每人一张卡/一轨进度；工具与完成态绑在同一 `dispatch_id`；关页后台仍可看 | 任务清单 + 每位进展 | 卡片上的机器/attach\|fresh/在线态是真的，不是装饰 |

禁止的达法：在云里再 spawn 一堆 ms-agent 子进程扮 teammate，再画 Swarm 进度条——与 Kimi **正面撞车**，且丢掉 W1。

#### 机制对照：该偷什么、不该偷什么

| 机制 | Claude | Swarm | 我方怎么落地 | 不该偷 |
|------|--------|-------|--------------|--------|
| 独立上下文 | teammate 不继承 Lead 历史 | 子代理分片小本子 | **每 `endpoint_id`（+ thread）私有 runtime session**；dispatch 只带 brief | 把 Timeline 当 prompt |
| 回流 | 消息/任务状态，非 token 流 | 只报结论 | `dispatch_done.summary` + `TeamTask` 状态进 Lead；全文留在该 Agent 轨 | 子 Agent 全文进主 LLM |
| 协调总线 | 共享 task list + mailbox 文件 | 平台内调度 | 扩展已有 `TeamTask` + EventBus；Worker 完成写 task，不写进 Lead transcript | 另造 Matrix；一期不必队友互聊 |
| 人看的面 | panel / tmux 分屏 | 每位进展 UI | Web 编排台：**按 `dispatch_id`/`at_name` 分轨**；SSE 禁止写入单一 assistant buffer | 复制 Claude TUI / 复制 Swarm 炫技主页 |
| 人直接跟 Worker 说话 | Enter 进 teammate | 较弱 | `@codex 补充：…` 只派 codex，不经过 Lead 再生成一遍 | 所有话都先让 Lead「翻译」 |
| 规模 | 建议 3–5 teammate | 宣传 ~300 | **真实执行端数量**受机器/配额限制；不要用子代理数量当 KPI | 「也能 300」 |

#### 分阶段（叠在现有 Phase A/B/C 上，不另开产品线）

**P0 — 先达到「不脏、不盖」（否则谈不上任一水位）**

1. **Context 过滤**：`ContextBundleAssembler` 按目标 `endpoint_id` 组装；禁止把其他 Agent 的完整回复灌进 prompt。Lead 最多带：任务 id、状态、**截断摘要**。
2. **路由**：消息里出现 `@codex` → **只**派 codex；default lead 不得静默附跑。需要汇总时走显式第二步或 `delegate_to_endpoint` 回执。
3. **SSE 分轨**：`team.stream` 必须带 `dispatch_id` + `at_name`；前端一 dispatch 一气泡/卡片。「处理完毕 22s」只允许挂在同一 dispatch 的 summary 上；对不上标 `attribution_mismatch`。
4. **验收句**：复现「@codex 找北京美景」→ 主会话不再出现「无法调用其他 agent」的串台正文；再次 @lead 询问进度时，Lead **不重新执行**找美景。

**P1 — Claude 协作水位的最小集（仍在真实 endpoint 上）**

1. 共享 **Task list**（已有 `TeamTask`：pending/in_progress/completed + 可选 `blocked_by`）成为协调源，而不是聊天记录。
2. **Mailbox 语义**（不必先做 JSON 文件）：`SendMessage`/`dispatch_done` 结构化事件；接收方当作「来自另一 Agent 的消息」而非用户口吻（对标 Claude：队友不能代批权限）。
3. 编排台：点开 `@name` 看到**该 Agent 私有流**；主会话只留人类意图 + 回执条。
4. spawn/attach 时 **不携带** Lead 历史（已有 `session_mode`；补：fresh 的 prompt = 任务 brief，不是 timeline dump）。

**P2 — Swarm 可见水位的最小集（数据来自真实 dispatch）**

1. 编排台任务清单：每 Worker 进度、起止、cancel、日志入口、产物。
2. 展示工具/来源**可选折叠**（吸收 E013 通道噪声）；默认给人看摘要。
3. 关页后台继续：已有 Event SSE + wait；UI 重连按 `dispatch_id` 续订，不新建会话。
4. **不要**把「子代理数量」或「推理链路可视化」当主标题。

**明确后置（对标官方缺口，不必追）**

- 队友横向互辩 / 嵌套 teams（Claude：无嵌套；Kimi：roadmap）。
- in-process TUI resume 恢复全部 teammate（Claude 自己做不到，E022）。
- 平台内 300 并行。

#### 证伪条件

1. 用户要的就是「一个聊天窗里所有人一起说」且接受串台 → 分轨/隔离降权，改卖可靠派工即可。
2. 做成独立上下文 + 任务表之后，用户仍觉得不如 Claude TUI → 承认 A 层体验差在 IDE/终端宿主，我方主场仍是跨环境派工，不在本机小队手感。
3. 做成可见进度条且执行全在云 ms-agent → 与 Swarm 同构，W1 失败，须停或改口。

#### 最小验证（建议一周内可做）

1. 同屏：人发 `@codex A` 与 `@me B` → UI 两轨、两份 session、互不进对方 prompt。
2. 完成后问 Lead「codex 做完了吗」→ Lead 只读 task/summary，**不**再跑 A。
3. 对照 Claude 文档：我方「点开 Worker」是否等价 panel Enter；对照 Swarm：任务清单是否展示**真实** endpoint 标签（机器 / attach|fresh）。

编排台功能拆解与控制面设计（四块 UI、三套存储、ContextGate）已落专章：[Agent_Team_编排台_功能与架构.md](Agent_Team_编排台_功能与架构.md)。各家编排台参考卡（Claude / Swarm / Claw 群聊 / AgentTeams / OpenClaw / QwenPaw / Hermes / Cursor / Codex / 扣子 / Trae）见该专章 **§10**。

