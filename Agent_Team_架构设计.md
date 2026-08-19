# Agent Team 架构设计

> 版本：v0.4 | 日期：2026-08-06  
> 前置文档：Agent_Team_定位叙事与功能拆解.md、Agent_Team_竞争力调研操作手册.md、Agent_Team_编排台_功能与架构.md、research/agent_team/*  
> 性质：在公开框架架构调研 + 本仓库已有代码之上的**目标架构假说**；强多少与周活未验证。  
> 说明：外链为完整 URL，可整份外贴。  
> v0.2：增补 §3.9，从 EvoMap 蜂群两场实验反推依赖的工程能力清单（吸收原则，非改卖点为 Swarm）。  
> v0.3：§3.3.1 落定子任务载体——扩展 `TeamTask`（+ 可选 Plan 壳），不另造平行 `TaskSet` 类型树。  
> **v0.4（纠正）**：废除「Bridge ↔ Agent 1:1 / 每 Agent 一条 WS」。根因是未把「发现已有 Agent 并 attach」当一等需求。正确形状见专章 → [Agent_Team_Host_Bridge架构.md](Agent_Team_Host_Bridge架构.md)（一机一 Bridge、多 Agent、Discovery→Bind→Attach）。下文若与专章冲突，**以专章为准**。  
> **竞品对照（2026-08-13）**：Live attach 调研范围内不存在（§18）。各家编排形态与「如何达 Claude Teams / Swarm 水位」见操作手册 **§19**；架构只吸收机制（独立上下文 + 分轨可见），不换卖点为 Teams/Swarm。

---

## 0. 设计目标（从不可拆功能包反推）

对外交付包（见定位文档）：

1. **派得到**：子任务落到指定本机 / 容器 / 已有编码会话（真实执行端）。  
2. **看得住**：编排台看执行端状态（在线、进度、日志、产物），非仅模型内并行。分轨与 Context 隔离见 [Agent_Team_编排台_功能与架构.md](Agent_Team_编排台_功能与架构.md)。  
3. **收得回**：停 / 重派 / 审批 / 断连可管；ånt.md ；https://hiclaw.io/

**结构要点**

```
用户/CLI (hiclaw apply)
    → kube-apiserver 风格 CRD：Worker / Team / Human / Manager
    → agentteams-controller reconcile
        → 起容器/Pod + Matrix 账号 + MinIO 空间 + Gateway consumer token
    → 协作总线：Matrix rooms（@mention 唤醒）
    → Higress：LLM/MCP 凭证代理（Worker 不持真密钥）
```

- **协作单元**：Manager → Team Leader → Workers；Manager 不穿透进 Team Worker（产品规则）。  
- **通信**：房间内可见、可 HITL；不是点对点 RPC 任务总线。  
- **存储**：Worker 无状态，制品上 MinIO。

**优点**

- 声明式 + reconcile：期望态与真实态收敛，适合企业运维。  
- 凭证与执行分离（Gateway consumer token）。  
- 人机同房审计天然。  

**缺点 / 与摩擦对应**

- 权限/白名单与房间邀请不同步 → 「进房仍无权限 / Manager 消息被忽略」（调研 E001、E002）。  
- 控制面重：K8s/嵌入式 apiI / CLI / Nodes   ─┼→  Gateway（长驻，WS :18789）
                              ├─ session / presence / health / cron
                              ├─ bindings：channel+peer → agentId（最具体优先）
                              └─ Agent Runtime（workspace + skills + 模型）
```

- **多 Agent**：同一 Gateway 内多 persona（独立 workspace/session），靠 **bindings 确定性路由**，不是动态跨机 spawn。  
- **Skills**：目录 + 按需读 SKILL.md，不全量塞进 system prompt。

**优点**

- 通道归一、配置驱动路由清晰；单机部署心智简单。  
- bindings 可测、可解释（相对 LLM 自路由）。  
- Node 角色可扩展设备能力（摄像头等）。  

**缺点 / 摩擦**

- 单 Gateway 进程成单点；休眠/网络导致通道挂、会话重置（E036–E039）。  
- 多 Agent ≠ 多机器；跨设备要另挂实例再被上层（如 Kimi 群聊）聚合。  
- 对我方：可借鉴 **bindings / 最具体优先** 与åstdin/stdout ldjson（ACP）
```

- **关键行为**：每次派工 **spawn 新子进程**，不是附着用户已开着的 CLI 会话。  
- 本机 `~/.coze/agents/<id>/` 可复活；device 心跳批量上报。

**优点**

- 协议分层干净：云私有协议 vs 开放 ACP。  
- daemon + 心跳 + 单实例锁，运维模型清楚。  
- 市场已教育「远程动本机」。  

**缺点 / 摩擦**

- 合盖休眠即断（官方自承 E047）。  
- spawn≠续会话 → 与「接着改那次会话」错位（E049）——**我方差异点**。  
- 框架探测缓存失败需 shim（E050）。  
- 对我方：Bridge daemon + ACP 路径应对齐；**session_id 稳定附着**与 **endpoint 健康态**必须强过 Coze。

---

### 1.4 Claude Code Agent Teams — 本机文件邮箱 + 共享任务表

**资料**：https://code.claude.com/docs/en/agent-teams  
**展开**：操作手册 **§19.1**；编排台参考卡见 [Agent_Team_编排台_功能与架构.md](Agent_Team_编排台_功能与架构.md) **§10.1**。

**结构要点**

```
Lead 会话（TUI）
    ↔ teammates（独立上下文，in-process 或 tmux pane）
    ↔ 共享 task list（可依赖、可自领）
    ↔ mailbox（~/.claude/teams/.../inboxes/*.json）
```

- 队友 **不继承** Lead 对话历史；过程不灌进主 transcript。
- 人可直接进某个 teammate；权限弹窗浮到 Lead 由人批。
- 已知坑：mailbox 卡住要击键（E020–E021）；resume 丢队友（E022）；任务完成滞后（E023–E024）。

**对我方**：偷「独立上下文 + 任务表 + 结构化回执」；**不要**对外宣称做成 Claude Teams，也不要在云里虚空 spawn teammate 扮小队。

---

### 1.5 Kimi Agent Swarm / Claw 群聊 — 平台内并行 vs 预关联多端

**资料**：https://www.kimi.com/zh-cn/help/agent/agent-swarm ；https://www.kimi.com/zh-cn/help/kimi-claw/kimiclaw-group-chat ；Code Swarm 评测 https://blog.csdn.net/weixin_43236007/article/details/162702297  
**展开**：操作手册 **§19.2–§19.3**；编排台参考卡见 [Agent_Team_编排台_功能与架构.md](Agent_Team_编排台_功能与架构.md) **§10.2 / §10.3**。

**Swarm**

- 主 Agent 动态生成大量 **同平台** sub-agent；上下文分片，**只回流结论**（E064、E066）。
- UI 强：任务清单、每位进展、工具与来源（E061）。
- 缺口：执行面非用户容器；peer/DAG 弱（E063、E065）。

**Claw 群聊**

- Conductor + **已关联** KimiClaw / OpenClaw / Android Claw。
- Thread 拆任务；Thread 不污染主群记忆；须预注册（E067–E068）。

**对我方**：编排台 UX 可对标 Swarm 的「每位一轨」；控制面必须是 **Endpoint + Dispatch + Lifecycle**，不是平台内虚空并行。达水位路径见 §19.6 / 定位文档 §5.5。

---

### 1.6 QwenPaw / 扣子通道 — IM 助理，不是小队编排

**QwenPaw**：钉钉通道适配可参考；**不要**把 Team 做成又一个全能 IM 助理（续本机 CLI 仍 Planned，E017）。  
**扣子**：bridge spawn，无多 Agent 编排产品面（E049）。

---

### 1.7 Cursor Cloud / Codex — 云沙箱 Worker

**结构要点**

- 每任务独立 VM/沙箱；结果以 PR/制品回流；本机可不在线。  
- 默认网络收紧；自动化权限易与交互配置不一致（Codex E027–E028）。  

**优点**：隔离与「关电脑继续」。  
**缺点**：执行面≠用户 cwd；配额/归档占槽（Cursor E033）。  
**对我方**：作为 **adapter_kind=cloud / ephemeral** 的一种后端，受同一套 endpoint 生命周期与权限状态机约束。

---

### 1.8 框架架构对比一览

| 框架 | 控制面 | 执行面 | 协作总线 | 可借鉴 | 勿照搬 |
|------|--------|--------|----------|--------|--------|
| AgentTeams | K8s CRD reconcile | 云容器 Worker | Matrix 房间 | 声明式生命周期、凭证代理 | 房间即产品、全家桶依赖 |
| OpenClaw | 单 Gateway | 同机 Agent 进程 | Channel bindings# 2. 本仓库已有骨架（必须长在这上面）

当前 Phase-1 已存在，架构演进应 **增量**，避免并行第二套。

### 2.1 模块地图

| 区域 | 路径 | 职责 |
|------|------|------|
| 领域模型 | ms_agent/team/models.py | AgentEndpoint、DispatchEnvelope、TeamTask、Timeline… |
| 入口管道 | ms_agent/team/ingress.py | MessageIngress、EndpointRegistryService、PerEndpointQueue |
| 路由 | ms_agent/team/router.py | @ 解析与在线检查 |
| 策略 | ms_agent/team/policies.py | InvokeGate、RemoteProfile |
| 事件 | ms_agent/team/events.py | TeamEvent |* | registry、dispatch、artifacts、ws_bridge_hub、cloud_runner |
| ACP/A2A | ms_agent/acp/*、a2a/* | 协议栈已有，**尚未接入 Team dispatch 主路径** |

### 2.2 ç ProjectResolver → AtRouter → ContextBundle
  → PerEndpointQueue
       ├─ adapter=acp  → BridgeHub WS → BridgeDaemon → AcpClaudeAdapter
       └─ adapter=cme（默认 dry_run）
```

### 2.3 已实现 vs 缺口（相对不可拆包）

| 能力 | 现状 | 缺口 |
|------|------|------|
| Endpoint 注册/配对 | 有 | 缺「创建临时容器」一等 API；类型字段已有 persistent/ephemeral |
| 跨机在线 | Bridge WS + status | 缺统一心è_id） |
| 编排可见 | Timeline/Tasks API、内部 event_subscribers | **无对外 UI 事件流**；Task board 偏观测非调度 |
| 停/重派 | Bridge cancel 部分 | 缺全局/按任务取消与熔断（防 E003 类循环） |
| Spawn Codex adapter 仍 stub |
| 远程权限 | 模型字段已留，默认 owner_only | remote_invoke 总开关默认关 |

**结论**：仓库已是「邮局 + 储物柜 + 本机桥」雏形；目标架构 = 把 **Lifecycle + SessionAttach +构

### 3.1 原则

1. **三平面分离**：Channel（人入口）/ Control（注册·路由·任务·事件）/ Execution（各 endpoint 运行时）。  
2. **Endpoint 一等公民**：一切派工以 endpoint_id 为键；云/本机/容器同一状态机。  
3. **协议适配在边缘**：平pawn**：Dispatch 显式 `session_mode=attach|fresh`；默认策略可配置但必须可见。  
6. **智能编排可选**：Lead Agent 可建议拆分；**硬调度与熔断在确定性组件**，避免 Manager 死循环。  
7. **汇合é──────────────────── Channel Plane ───────────────────────────┐
│  Web UI  │  DingTalk  │  (P1 Feishu)  │  未来企业 IM                 │ 订阅 TeamEvent（SSE/WS）
┌───────────────▼───────────────────────▼─────────────────────────────┐
│                     Control Plane  SessionDirectory          │
│  TaskGraph（P1 DAG）│ PerEndpointQueue │ EventBus → UI/通道回执      │
│  ArtifactStore │ TimelineStore │ Token/Pairing                       │
└────────────â              │ DispatchEnvelope      │ LifecycleCmd
                │ (prompt,ctx,session)  │ (create/stop/heartbeat)
┌───────────────▼─â──────┐
│ Execution: Bridge Path   │  │ Execution: Managed Path                 │
│ BridgeDaemon (用户机)    │  │ Spawner（平台/用户集群）                │
│  RuntimeAdapter          │  │  ephemeral container / EAS / cloud VM   │
│   ├ AcpClaude (attach)   │  │  内嵌 Bridge 或 cloud adapter           │
│penClaw (P1)        │  │                                         │
│   └ Hermes (P1)          │  │                                         │
└──────────────────────────┘  â扩展）

| 对象 | 职责 | 相对现状 |
|------|------|----------|
| AgentEndpoint | 执行端身份、adapter_kind、capabilities、owner | 已有；补 health、session_cffline/need_reauth + last_seen + reason | **新增**（吸收休眠摩擦） |
| SessionBinding | (endpoint_id, thread_id|project_id) → runtime_session_id | **新增**（续会话尝试）；增加 session_mode、parent_dispatch_id、cancel_token；`referenced_task_id` 指向 part | 扩展；**不是**任务集载体 |
| TeamTask | 任务板条目；**兼作原子 part 载体**（见 §3.3.1） | 扩展 parent / slot |
| TaskPlan（可选壳） | 整包目标的覆盖规则与 merge 契约；节点仍是 TeamTask | Phase C 按需æ**新增** |
| TeamEvent | 统一流：dispatch_*、health_*、lifecycle_*、stream | 扩展 kinds |

#### 3.3.1 子任务载体结论：扩展 TeamTask，不另造 TaskSet

实é槽位）。子任务不能只活在 prompt 或 Timeline 气泡里。但这**不等于**再发明一套与现有模型抢职责的 `TaskSet` / `SubTask` 类型树。

三层职责分开：

| 对象 | 职责 | 适不适合当「任务集| **适合扩展成 part** |
| `DispatchEnvelope` | 一次派到某 endpoint 的运输单；同一 part 可重派多次 | **不适合**；只表示「这一枪」 |
| `Artifact` | 结果落盘 / 汇合读取源 | 汇合读这里，**不是**拆分载体 |
| Timeline | 人机可见叙事 | 可引用 part，点 = 原子 part**；`DispatchEnvelope.referenced_task_id` 指向某个 part。覆盖检查 =「该 plan/根下所有叶子是否都有成功 Artifact（或显式缺口）」。  
2. **需要显式汇合契约时（Phase C）**：再加薄壳 `T `DispatchEnvelope` 当任务集；用纯聊天记录当唯一拆分真相；与 `TeamTask` 抢同一语义的第三套「TaskSet 产品对象」。

与 §3.9.1 对齐：E1 要的æ²有会话（W1 核心）

```
用户钉钉/Web @agent 下指令
  → Ingress 鉴权 + 路由到 endpoint E
  → SessionDirectory：查 (E, thread) 是否有 runtime_session_idh（须在 Timeline 明示）
  → Queue → BridgeHub.dispatch(envelope)
  → BridgeDaemon → Adapter.execute(session_id=稳定 id)
  → 事件流：stream → dispatch_done / failed
  → 若 ACP session/load 失败 → 升 need_reauth 或提示 fresh（勿静默）
```

#### B. 临时容器 Worker（W4 核心）

```
Lead 或用户请求「在隔离环境跑」
  → LifecycleCoordinator.create_ephemeral(spec)
  → Spa成后按 TTL destroy（或保留供验收）
```

#### C. 编排可见

```
所有 Dispatch/Health/Lifecycle 写入 EventBus
  → UI SSE/WS 订阅（补齐现状缺口）
  → 通é
### 3.5 组件职责（落地到目录）

| 组件 | 建议落点 | 说明 |
|------|----------|------|
| LifecycleCoordinator | ms_agent/team/lifecycle.py（新） | 创建/销毁/é/session_dir.py（新） | 稳定 session 映射；对接 ACP load |
| EventBus 对外 | webui/.../api_events.py（新） | 订阅 state.event_subscribers |
| Spawner | ms_agent/team/spawners/*（新） | 本地 docker / EAS / 空实现可测 |
| Adapter 补齐 | bridge/adapters/* | Codex/OpenClaw/Herme用 acp/proxy spawn | 与 MSAgentACPServer 能力对齐 |
| 熔断 | ingress / queue | 同命令重复 N 次熔断（对标 E003） |
| 消息策略 | channel_* | filter_tool_messages 级配置 |

### 3.6 与 ACP / A2A 的关系

| 协议 | 在目标架构中的角色 |
|------|-------------------|
|*：dispatch、stream、heartbeat、cancel |
| A2A | **可选远程 Agent 调用**：可作为 adapter_kind 之一，不替代 Endpoint 模型 |
| OpenClaw Gateway | **通道ånt 连接 |

勿把 A2A/ACP 服务器本身当成 Team 产品；它们是执行与互通工具。

### 3.7 状态机（Endpoint）

```
unregistered
    → pairing → registered(offline)
        → heartbeat ok → online
      degraded → offline
        → auth fail → need_reauth
ephemeral: online → draining → destroyed
```

派工仅允许 online/busy；degraded 可只读或拒绝å：

- Phase 默认 **owner_only**（InvokeGate + Bridge assert）。  
- 放开远程调用时：allowlist + RemoteProfile；**禁止静默丢消息**（对标 E002：必须回执 `forbidtTeams Gateway，不必上 Matrix）。  
- 临时容器：网络/挂载最小权限；编排台展示 sandbox 标志（对标 Codex 网络坑）。

### 3.9 EvoMap 两场实验 → 依赖的工程能力清单

**来源**：https://evomap.ai/zh/blog/how-ai-swarms-win-from-26-to-71-percent （2话汇总中丢失）。

**用法**：下列清单回答「若要复现实验所依赖的执行面，系统要具备哪些工程件」——用于加固架构原则与 Phase C 加深项；**不是**定摘要：任务被完整拆成原子 part → 每 part 独立 Agent、独立上下文 → 结果写入约定位置 → **程序按题号/ID 汇合**（无第二模型改写、无中央角色再取舍）。相对 Sub-Agent：主 LLM 拆解 → 子 Agent 报告 → 主 LLM 再综合，损耗大。

| # | 工程能å®处理者与输出位 | **扩展 `TeamTask`**（`parent_task_id` / `part_key`）；Dispatch 只 `referenced_task_id` 引用；见 §3.3.1 | Phase C 加深；Phase A 可先用 Timeline 手工标注 |
| E1-2 | **完整覆盖检查**（part 集合 ∪ = 目标ähecklist |
| E1-3 | **尽量细的拆分策略**（鼓励原子化，可配置粒度上限） | 子任务小 → 边界清、可追踪、易并行 | Lead 建议拆分可选；**硬校败 | `DispatchEnvelope` + PerEndpointQueue；endpoint/session 隔离 | Phase A 已部分具备；忌单会话塞满子报告 |
| E1-5 | **约定落盘 / Artifact 槽位**（按 `part_id` 写固定路径或结构化记录） | Agent 只负责解题**程序化汇合（Programmatic Merge）** | 正确答案不再经 LLM 压缩/取舍；实验胜出关键因 | Merge 算子：按 ID 收集 → 格式校验 → 组装交付物；Lead 综合降为**可选复核** | **原则级吸收**；工程任务可部分程序化（清单/文件树），不可全盘ç起遵守 |
| E1-8 | **Part 级追踪与可观测**（状态：pending/running/done/failed；谁在做、产物在哪） | 原子任务可追踪 | EventBus：`dispatch_*` + part ç **失败可重试、不污染邻居**（单 part 失败重派；不影响已落盘正确结果） | 局部失败不拖垮整表汇合 | cancel + 按 `part_id`/`dispatch_id` 重派；已成功 Artifact 不可静默覆写 | Phase A canc或明确缺失」 | Phase B/C；与 ephemeral spawn 可组合 |
| E1-11 | **结果权威源声明**（Artifact / 文件 / 结构化表为 source of truth） | 「程序负责汇合」的前提 | Timeline 展示引用权威源；通道回执å 主模型读报告」的高损耗形态。

#### 3.9.2 实验二（不写死全部组织关系时，Agent 能否开始自己选人、长结构）

实验设定摘要：同构 Agent 做题并写入可积累 memory（Gene/`skill`）→ 形成专长侧重与正确率身份 → 圆桌关系网断è----|------------------|----------|
| E2-1 | **可积累的专长/经验存储**（按题型或任务标签的心得；非一次性 prompt） | Gene/`skill` 场累积，影响下次**非一期**；勿与 Bridge 能力混写 |
| E2-2 | **身份派生（专长向量 + 质量指标）** | 「物理侧重 + 正确率」非预写角色，而是履历 | `Endpoint`/`AgentProfile` 可扩展 observed_skills、success_rate；需评测回路 | 远期；依赖可信评测数据 |
| E2-3 | **同伴关系图（Peer Graph）** | 圆桌边、断边、重连的载体 他端隐私与内部指标 | 若做推荐，必须可配置 |
| E2-5 | **自主选伴 / 重连 API**（候选集、一次选择、记录理由可选） | 实验最小动作「选择伙伴」 | 可选：协作建议工具；**硬路由仍以 @ / b**：沿边交换经验、转交任务、找互补者、失败换人 | 网络从「形状」变成路由与经验传播 | 若做：边 = 推荐路由；真正派工仍走 Dispatch/Queue/」 | **先具备 §3.9.1**，再谈 E2；与 §3.1「智能可选、调度/熔断必硬」同构 | E1 底座 → 再 E2 |

**实验二能力包（一句话）**：`履历记忆 + 可观æ
|------|----------------|
| E1 的程序化汇合、Part 隔离、覆盖对账、反 LLM 传话损耗 → 写入原则与 Artifact；part 载体见 §3.3.1 | 把产品改叙事为「自组织蜂群 / 准确率 71%」；另造与 TeamTask 平行的 TaskSet 类型树 |
| 「自由发生在人选与组合，协议死在覆盖与格式」→ 强化熔断与 session_mode 可见自己长关系」。

---

## 4. 摩擦 → 架构机制映射

| 调研摩擦 | 架构机制 |
|----------|----------|
| 进房无权限 / 白名单漏配 | Registry 与 ACL 同事务更新；dispatch 前显式授权检查 + 错误码 |
| Manag需击键 | EventBus/队列与 UI 解耦 |
| resume 丢队友 | SessionDirectory；不假装跨 dispatch 恢复未声明的 team |
| 通道 tool 刷屏 | Channel 出站过滤器 |
|aph；workspace 路径隔离 |
| 仅预注册 | LifecycleCoordinator 区分 register_existing vs create_ephemeral |
| spawn≠续会话 | session_mode 一等字段 |
| 配置漂移 / å°（与功能 P0/P1 对齐）

### Phase A — 薄不可拆包（建议先做）

> **实现规格**：`Agent_Team_PhaseA_实现规格.md`（接口 / 字段 / 时序 / 验收 / PR 切ç台展示。  
3. UI/通道 **Event SSE**（看得见派工与在线）。  
4. cancel / 失败回执 / 熔断。  
5. 钉钉入口保持薄（可 WoZ）。  

验收：删掉「真å¼docker 或已有云资源）。  
2. ephemeral endpoint 全流程：create → online → dispatch → destroy。  
3. Codex/OpenClaw adapter 至少一个非 stub。  

### Phase C — 加深

1. TaskGraph / 覆盖对账 / 程序化汇合：. 受控放开 remote_invoke。  
4. 多租户与凭证代理加固。  
5. （可选研究）§3.9.2 同伴图 / 履历身份——不进入不可拆卖点包。  

**明确不做 KPI；不另造与 `TeamTask` 抢职责的平行 `TaskSet`/`SubTask` 类型树。

---

## 6. 关键决策记录（ADR 摘要）

| 决策 | 选择 | 理由 |
|------|------|------|
| 协作总线 | 平台 Timeline + EventBus，而非 Matrix | 避同台 AgentTeams；轻量 |
| 本机协议 | ACP + Bridge WS | 对齐生态与现有 AcpClaudeAdapter；可做 attach差异 | session_mode=attach 一等 | 调研缝 |
| 与 Kimi 差异 | 用户环境生命周期 + 非仅预注册 | 调研缝 |
| 达 Claude/Swarm 水位 | 偷独立上下文 + 分轨可见；不换卖点 | 操作手册 §19.6；编排台专章 |
| 复用代码 | 扩展 ms_agent/team + bridge + webui/team威源 | EvoMap 实验一：NL 传话汇总高损耗（§3.9） |
| 子任务载体 | 扩展 `TeamTask`（parent/slot）+ 可选薄 `TaskPlan`；Dispatch≠任务集 | §3.3.1；避免第三套 TaskSet 本体论 |
| 自组织选伴 | 后置于 E1足，停在 A（Bridge+可见）并缩叙事。  
2. attach 在各 CLI 上不可靠 → 诚实降级 fresh，并改卖点。  
3. Spawner 运维成本高于收益 → ephemeral 只接托管云，不做自建 K8s。  
4. 实现偷工只做 Event UI → 架构评审按 §0 验收句否决。  

---

## 8. 文档与代码索引

| 材料 | 位置 |
|------|------|
| 定位/åw_swarm.md |
| 领域实现 | ms_agent/team/、ms_agent/bridge/、webui/backend/team/ |
| AgentTeams 架构 | https://github.com/agentscope-ai/AgentTeams |
| OpenClaw 架构 | https://dopts/architecture |
| Claude Agent Teams | https://code.claude.com/docs/en/agent-teams |
| Coze 本地 Agent | https://docs.coze.cn/cozespace/local_agent |
| EvoMap 蜂群实验（汇åomap.ai/zh/blog/how-ai-swarms-win-from-26-to-71-percent ；能力清单见 §3.9 |

---

**一句话**：近邻里，AgentTeams 教「声明式生命周期」、OpenClaw 教「通道路由与心跳」、Coze 教「Bridge+ACP」也暴露「只 spawn」、Claude Teams 教「别把轮询绑 UI」、Kimi 教「可见性水位与预注册上限」；EvoMap 实验则教「é¡型兑现「派得到、看得住、收得回」，而不是再造房间 OS 或平台内 Swarm。

