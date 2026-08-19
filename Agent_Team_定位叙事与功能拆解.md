# Agent Team：竞品对照、用户摩擦、宣传叙事与功能拆解

> 版本：v0.3 | 日期：2026-08-13（增补 §2.13 各家编排；§5.5 如何达到 Claude Teams / Swarm 水位）  
> 性质：基于公开二次研究的**产品假说文档**，不是竞争力定论；「强多少」未知，须原型或 Wizard of Oz 验证。  
> 证据：Agent_Team_evidence.csv（E001–E070）；操作手册 §18–§19 与分层矩阵见同目录调研产出。  
> 说明：外链均为完整 URL，可整份复制到远程文档。

---

## 0. 先定调：我们在卖什么（一句话）

**在用户指定的机器和容器里，把活派出去、盯得住、收回来——还能接上已经在跑的编码 Agent。**

这不是「多 Agent」「群聊更好看」「免费机器」。  
能力必须绑成**不可拆的交付包**；只做其中一项或几项，公开市场上几乎都会撞到近邻。

| 只做这些 | 容易撞谁 |
|----------|----------|
| 编排可见（进度 / Thread / 时间线） | Kimi Agent Swarm、AgentTeams 房间 |
| 多 Bot + Conductor、预登记再èLocal Agent、Kimi Claw 群聊、Kimi Agent Swarm（编排面）、（我方意向） |

我方默认位置：**C 层产品面 + 复用 A/B 执行面**。  
禁止把「比 Claude Code Agent Teams 编排更炫」当主竞争力问题。

---

## 2. 与各框架的优劣与区别

下列「优/劣」指**对公开用户行为与产品形态的客观对照**，不是「我们已更强」。  
「相对我方意向包」一列：意向包 = 跨环境真实执行端 spawn + 执行端级编排可见可管 +（可选）续上已有 A 层会话。

### 2.1 AgentTeams（原 HiClaw）— C 层同台近邻

- 入口：https://github.com/agentscope-ai/agentteams  
- 形态：Matrix 房间 + Manager-Worker；云端容器 Worker；宣称钉/飞/企微等  
- 优势：企业治理叙事完整（权限分级、HITL、可审计房间）；Worker 可共存 OpenClaw/QwenPaw/Hermes  
- 劣势 / 摩擦（调研）：权限白名单导致进房仍无法 @（E001 https://github.com/agentscope-ai/AgentTeams/issues/954）；Manag形态：本机/云个人助理；钉钉推荐；飞书/微信等多通道；可作 AgentTeams Worker  
- 优势：国内 IM 通道产品化深；安装路径含 Studio  
- 劣势 / 摩擦：飞书/钉钉多步工具任务过程消息刷屏（E013 https://github.com/agentscope-ai/CoPaw/issues/583 、E014）；兼容 Claude Code 仍 Planned（E017）→ **续本机 CLI 会话未闭环**  
- 相对意向包：钉钉助理与「只想 IM 聊天」分流强；跨环境 Team / 续会话不是其当前主叙事。撞点在通道与个人助理，不在「指定容器 spawn」。

### 2.3 Claude Code（含 Agent Teams）— A 层执行面

- 入口：https://code.claude.com/docs/en/agent-teams  
- 形态：本机终端 Lead + teammates；mailbox；偏并行开发/评审  
- 优势：用户已满意的编码主路径；独立上下文队友  
- 劣势 / 摩擦：mailbox 卡住需 Esc/击键才继续（E020 https://github.com/anthropics/claude-code/issues/34668 、E021 https://github.com/anthropics/claude-code/issuepenai/codex/issues/15310）；配置开了网络仍 DISABLED（E029）  
- 相对意向包：可作一类云 endpoint；不是办公 IM 入口，也不是「用户本机 cwd」。权限门是共性摩擦，我方编排台必须暴露「网络/审批状态」。

### 2.5 Cursor Cloud Agents — A 层云 VM

- 入口：https://cursor.com/docs/cloud-agent  
- 形态：独立 VM、开 PR、可关本机；偏 GitHub  
- 优势：并行、制品（截图/视频）、远程桌面  
- 劣势 / 摩擦：Pro 并发上限与归档占槽（E033 https://forum.cursor.com/t/clarification-on-cloud-agent-limits-simultaneous-agents-vs-environments-repos/157584）；GitHub-only（E034）；止于 PR、无 BYOC（E035）  
- 相对意向包：「关电脑继续」已满足；执行面≠用户本机。我方若只做云 VM 并行 → 撞 Cursor/Codex，无差异。

### 2.6 Trae / trae-agent — A 层（低优先级）

- 入口：https://github.com/bytedance/TRAE-agent  
- 形态：研究向 CLI；多 Agent 多为框架改造擦  
- 相对意向包：是**执行端/通道组件**，不是协作平面。跨机后「endpoint offline」是必解摩擦，否则编排台只会显示红灯。

### 2.8 Hermes — B 层执行端

- 优势：部分用户从 OpenClaw 迁出求稳（E045）；可被 AgentTeams 用作编码 Worker  
- 劣势：公开周活与 Team 证据薄  
- 相对意向包：可作 Worker 类型之一，不构成主竞品。

### 2.9 Coze Local Agent — C 层「远程本机」占位

- 入口：https://docs.coze.cn/cozespace/local_agent  
- 形态：coze-bridge；扣子侧对话，本机执行  
- 优势：远程 @ 本机 Claude 的叙事已教育市场（E048）  
- 劣势 / 摩擦：官方自承休眠/关机/断网则断连，需重连（E047）；bridge **spawn 新进程**而非附着已有会话（E049 https://developer.cloud.tencent.com/article/2681646）；接入 Hermes 等需 shim、缓存失败（E050）  
- 相对意向包：**W1 的直接对照。** 差异必须是「续会话 / ACP 附着」，不是「我äAG、无横向通信、文件冲突靠拆分（E065 https://blog.csdn.net/weixin_43236007/article/details/162702297）；子过程常不灌回主上下文（E064、E066）  
- 相对意向包：**编排可见的高水位线。** 只做时间线/进度条 → 假缝。

### 2.10 kimi Claw 群聊（C）
- https://www.kimi.com/zh-cn/help/kimi-claw/kimiclaw-group-chat  
- 优：Conductor + 云/PC/Android 多 Claw；Thread、工作空间、围观；跨设备协作（E067、E069）  
- 劣：**须先关联再入群**，非任意新容器即时 spawn（E068）；本机 @ 无响应先查私聊在线（E070）  
- 相对意向包：**多端协作可见的高水位线。** 若落地变成「预登记 Bot 列表 + 群调度」→ 同构。

### 2.11 一页对照总表

| 框架 | 强项 | 弱项 / 摩擦主题 | 与意向包关系 |
|------|------|-----------------|--------------|
| AgentTeams | 房间治理、云 Worker | 权限白名单、死循环、配置漂移 | 忌再造房间 |
| QwenPaw | 国内 IM 助理 | 通道噪声、安装摩擦 | 忌只做 IM 助理 |
| Coze Local | 远程本机叙事已教育市场 | spawn 非附着；休眠断连 | W1 直接对照 |
| OpenClaw/Hermes | Host 级 Gateway / 通道 | 不管用户已开编码 CLI | 执行端组件，非协作平面 |
| Kimi Swarm/群聊 | 编排可见、多端预关联 | 非任意环境即时 spawn | 忌只做进度条/预登记群 |

### 2.12 同进程 Attach 与 Bridge 绑 Host vs Agent（2026-08-12）

完整表与证伪条件见 [Agent_Team_竞争力调研操作手册.md](Agent_Team_竞争力调研操作手册.md) **§18**。此处只收产品含义。

**三种 attach（禁止混谈）：**

| 能力 | 含义 | 卖点？ |
|------|------|--------|
| Live attach | 远程指令出现在用户已开的交互式 CLI/TUI | **调研范围内不存在**；不宜当卖点 |
| Session attach | 另进程 ACP `session/load` 续同一会话历史 | **可卖**，须 Timeline 明示 attach\|fresh |
| Fresh spawn | Bridge 新开进程 | Coze 默认路径（E049）；照抄则无差异 |

**FAQ 口径：** 本地已开 `claude` → 远程 IM 发指令 → **交互页看不到该命令**。竞品要么连 Gateway，要么 spawn 新进程。

**Bridge 绑定形态摘要：**

| 形态 | 代表 | 对我方 |
|------|------|--------|
| 偏 Agent（1:1 通道） | 扣子 coze-bridge | 旧 `/endpoints/pair` 同构；禁止回退 |
| 偏 Host（Gateway / 一实例） | OpenClaw、Hermes、QwenPaw | 绑机器/进程，但只管**自有** runtime，非发现已开 Claude 会话 |
| 绑实例 / Worker | Kimi Claw、AgentTeams | 预关联或容器端点，非本机 Host Bridge |
| **目标** | 我方 Host Bridge | 配对认机器；一条 WS 多 Agent；默认 Session attach |

叙事约束：**差异写「Host + 多 Agent + 续会话明示」**；**禁止**写「远程命令回显到 Claude CLI 屏上」。

### 2.13 各家「编排」不是同一种东西（2026-08-13）

全文与达水位路径见 [Agent_Team_竞争力调研操作手册.md](Agent_Team_竞争力调研操作手册.md) **§19**。编排台视角的各家参考卡（含 Subagents / Swarm / Claw 群聊 / AgentTeams / OpenClaw / QwenPaw / Hermes / Cursor / Codex / 扣子 / Trae）见 [Agent_Team_编排台_功能与架构.md](Agent_Team_编排台_功能与架构.md) **§10**。

| 类型 | 编排对象 | 代表 | 有名在哪 |
|------|----------|------|----------|
| A. 会话协作 | Lead + 独立 teammate + 任务表 + mailbox | **Claude Agent Teams** | 本机小队协作语义；主历史不吞队友过程 |
| B. 平台内并行 | 指挥 + 海量子代理；只回流结论 | **Kimi Swarm** | 编排可见：每位进展 / 工具链 |
| C. 执行端运维 | Worker 在哪、在线、生命周期 | AgentTeams / Cursor Cloud | 房间或云作业；**我方编排台本义** |

Claude 官方对照（https://code.claude.com/docs/en/agent-teams）：Teams ≠ Subagent——后者只能向主 Agent 汇报；前者队友互聊、共享任务表、人可直接进某个 teammate。Swarm 官方（https://www.kimi.com/zh-cn/help/agent/agent-swarm）：看得见并行，但执行面在平台内。

**对宣传**：可对标「主上下文干净 + 人能盯住每一位」；**禁止**标题写成「我们的 Claude Teams」或「我们的 300 Swarm」。

---

## 3. 调研中的用户使用摩擦（应按功能设计吸收）

以下按**摩擦类型**归并；实现意向包时，这些是「细节功能」清单，不是文案装饰。证据 id 可在 evidence.csv 核对。

### 3.1 æ不在房」vs「无执行权」 |
| Manager 不在 Worker allowFrom，消息被静默忽略 | AgentTeams E002 | Lead→Worker 默认信任链；禁止静默丢弃，须回执ush/自动化失败 | Codex E027–E030 | 任务卡片展示网络/审批状态；无人值守策略显式配置 |

### 3.2 休眠、断连、会话不续（远程最大坑）

| 摩擦 | 来源 | 对功能含义 |
|------|------|------------|
| 合盖/休眠后通道停、会话重置、轮询eauth |
| 关联本机 Claw 后群里 @ 无响应，先查私聊是否在线 | Kimi E070 | 编排台「执行端在线」与「会话健康」分列 |
| Claude Teams resume 丢 teamma|
|------|------|------------|
| mailbox 有消息但不处理，需 Esc/击键 | Claude E020、E021 | 独立于 UI 焦点的收件箱轮询；卡住告警与一键 nudge |
| Manager 处理中无法中断 | AgentTeams E005 | 全局 /stop、按 Woam | AgentTeams E004 | 幂等创建；「查询 vs 变更」工具分离 |

### 3.4 通道噪声与上下文膨胀

| 摩擦 | 来源 | 对功能含义 |
|------|------|------------|
| 飞书/钉钉工具调用/思考过程刷屏 | QwenPaw E013、E014 | 按通道折叠 tool/thinking；进度汇总而非逐步刷屏 |
| Swarm/子 Agent 过程不回流主上下文 | Kimi
|------|------|------------|
| 无 DAG：不能 A 完成再启 B，需多轮 | Kimi Code Swarm E065 | 支持依赖边或分阶段门禁；UI 显示 blocked_by |
| 无子 Agent 横向通信 | E063、E065 | 一期可经 Lead 中转；如实宣传，勿称全互联 |
| 文件冲突靠提示词拆分无ç对功能含义 |
|------|------|------------|
| Claw 群聊须先关联再入群 | Kimi E068 | 若做即时 spawn，向导区分「登记已有」vs「创建临时环境」 |
| Coze/ Hermes shim、detect 缓存失败 | E050 | 探测缓存可| E034 | endpoint 类型标注能力边界（VCS/网络） |

### 3.7 配置漂移与占槽

| 摩擦 | 来源 | 对功能含义 |
|------|------|------------|
| manager 配置反复重E033 | 归档/回收与并发配额可视化 |
| 自动化不继承 Full access | Codex E028 | 「交互策略」与「无人值守策略」分开保存 |

---

## 4. 宣传叙.2 支撑句（必须连在一起说，禁止拆开当三个标题乱飞）

1. **派得到**：子任务落到指定本机 / 容器 / 已有 Claude·Codex·Cursor 会话，不是平台里的虚空并行。  
2. **看得住**：编排台条、全在云里跑 → 解决不了「必须在我这台环境 / 这份已有会话」。  
- 先登记一堆 Bot 再进群 → 缺「临时环境 / 指定容器拉起」。  
- 远
- 副标题：Lead 跨环境派工 · 执行端级编排台 · 可续上已有编码会话  
- CTA：连上一个本机或容器，跑通一次派工—回收  
- 边界（必写）：一期若只做钉钉，写清「办公研发远程；非å
以上只能当证明主卖点的材料，不能当标题。

### 4.6 客观评价（不美化）

- 逻辑差异相对近邻：**清晰**，适合做内部原则与叙事骨架point 做成登记列表，宣传仍是施工队、交付已是 Swarm/群聊仿制品。  
- **不宜**在验证前 All-in 对外宣称「我们的切入点已定」或「强于某竞品百分之几」。

---

## 5. 功能拆解（不可拆交付包 → 模块）

下表按「用户可感知能力」 会话）、常驻 Agent、临时容器、云沙箱；每端有 id、环境标签、能力声明 | 预注册 vs 即时 spawn 分型（对照 E068） |
| B. 生命周期 | 登记 / 创建 / 销毁临时环境；心跳；状态：online / degra（E004）；不嵌套（E066） |
| D. 执行端级编排台 | 任务列表、依赖/阶段、每 Worker 进度、日志入口、产物；展示在线与权限状态 | Swarm 可见水ilbox 卡（E020–E021） |
| F. 会话策略 | 「附着已有会话」与「新开进程」显式二选一；resume 行为诚实 | Coze spawn（E049）；Claude resume 丢队åª声折叠（E013） |

### 5.2 P1 — 体验与治理加深

| 模块 | 功能要点 | 吸收的摩擦 |
|------|----------|------------|
| 通道消息策略 | 折叠 tool/thin务目录隔离写路径 | 文件冲突（E065）；Kimi 工作空间习惯 |
| DAG / 阶段门禁 | blocked_by；阶段通过再放行 | E065 无 DAG |
| 成本与并行宽度 | 默è E033 |

### 5.3 P2 — 可后置（勿抢主叙事）

| 模块 | 说明 |
|------|------|
| 子 Agent 横向通信 | Kimi 仍在 roadmap（E063）；一期经 Lead 中转即可 |
069）；非差异核心 |
| 免费分时机池 | 可作一类 endpoint（原 W2）；禁止当主标题 |
| 纯 Web 多 Agent 炫技房间 | 已判假缝（W3） |

### 5.4 推荐一期切片（在不可拆原则下仍可薄）

在验证资源有限时，**薄包**建议为：

1. **一台本机 Host Bridge**（发现/附着已有 Claude·Codex 会话；**一机一条 WS，可挂多个 Agent**）+  
2. **第二台机器或临时容器各一 Bridge**（不是「每个 @name 一条连接」）+  
3. **编排台**：Bridge/Agent 在线、attach|fresh 明示、cancel  

> 纠正：旧「一种 Bridge = 一个 Agent」等同 Coze spawn 桥，已废止。见 [Agent_Team_Host_Bridge架构.md](Agent_Team_Host_Bridge架构.md)。

### 5.5 如何达到 Claude Teams / Kimi Swarm 水位（机制，不换卖点）

用户问「那两家形态都很好，我们怎么达到」。答案：**偷机制，不换主句。** 细节与证伪见操作手册 §19.6。

两家好在不同处：

- Claude：派工不会污染 Lead 对话；人能点进 Worker 自己的流；协调靠任务表/邮箱，不是把别人的 token 塞进自己历史。
- Swarm：同时跑多人时，任务清单 + 每位一轨，关页也能看。

我方达法（叠在 Host Bridge 上）：

| 阶段 | 必须可演示 | 否则会变成 |
|------|------------|------------|
| **P0 不脏不盖** | `@codex` 只跑 codex；Timeline 不进其他 Agent 的 prompt；SSE 按 `dispatch_id` 分轨 | 现在的串台 / 重复执行 / 主输出被覆盖 |
| **P1 Claude 协作水位** | 每 Agent 私有 session；Lead 只吃 summary/任务状态；编排台能点开 Worker 私有流 | 仿 Claude TUI、却在云里虚空 spawn teammate |
| **P2 Swarm 可见水位** | 任务清单 + 每 Worker 进度/cancel/产物；标签含真实机器与 attach\|fresh | 只做进度条、执行全在平台内 → 与 Swarm 撞车 |

一期薄包（§5.4）仍成立：先 Host Bridge 派得到；P0 是编排台「看得住」的**正确性**门槛，不是新卖点。

编排台的功能清单、四块 UI、三套存储、ContextGate 与事件契约见专章 → [Agent_Team_编排台_功能与架构.md](Agent_Team_编排台_功能与架构.md)。

---

## 6. 与需求/架构文档的衔接建议

| 主题 | 建议 |
|------|------|
| 用户画像 | 一期：已在用 CLI 的办公研发 + 需要第二环境 |
| 钉钉 | 可作入口；补微信缺口、飞书 P1 |
| 多执行环境 | 多 **Bridge**（多机/容器）；单 Bridge 可多 **Agent**；禁止再写「多 endpoint 各一条连接」 |
| Host Bridge | 以 [Agent_Team_Host_Bridge架构.md](Agent_Team_Host_Bridge架构.md) 为准 |
| 编排台 | 以 [Agent_Team_编排台_功能与架构.md](Agent_Team_编排台_功能与架构.md) 为准 |
| 下一步 | Wizard of Oz：配对 Bridge → 登记两 Agent → attach 一次可见；同屏两轨不串台 |

---

## 7. 文档索引

| 材料 | 路径 |
|------|------|
| 摘录库 | Agent_Team_evidence.csv |
| 竞争力调研（含 §18 Attach/绑定、§19 编排形态） | Agent_Team_竞争力调研操作手册.md |
| Host Bridge 纠正专章 | Agent_Team_Host_Bridge架构.md |
| 架构设计 | Agent_Team_架构设计.md |
| 编排台功能与架构 | Agent_Team_编排台_功能与架构.md |
| 需求 | Agent_Team_需求分析.md |

**本文结论再次强调：** 宣传叙事可用；功能按 §5 拆；摩擦按 §3 进设计；**勿把 Live attach（CLI 回显）当卖点**（§2.12）；达 Claude/Swarm 水位靠 §5.5 机制而非换主句。未做验证前，不升级为「已选切入点」或量化竞争优势。

