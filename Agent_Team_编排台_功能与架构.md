# Agent Team：编排台功能拆解与架构

> 版本：v0.4 | 日期：2026-08-18  
> 状态：**编排台专章**。宣传点仍是「派到真实执行端、盯得住、收回来」；本文只拆**好用的编排能力**与**可借鉴的结构**。  
> v0.2：将任务拆解、Agent 任务清单、人类看板、自动协调/并行、每位进展与结果、关页后台继续提升为一等能力（§1.5 / §3.4）。  
> v0.3：三层信息隔离（§0.3）；完结回执改为 Claude idle 语义——平台只写「已结束执行 / 失败」，**不再**截断 Worker 正文或再调 LLM 当结论。主时间线允许可折叠预览，与私有流独立；硬约束是过程不进 Lead、主会话不订 `team.stream` delta。  
> v0.4：Lead 默认不把任务板行 dump 进 prompt（只留 `project_id` 指针，查表走 `task_board_read` / `dispatch_result_read`）；§10 收齐调研过的各家编排台作参考。  
> 调研依据：[Agent_Team_竞争力调研操作手册.md](Agent_Team_竞争力调研操作手册.md) §18–§19；证据 [Agent_Team_evidence.csv](Agent_Team_evidence.csv) E001–E070。  
> 前置：[Agent_Team_Host_Bridge架构.md](Agent_Team_Host_Bridge架构.md)（派到哪台机器）、[Agent_Team_架构设计.md](Agent_Team_架构设计.md)（三平面）、[Agent_Team_PhaseA_实现规格.md](Agent_Team_PhaseA_实现规格.md)。  
> UI 落点：`ms-agent-webui` 侧栏 Agent Team（`/team`）；本仓库只提供 Team HTTP/SSE。

---

## 0. 一句话

**编排台是给人用的控制/观察面**：看活派到哪台机器上的哪个 Agent、会话是 attach 还是 fresh、跑到哪了、能不能停——并且 **Lead / 其它 Worker 的模型上下文不被工具过程污染**。

它不是 Lead Agent，不是钉钉群，不是 Claude 的 teammate TUI，也不是 Kimi 的平台内 Swarm 进度条。  
Host Bridge 解决「派到哪」；编排台解决「派出去之后人怎么盯、模型吃什么」。

### 0.1 宣传 vs 功能（允许不一样）

| 对外主句（不变） | 对内要达成的好用能力（可对标竞品机制） |
|------------------|------------------------------------------|
| 真实执行端（本机 / 容器 / 已有编码会话） | Claude：独立上下文、点开某一个 Worker、任务表协调 |
| 编排台看**执行端状态**（机器、在线、权限） | Swarm：任务清单、每位进展、关页后续看 |
| attach \| fresh 明示 | 扣子反例：spawn 冒充续会话——我们禁止 |
| （不换主句） | **拆解 → 双清单 → 自动协调与并行 → 每位结果 → 关页仍跑**（§1.5） |

### 0.2 三种编排 → 编排台只做哪一层

各家「编排台」不是同一种东西，禁止混谈。详细参考卡见 **§10**。

| 类型 | 在编排什么 | 调研对象 | 编排台态度 |
|------|------------|----------|------------|
| A. 会话协作 | Lead + 独立 teammate 怎么拆任务、互发消息、人怎么点进某一个 | **Claude Agent Teams**（对照：Claude Subagents） | **借鉴结构**：分轨、私有 session、结构化回执；不是 IM/跨机 |
| B. 平台内并行 | 一个任务下 spawn 大量同平台子代理，UI 看每位进展 | **Kimi Agent Swarm / Code Swarm** | **借鉴 UX**：每位进展、关页续跑、只回流结论；**不**把执行面收进平台虚空并行 |
| C. 执行端运维 | Worker 起在哪、在线否、生命周期 | **AgentTeams**、**Cursor Cloud**、**Codex 云**、**Kimi Claw 群聊** | **本义**：Bridge/Agent 生命周期与健康 |
| （相邻、不是小队编排） | 通道路由 / 远程本机 / 个人助理 | **OpenClaw**、**QwenPaw**、**Hermes**、**扣子 Local**、**Trae** | 可作执行端或通道；**不要**把编排台做成又一个 IM 助理或 spawn 冒充续会话 |

### 0.3 三层信息（人 / Lead / 私有流）

给人和给模型的不是同一份字。私有流和主时间线**互不影响**：有 C-05 不要求主会话变空白。

硬约束只有两条：**(1) 工具过程 / token 流不进任何其它 Agent 的 prompt； (2) 主时间线不得订阅 `team.stream` 的 delta**（那是并发盖台的根因）。人在主会话里看见预览气泡，只要标 display-only、排除出 SessionLog，就不破坏隔离。

| 层 | 给谁 | 内容 | 禁止 |
|----|------|------|------|
| **私有流**（C-05） | 人点进该 Agent | 该 `runtime_session_id` / `dispatch_id` 的完整过程：工具、多轮正文 | 不是本机 Claude/Codex TUI（Live attach 不做） |
| **主会话**（C-04） | 人 | 意图 + 系统回执（`已派` / `已结束执行`）+ **按 dispatch 分轨的结果卡**：默认短预览，下拉展开更多助手正文 | 主时间线订阅 `team.stream` delta；把工具 JSON / thinking 当主气泡默认展开 |
| **任务板 / mailbox**（C-06、C-12） | Lead | 默认只注入 **指针**（`[task_board] project=…` +「去查表」）；行数据走 `task_board_read`，正文走 `dispatch_result_read` | 把任务板行 dump 进每一轮 prompt；平台截断 transcript；平台再调 LLM 总结；Lead 默认拉完整 tool trace |

完结语义贴近 Claude idle notify：**通知「闲了 / 结束了」，不含输出。**  
这约束的是 **Lead 吃什么**，不是人在主时间线能不能看见预览。`dispatch_done` 写入 Timeline 的系统句是 `@codex 已结束执行`；失败则 `@codex 已结束执行（失败）`，可附短 `error_code`。预览卡另挂、display-only。完整工具过程只在私有流。

Lead **prompt 里不列** `completed @bibo dispatch=…`（任务一长会占满上下文，问候也会被带偏）。按需可取的范围：

| 取什么 | 谁 | 默认 |
|--------|----|------|
| 指针：`project_id` + 查表说明 | 每轮 Lead prompt | 有 teammate 任务时开 |
| `status` + `@` + `last_dispatch_id` + 产物路径 | Lead **主动** `task_board_read` | 开（工具，非预取） |
| 队友最终纯回复（不含工具 trace） | Lead **主动** `dispatch_result_read` | 开；对方没写文件也能读 |
| Worker **主动写下**的 `result_summary` | `task_board_read` 有则附 | 无则空（idle） |
| 主时间线预览卡 / 最后一轮助手正文 | 人 | 开（前端折叠）；**不**进 Lead |
| 完整工具过程 | **仅人**打开 Worker 轨 | Lead 默认关；若将来开「拉过程」工具须限长且人批准 |

---

## 1. 功能拆解（用户可感知）

按「人在编排台上能做什么」拆，不按内部类名拆。每条注明借鉴来源与我方差异。

### 1.1 P0 — 不脏、不盖、不串台（正确性门槛）

没有这些，后面的任务板/进度条都是假的。对应现网症状：`@codex` 卡片挂着另一 Agent 的「无法调用其他 agent」；Lead 重复执行已派任务；并发流互相覆盖。

| ID | 功能 | 人感知 | 借鉴 | 我方差异 |
|----|------|--------|------|----------|
| C-01 | **按 @ 精确派工** | `@codex …` 只跑 `@codex`；default lead **不得**静默附跑 | OpenClaw bindings：最具体优先 | 目标是已登记 endpoint，不是 Gateway persona |
| C-02 | **SSE / 气泡分轨** | 一路 `team.stream` 只写入该 `dispatch_id` 的 Worker 轨；「已结束执行」只绑同一 dispatch | Claude agent panel；Swarm 每位一轨 | 轨上要标机器 / attach\|fresh |
| C-03 | **归属校验** | 卡片 `at_name` 与事件 `at_name` 不一致 → 标 `attribution_mismatch`，禁止静默拼接 | Claude mailbox 校验坏条目 | 编排台可见错误，不靠 Esc 才刷新 |
| C-04 | **主会话：回执 + 可折叠预览** | 人那条下面：回执条 + `@codex` 结果卡（默认短预览，下拉展开正文）。工具过程不在这里默认展开 | idle 约束的是 Lead，不是把主会话掏空 | 预览 display-only，不进 SessionLog；**平台不代写摘要** |
| C-05 | **点开 Worker 看私有流** | 点卡片 / 「完整过程」进入**该 Agent 的 transcript**（工具过程可折叠） | Claude Enter 进 teammate | 与主时间线预览独立；绑 `runtime_session_id`，可跨 Web 重连续订 |
| C-06 | **Context 隔离** | 再问 Lead「做完了吗」时，Lead **不重新执行** Worker 任务；prompt 里只有查表指针，没有 Worker 全文或任务板行 dump | Claude 不继承 Lead 历史 + mailbox 拉取；Swarm 分片只报结论 | 见 §4；Timeline ≠ prompt；Lead 用 `task_board_read` / `dispatch_result_read` 按需拉 |

**P0 验收句**：同屏 `@codex A` 与 `@me B` → 两轨、两份 session、互不进对方 prompt；完成后问 Lead 进度 → Lead 先查任务板工具（无 Worker 全文、无行 dump）。

### 1.5 一等能力：拆解、双清单、自动协调与并行、每位进展、关页续跑

这些不是「看板的附属展示」，而是编排台要达成的**好用功能**。宣传仍不说「我们的 Swarm」；能力要对齐 Claude 任务表 + Swarm 可见并行。

同一条 `TeamTask` 记录，**两副面孔**：

| 面孔 | 给谁 | 形态 | 借鉴 |
|------|------|------|------|
| **任务清单** | Agent | 我能领 / 正在做 / 被谁阻塞；只看与自己相关的行 + brief | Claude 共享 task list（队友自领） |
| **任务看板** | 人 | 列：待办 / 进行中 / 完成 / 失败；卡片上有 `@name`、机器、耗时、摘要、产物 | Swarm 任务清单 UI；AgentTeams 房间里人看进度 |

禁止做成两套数据。Agent `task_board_read` 与人类 `GET /tasks` 读同一 store；投影字段不同（Agent 不看别人的 token 流）。

#### 1.5.1 任务拆解（C-40）

| | |
|--|--|
| **人感知** | 发一条大任务后，板上出现若干子任务（可改、可删、可指定 `@`）；不是只有一句「正在思考怎么拆」 |
| **谁拆** | **Lead 建议**（LLM）：调用 `task_board_write` 写下子任务（prompt、`target_at_name`、`blocked_by`）。**平台不替模型发明拆法**。人可在看板改指派/依赖后再点「按板执行」。 |
| **借鉴** | Claude：Lead 建任务、可指派或自领。Swarm：主 Agent 拆但缺硬 DAG。 |
| **不做** | 平台规则引擎按关键词自动拆（易错、难解释）；也不把拆解结果只写在 Lead 聊天里（污染 + 不可调度）。 |

拆完的落盘最小集：`task_id`、`prompt`、`target_at_name`（可空=待领）、`blocked_by[]`、`status=pending`、`parent_task_id`（根任务）。

#### 1.5.2 Agent 任务清单（C-41）

派给某 Agent 时，ContextGate 附带 **该 Agent 的 inbox 投影**，不是全项目看板 JSON：

```
可领:  T3 写测试（未阻塞）
进行中: T1 改 auth（本 dispatch）
等待:  T4 发版  blocked_by=[T1,T3]
```

工具：`task_board_read`（已有）+ 后续 `task_claim`（乐观锁，对标 Claude 文件锁）。  
平台完结只改 `status=completed|failed` + `last_dispatch_id`（idle）。`result_summary` **仅** Worker 用 `task_board_write` 主动写下才出现；**禁止**平台把 transcript 截进清单，也**禁止**完结时再调 LLM 总结。

#### 1.5.3 人类任务看板（C-42）

编排台右侧（或主会话下）Kanban：

- 列 = 状态；卡片 = 一个 `TeamTask`
- 卡片字段：标题（prompt 首行）、`@at_name`、Bridge/机器、`session_mode`、耗时、状态（已结束/失败）、产物链接、`blocked_by` 红点；摘要槽仅在 Worker 写过 `result_summary` 时出现
- 点卡片 → 打开该 `last_dispatch_id` 的 Worker 轨（C-05）
- 人可：改指派、改依赖、cancel、强制 fresh、手动标完成（须写原因，防与 runtime 双真相）

和主会话关系：主会话是叙事；看板是**进度真相**。Lead 问「做到哪了」只读看板。

#### 1.5.4 自动协调多 Agent（C-43）

今日代码明确写着：`blocked_by is informational — the platform will NOT auto-dispatch`（`TeamTaskBoardTools`）。编排台要达到 Claude/Swarm 的好用程度，必须改成：

```
Lead 只负责：拆解 + 可选指定 @
Coordinator（确定性组件，非 LLM）负责：
  扫描 pending 且 blocked_by 均 completed
  → 已有 target → dispatch 该 endpoint
  → 无 target → 按策略：default lead / 空闲 endpoint / 等人指派
  同一 endpoint 上多任务：PerEndpointQueue 串行
  不同 endpoint 上无依赖：立刻并行 enqueue
  完成/失败 → 再扫描（事件驱动，禁止 Manager 空转催，对标 E003）
```

| 自动 | 仍要人/Lead |
|------|-------------|
| 依赖满足后派工 | 第一次怎么拆、派给谁（可空） |
| 失败熔断、不把邻居任务重跑 | 失败后是否换人、改 brief |
| inbox 更新、idle 回执给 Lead（结束/失败 + 索引） | 队友互辩（C-30 后置）；平台代写结论 |

**硬约束**：Coordinator **不得**再把「找北京美景」塞进 Lead 的 user prompt 让它「也做一遍」。它只 `enqueue(DispatchEnvelope)`，Lead 最多收到 idle mailbox：`T1 ended by @codex`（失败则标明）；没有 Worker 主动写的 `result_summary` 就没有结论正文。

#### 1.5.5 Agent 并行（C-44）

| 规则 | 行为 |
|------|------|
| 跨 endpoint、无相互 `blocked_by` | **并行**（已有 `PerEndpointQueue` 按 endpoint 分队列） |
| 同一 endpoint | **串行**（避免同一 Claude/Codex 会话交错写） |
| 有 `blocked_by` | 上游 `completed` 之前不 enqueue |
| 并行宽度 | 配置 `max_inflight_global` / 每 Bridge 上限；超额排队；看板显示「等待槽位」（C-25） |

人感知：看板同时两张「进行中」卡片、两路 Worker 轨一起涨。这就是 Swarm「子代理并行」的体验；执行面仍是真实 `@codex` / `@me`，不是平台内 300 个虚空子进程。

人一次消息 `@codex A` `@me B`：Ingress 直接两 envelope，**不必**先经 Lead 拆解。Lead 拆解路径是「一条大任务、未 @ 多人」时的自动协调。

#### 1.5.6 每位进展和结果（C-45）

对标 Swarm「每位代理任务进展与结果」（E061），数据来自真实 dispatch：

| 层 | 内容 | 写入哪 |
|----|------|--------|
| 进展（活的） | 状态、耗时、最近 tool 名（可折叠）、token/步数可选 | `team.stream` + **私有轨**；**不**进 Lead prompt。主会话最多显示「进行中」卡，不订 stream delta |
| 完结（平台） | `已结束执行` / `已结束执行（失败）` + `last_dispatch_id` | Timeline 回执条；`task.update`；mailbox idle。**不含**正文 |
| 主时间线预览 | 助手正文短预览，下拉展开；点「完整过程」进私有流 | display-only `team_reply` / 结果卡；**不**进 Lead |
| 结论（可选） | Worker **自己写**的 `result_summary` + `output_artifacts[]` | `TeamTask`；有则进 Lead 看板投影 |

看板卡片同时显示进展点与完结态。主会话 = 回执 + 可折叠预览。完整工具过程只在私有流。平台**不**在完结时把助手全文截进 Lead 的结果槽。

#### 1.5.7 关闭页面后台继续（C-46）

对标 Swarm「关页也继续」。原则：**跑在 Control/Execution，不跑在浏览器。**

| | 关页后 | 再打开 `/team` |
|--|--------|----------------|
| Dispatch / Queue / Bridge / Cloud | 继续跑，不受 SSE 断开影响 | 用 `GET /tasks` + `GET /events?replay=` 恢复看板与轨 |
| 主会话回执 | 以 Timeline 短回执为准 | 刷新即可，不丢 |
| 未完成 stream | 服务端可把片段写入 dispatch log（建议补 `GET /dispatches/{id}`） | Worker 轨续订；允许「中间一段空白 + 已完成摘要」 |
| 钉钉 | 同样只收完成摘要，不刷 tool | — |

浏览器 `beforeunload` **不得** cancel dispatch。只有人点停止或 Lead/Coordinator 发 cancel 才停。

**验收句（§1.5）**：一条大任务 → 板上出现子任务 → 无依赖的两个 `@` 同时 in_progress → 关页 30s 再打开，状态与完结回执仍在且 dispatch 未中断 → Lead 只收到两条 idle 回执，未重做子任务。

### 1.2 P1 — 协作水位（Claude 机制；拆解/清单/协调见 §1.5）

| ID | 功能 | 人感知 | 借鉴 | 不做 |
|----|------|--------|------|------|
| C-10 | **任务板是协调源** | 见 **§1.5**：人类看板与 Agent 清单是同一 `TeamTask` 的两种视图 | Claude 共享 task list | 不是 CI；但 **Coordinator 会按板调度**（改现状「blocked_by 仅展示」） |
| C-11 | **依赖 / 阻塞 / 自动放行** | 未完成不可派；完成后 Coordinator 派下游 | Claude 任务依赖；Swarm 缺 DAG（E065） | 硬调度在平台，不靠 Lead 死循环催 |
| C-12 | **结构化回执（mailbox 语义）** | 平台完结 = idle（结束/失败 + 索引）。Worker **可选** `task_board_write.result_summary`，Lead 把它当「另一 Agent 主动交的结论」而非 transcript 切片 | Claude idle 不含输出；结论靠 SendMessage | 一期不做队友互辩；平台不代总结；不必落地 JSON 文件邮箱 |
| C-13 | **人直接跟 Worker 说话** | 在 `@codex` 轨里续一句，只派 codex，不经 Lead 再生成 | Claude 点进 pane 说话 | 不是注入本机 Claude TUI（Live attach 不做） |
| C-14 | **权限 / 审批浮到人** | Worker 要批的权限出现在编排台，由**人**批；Agent 不能互相代批 | Claude：队友不能代批 | 展示 Codex/Cursor 网络/沙箱状态（E027–E030） |
| C-15 | **Plan 门** | 可选：Worker 先出只读 plan，人/Lead 批了才改代码 | Claude plan approval | 默认关；高风险任务才开 |
| C-16 | **取消 / 熔断可见** | 一键 cancel 该 dispatch；同指纹空转熔断有卡片原因 | 吸收 AgentTeams 无法中断（E005）、死循环（E003） | — |

### 1.3 P2 — 可见水位（Swarm UX，数据来自真实 dispatch）

| ID | 功能 | 人感知 | 借鉴 | 不做 |
|----|------|--------|------|------|
| C-20 | **舰队健康** | 分列：机器 Bridge online/degraded/offline；其下各 Agent/会话健康；need_reauth | Host Bridge §4；Kimi E070 在线 vs 会话分列 | 不是 K8s 管控台 |
| C-21 | **每位进展与结果** | 见 **C-45**：进展在轨上，结果在看板卡片 | Swarm E061 | 不以「子代理数量」为 KPI |
| C-22 | **关页后台继续** | 见 **C-46**：Execution 不绑浏览器；再打开只续订 | Swarm 关页继续 | `beforeunload` 不得 cancel |
| C-23 | **产物槽** | 完成态挂 artifact / 路径 / PR 链接，作为权威结果 | Cursor/Codex 以 PR 回流；EvoMap 程序化汇合原则 | 不以 Lead 口述为唯一真相 |
| C-24 | **通道噪声折叠** | IM/Web 默认摘要；tool/thinking 仅在 Worker 轨展开 | QwenPaw E013–E014 | — |
| C-25 | **并行宽度与配额** | 明示本机/云并发上限与占用 | Cursor 并发 8 占槽（E033）；Claude token 线性涨 | 不宣传 300 并行 |

### 1.4 P3 — 可后置

| ID | 功能 | 原因 |
|----|------|------|
| C-30 | 队友横向互聊 / 对抗辩论 | Claude 有、Kimi roadmap；一期 Lead 中转足够 |
| C-31 | 嵌套 teams / 转让 Lead | Claude 官方限制：一会话一 team、Lead 固定 |
| C-32 | 恢复 in-process 全部 teammate | Claude 自己 resume 做不到（E022） |
| C-33 | Matrix 房间、平台内虚空 Swarm | 同台撞车；手册 W3 |
| C-34 | Live attach（远程命令回显到已开 CLI） | 调研范围内竞品均无；§18 禁止当卖点 |

---

## 2. 信息架构（人看见的五块）

对标 Claude「主会话 + panel」和 Swarm「清单 + 每位进展」，但主视觉必须露出**执行端**。任务看板是进度真相，不是聊天的附录。

```
┌─ 舰队 ─────────────────────────────────────────────────────────┐
│ Bridge: 笔记本 online · gpu-box degraded                         │
│   @me (claude, attach)  @codex (acp)  @reviewer (cloud)         │
├─ 任务看板（人类 · 进度真相）────────────────────────────────────┤
│ 待办 T4 发版 ⏳T1,T3 │ 进行中 T1 @codex  T3 @me │ 完成 …        │
├─ 项目主会话（人类意图 + 回执 + 可折叠预览）─────────────────────┤
│ 你: 把登录改完并补测试                                           │
│ 回执: 已派 @codex · attach · 已结束执行                          │
│ [@codex] 预览… ▾展开正文  [完整过程 → 私有流]                    │
├─ Worker 轨 / 私有流（工具过程在这里）───────────────────────────┤
│ [@codex T1] 流式… 工具… 多轮正文                                 │
│ [@me T3]    （另一路并行，互不写入对方气泡）                     │
└─────────────────────────────────────────────────────────────────┘
Agent 侧另有「任务清单」投影（inbox），不单独占一块人用 UI。
```

| 区域 | 数据源 | 写入模型？ |
|------|--------|------------|
| 舰队 | Bridge 心跳、`endpoint.status`、SessionBinding | 否 |
| **任务看板** | 同一 `TeamTask` store | Lead/Worker 只读 status + 索引；`result_summary` 仅 Worker 自写时 |
| 主会话 | Timeline：人 + idle 回执 + display-only 预览卡 | Lead 不吃预览卡、不吃 Worker 全文 |
| Worker 轨（每位进展） | 按 `dispatch_id` 的 `team.stream` | **仅该** endpoint session |
| Agent 任务清单 | 同一 `TeamTask` 的 inbox 投影 | 写入 claim/complete，不写别人过程 |

前端约束（`ms-agent-webui`）：

1. **禁止**全局唯一 `assistantBuffer`。Map 键 = `dispatch_id`（缺省则丢弃或进 mismatch）。
2. 主会话列表**不得**订阅 `team.stream` 的 delta（防多路盖台）。回执订 `dispatch_start/done/error`；结果预览用完结后的 display-only 记录，默认短预览、下拉展开。
3. Worker 轨组件按 `dispatch_id` 订阅 stream；卸载后重连用 SSE `replay` + `dispatch_id` 过滤。点预览卡「完整过程」打开该轨。

---

## 3. 控制面架构

### 3.1 三套存储，禁止混用

调研结论：Claude / Swarm 都把「给人看的热闹」和「给模型吃的上下文」拆开。现网污染来自 `ContextBundleAssembler` 把项目 Timeline 最近 20 条灌进每次 dispatch。

```
                    ┌─ TimelineStore ─────────┐
                    │ 给人看的多说话人日志     │  ← 编排台主会话 + 审计
                    │ 不默认进入 LLM prompt    │
                    └───────────┬─────────────┘
                                │ 仅 idle 回执 / Worker 自写结论可投影
┌─ SessionDirectory ────────────┼──────────── TeamTask 板 ─────────┐
│ 每 endpoint(+thread)          │            │ pending/running/done │
│ 私有 runtime_session_id       │            │ last_dispatch_id     │
│ → 该 Agent 的模型历史         │            │ result_summary（可选）│
│ → 该 Agent 的模型历史         │            │ blocked_by / artifacts│
└───────────────────────────────┴────────────┴─────────────────────┘
```

| 存储 | 现有类型 | 谁读 | 谁写 |
|------|----------|------|------|
| **Timeline** | `TimelineMessage` | 编排台、审计、IM 回执 | Ingress（人）、dispatch 完成写 **idle 回执**（已结束执行 / 失败）、system session_mode 行 |
| **Runtime session** | `SessionBinding` → ACP/`cloud` session | **仅该** endpoint 的模型 | 该 endpoint 的 dispatch 流 |
| **Task board** | `TeamTask` | 编排台看板、Lead 问进度 | Ingress 创建；平台更新 status；**仅 Worker** 可写 `result_summary` |
| **Mailbox（逻辑）** | `TeamEvent`（见 §5） | 目标 Agent 下一轮 prompt 的「来自 @x 的消息」 | idle `dispatch_done`（无正文）；可选 Worker 自写结论；人在 Worker 轨续话 |

### 3.2 组件图（叠在已有三平面上）

```
Channel          Web / 钉钉
                   │ InboundMessage（mentions[]）
                   ▼
Control          Ingress
                   ├ AtRouter          C-01 精确目标，禁止 default lead 附跑
                   ├ ContextGate       C-06 按 endpoint 组装 bundle（§4）
                   ├ SessionDirectory  attach|fresh 明示
                   ├ PerEndpointQueue  同 endpoint 串行，跨 endpoint 并行（C-44）
                   ├ TaskBoard         同一 store：人类看板 + Agent 清单
                   ├ Coordinator       依赖满足 → enqueue（C-43）；非 LLM
                   ├ EventBus          每条 TeamEvent 必带 dispatch_id + at_name
                   └ CircuitBreaker / Cancel
                   │
          ┌────────┴────────┐
          ▼                 ▼
Execution   Host Bridge     Cloud adapter
            （关页仍跑）     （关页仍跑）
```

编排台（Web）**只订阅 EventBus + 读上述存储**，不直接打 runtime stdin。

### 3.3 与 Host Bridge 的边界

| 问题 | 负责方 |
|------|--------|
| 这台电脑是谁的、上面有哪些 Agent | Host Bridge / Registry |
| 这一枪派给 `@codex`、session_mode | Ingress + SessionDirectory |
| 这一枪的 token 流显示在哪张卡 | 编排台（按 dispatch_id） |
| `@codex` 的模型下一轮看见什么 | ContextGate + 该 runtime session |
| 机器休眠 | HealthMonitor → 舰队红灯；下属 Agent 不可派 |
| 关页 | 无事发生；Execution 不绑 SSE 生命周期 |

### 3.4 Coordinator（自动协调，确定性）

与 Lead 分工：Lead = 拆解建议（LLM）；Coordinator = 按板派工（代码）。落点建议：`ms_agent/team/coordinator.py`，订阅 `task.update` / `dispatch_done`。

```
on task.update | dispatch_done | dispatch_error:
    ready = [t for t in board if t.status==pending and all(blockers completed)]
    for t in ready:
        if t.target_endpoint_id is None: continue  # 或 apply_idle_policy
        if t.last_dispatch_id still in-flight: continue
        enqueue(DispatchEnvelope(prompt=t.prompt, referenced_task_id=t.task_id, ...))
```

改现有工具文案：`blocked_by` **不再是纯展示**。`task_board_write` 完成后必须 `emit task.update`，以便 Coordinator 扫描。防 E003：同一 `task_id` 同时只允许一个 in-flight dispatch；熔断指纹含 `task_id`。

策略旋钮（配置，非 prompt）：

| 项 | 默认 |
|----|------|
| `coordinator.enabled` | Phase A 可先 `0`（只看板）；P1 开 |
| `auto_dispatch_on_unblock` | `1` |
| `unassigned_policy` | `wait_human`（空 target 不自动乱派） |
| `max_inflight_global` | 如 8 |

---

---

## 4. ContextGate：模型吃什么（P0 核心）

替换「Timeline 整段进 prompt」为按角色投影。

### 4.1 规则

| 派给谁 | 允许进入 `ContextBundle` / runtime 历史 | 禁止 |
|--------|------------------------------------------|------|
| **被 @ 的 Worker** | 本条 user prompt（已 strip 其它 @ 或保留本 @）；本 endpoint 自己的 session；可选 `referenced_task_id` 的 brief；git_snapshot / artifacts（若有） | 其他 Agent 的全文、工具过程、Lead 闲聊 |
| **Lead / default @me**（无 @ 或显式 @lead） | 人类意图；**任务板指针**（`project_id` + 查表说明）；舰队健康一句 | Worker token 流 / 工具过程；任务板行 dump（`completed @x dispatch=…`）；平台截断的 transcript；把「已派给 @codex 的原任务」再当 user prompt |
| **人在 Worker 轨续话** | 只追加到该 endpoint session | 不创建第二条 default-lead dispatch |

`merge_prompt` 不再拼接 `[project_timeline]` 全文。若需要项目背景，用独立字段 `project_brief`（人手写 / 固定 instruction），长度上限另计。

### 4.2 `ContextBundle` 增量

| 字段 | 说明 |
|------|------|
| `audience` | `worker` \| `lead` |
| `task_snapshots[]` | 内部仍可带 `{task_id, at_name, status, last_dispatch_id}`；**`merge_prompt` 不把行写进 Lead**，只留 `project=` 指针 |
| `task_inbox[]` | Worker 用：可领 / 进行中 / 等待（C-41） |
| `peer_receipts[]` | 可选，mailbox 投递到该 Agent 的短消息 |
| `project_timeline` | **废弃作为模型输入**；仅 TimelineStore 展示。过渡期：空列表 |

实现落点：`ms_agent/team/context.py` 的 `build` / `merge_prompt`；`ingress.py` 在 `router.resolve_targets` 之后按 **每个 target 各 build 一次** bundle（今日是多 target 共享同一 bundle，这是污染源之一）。

### 4.3 路由伪代码

```
mentions = parse(@)
if mentions:
    targets = lookup(mentions)          # 只这些
else:
    targets = [project.default_lead]    # 仅无 @ 时
for t in targets:
    bundle = ContextGate.build(audience=t, ...)
    enqueue(dispatch(t, bundle))
# 禁止: mentions 非空时再 append default_lead
```

---

## 5. 事件、对象与 API

### 5.1 事件（`TeamEvent`）

已有 kinds 保留。编排台渲染契约：

| type | 编排台动作 | 必填字段 |
|------|------------|----------|
| `team.dispatch_start` | 建回执条 + 建/打开 Worker 轨 | `dispatch_id`, `at_name`, `endpoint_id`；payload：`session_mode`, `prompt_preview` |
| `team.stream` | **只**追加到该 dispatch 轨 | 同上；payload：`text` 或 tool 片段 |
| `team.dispatch_done` | 轨完成；主会话回执更新为「已结束执行」；TaskBoard status | payload：`ok`；失败时 `code`。**不含** transcript |
| `team.dispatch_error` / `_cancelled` | 轨失败态；回执「已结束执行（失败）」 | payload：`code` |
| `team.session` | 回执上标 attach\|fresh\|fallback | payload：`session_mode`, `resolution` |
| `endpoint.status` | 舰队 | `endpoint_id`；可加 `bridge_id` |
| `task.update` | 任务板 | payload：`task_id`, `status` |
| `artifact.ready` | 产物槽 | payload：`artifact_id` |

**新增（P1）**

| type | 用途 |
|------|------|
| `team.mailbox` | 结构化回执投递给某 Agent：`from_at`, `to_at`, `kind=summary\|nudge\|plan\|human_followup` |
| `team.attribution_mismatch` | C-03；UI 必须展示，不得吞 |

SSE：`GET /projects/{id}/events?dispatch_id=` 可选过滤，供 Worker 轨续订。

### 5.2 `TeamTask` 增量（扩展现有，不另造 TaskSet）

| 字段 | 阶段 | 说明 |
|------|------|------|
| （已有）status / blocked_by / result_summary / target_* / output_artifacts | P0–P1 | 看板 + Agent 清单同一数据 |
| `last_dispatch_id` | P0 | 点开任务 → 打开哪条轨 |
| `parent_task_id` | P1（拆解根） | 不必等 Phase C 全树；先一层父子即可 |
| `claim_lock` / `version` | P1 | 防抢单 |
| Coordinator 语义 | P1 | `blocked_by` 全部 completed 后 **自动 enqueue**（改今日「仅展示」） |

Dispatch 完成时：平台只写 `status` + `last_dispatch_id` + idle 回执；**不**把 stream 截进 `result_summary`。全文只在该 runtime session / stream 日志。Worker 若要交结论，自己 `task_board_write`。

### 5.3 API（在现有 `/api/v1/team` 上）

| 已有 | 编排台用法 |
|------|------------|
| `GET /projects/{id}/events` | 主订阅；加 `dispatch_id` 过滤 |
| `GET /projects/{id}/timeline` | 主会话；客户端按 `sender_type` 分层，**不要**把 agent 全文当主气泡 |
| `GET/POST /projects/{id}/tasks` | 任务板 |
| `POST /projects/{id}/messages` | 入口；body：`mentions` 已由服务端 parse；`session_mode`；**Worker 轨续话**带 `target_at_name` + `thread_id` 绑该 session |
| `POST /dispatches/{id}/cancel` | 轨上的停止 |
| `GET /bridges`、`/endpoints/{id}/health` | 舰队 |

| 建议补 | 阶段 |
|--------|------|
| `GET /projects/{id}/dispatches/{id}` 含 stream 片段或 replay 游标 | P0 / C-46 |
| `POST /projects/{id}/mailbox` 或 messages 上 `audience=endpoint` | P1 |
| Timeline 查询 `?role=receipts\|human` | P0 可先前端过滤 |
| `POST /projects/{id}/coordinator/run`（可选手动「按板执行」） | P1 |

---

## 6. 关键时序

### 6.1 `@codex 找北京美景`（P0 正确路径）

```
人 Web 发送
  → Ingress 解析 mentions=[codex]
  → 只 build Worker bundle（无 timeline dump）
  → SessionDirectory attach|fresh
  → Event: dispatch_start {at_name=codex, dispatch_id=D1}
  → 编排台：主会话回执条 + 打开 @codex 轨
  → stream 仅进 D1 轨
  → dispatch_done {ok}（无正文）
  → Timeline 追加 idle 回执「已结束执行」（非全文）
  → TeamTask T1 completed + last_dispatch_id（不写 result_summary）
人: 「做完了吗」（无 @）
  → 只派 default lead
  → Lead bundle = `[task_board] project=…` 指针（不列 T1 行）
  → Lead 需要时调用 task_board_read / dispatch_result_read
  → Lead 回答进度，不跑「找美景」
```

### 6.2 同时 `@codex A` `@me B`

两 envelope、两 dispatch_id、两轨、两 session；Queue 按 endpoint 各串行。UI 两个卡片同时涨。

### 6.3 人在 @codex 轨续话

`POST messages` + `target_at_name=codex`（或 UI 当前轨上下文）→ 单 target；`session_mode=attach` 同一 `runtime_session_id`。

### 6.4 大任务：拆解 → 并行 → 关页再打开

```
人: 「改登录并补测试」（无 @ 多人）
  → 只派 default lead
  → Lead task_board_write:
       T1 改 auth  @codex
       T3 写测试  @me     blocked_by=[]
       T4 发版            blocked_by=[T1,T3]
  → Coordinator: T1、T3 无依赖 → 两路并行 enqueue
  → 看板两张 in_progress；两 Worker 轨同时涨
  → 人关页；Queue/Bridge 继续
  → T1、T3 dispatch_done → idle 回执落盘（status + last_dispatch_id）
  → Coordinator: T4 unblock → 若已指定 target 则派；否则等人指派
  → 人再打开 /team：GET /tasks 恢复看板；replay 恢复轨或只见完结态
  → Lead 仅收到两条 idle mailbox，不重做 T1/T3
```

### 6.5 人一次 @ 两个 Agent（跳过拆解）

与 §6.2 相同：Ingress 直接并行。平台仍各建 `TeamTask` 以便看板/关页恢复（即使 Lead 没拆）。

---

## 7. 分期与现有 Phase 对齐

| 编排台 ID | 建议挂靠 | 代码主落点 |
|-----------|----------|------------|
| C-01–C-06、C-20 健康只读 | **立刻 / Phase A 补强** | `router.py`、`context.py`、`ingress.py`、`api_events.py`、webui `/team` 分轨 |
| C-40–C-42 拆解落盘 + 双清单/看板只读 | Phase A 末 | `TeamTask` 每次 dispatch 必建；Lead 工具 `task_board_write`；UI Kanban |
| C-43–C-46 Coordinator 自动派工、并行槽位、dispatch log、关页恢复 | Phase A 末 / Phase B 前 | **新** `coordinator.py`；改 `blocked_by` 语义；`GET /dispatches/{id}` |
| C-12–C-16 mailbox / 权限 / plan | 同上 | mailbox 事件、权限卡片 |
| C-21–C-25 UX 加深 | 与编排台同期 | 折叠 tool、配额条 |
| C-30+ | 不做或 Phase C+ | — |

Phase A 原验收「看得见 stream」必须改读为 **分轨看得见**。单一 assistant 缓冲里能看到字 = **不合格**（§19.5）。

前端：`ms-agent-webui` `/team`；不要往 `modelscope-agent/webui/frontend` 加页面。

---

## 8. 成功标准 / 不合格

合格：

1. 去掉「真实 Bridge 执行端」后主卖点不成立（Host Bridge 专章，不变）。
2. 去掉「分轨 + ContextGate」后，P0 验收句失败 → 编排台不合格。
3. 舰队能区分 **Bridge offline** 与 **Agent unavailable**。
4. Lead 问进度不重复执行已派任务。
5. §1.5 验收句：拆解落盘、无依赖并行、关页后续跑、完结进看板（idle），不进 Lead 重跑。

不合格（即使 UI 很好看）：

- 进度条上的 Worker 是平台内虚空子代理，无 `bridge_id` / cloud endpoint。
- 主会话 transcript 被多路 stream 覆盖。
- `merge_prompt` 仍带其他 Agent 全文、任务板行 dump，或平台截断的 Worker 正文。
- 完结时平台再调 LLM「总结一遍」当作结论。
- 宣传 Live attach 或「我们的 Claude Teams / 300 Swarm」。

---

## 9. 文档索引

| 材料 | 关系 |
|------|------|
| 操作手册 §19 | 竞品编排形态与达水位假说（本文 §10 是编排台视角的摘录） |
| Host Bridge 专章 | 一机一桥、多 Agent、attach |
| 架构设计 | 三平面、TeamTask 扩展原则；**编排台细节以本文为准** |
| Phase A | 接口基线；分轨列为「看得住」验收 |
| 定位叙事 §2.13 / §5.5 | 宣传与机制的关系 |
| evidence.csv E001–E070 | 带 URL 的行为证据 |

**一句话**：编排台 = 舰队 + **任务看板（人）** + **任务清单（Agent，同一数据）** + 主会话（回执 + 可折叠预览）+ 分轨私有流；Lead 默认只拿查表指针，Coordinator 按板并行派工；关页只断 UI，不断 Execution。结构学 Claude/Swarm，执行面仍是用户的机器与会话。

---

## 10. 调研过的编排台（参考卡）

> 用途：设计时对照「别人在编排什么、人怎么看、模型吃什么」。**不是功能打分表，不是宣传口径。**  
> 分层：A 编码运行时 / B 通用 Agent / C 协作平面。对象不在同一层，禁止做成八家功能清单互抄。  
> 全文与证伪条件：[Agent_Team_竞争力调研操作手册.md](Agent_Team_竞争力调研操作手册.md) §19；证据 [Agent_Team_evidence.csv](Agent_Team_evidence.csv)。

### 10.0 一张总表

| 产品 | 层 | 类型 | 编排对象 | 人怎么看 | 模型吃什么 | 勿照搬成我方主句 |
|------|----|------|----------|----------|------------|------------------|
| Claude Agent Teams | A | A. 会话协作 | 本机 Lead + teammate 会话 | panel / tmux；Enter 进某一个 | teammate 不继承 Lead 历史；idle 不含输出 | 「我们做 Claude Teams」 |
| Claude Subagents | A | （对照，非小队） | 主 Agent 调短任务子代理 | 主会话里出结果 | 只能向主 Agent 汇报，无互聊 | 把 Subagent 当成 Teams |
| Kimi Agent Swarm | B/C | B. 平台内并行 | 同平台海量子代理 | 任务清单、每位进展、关页续跑 | 分片小本子，只报结论 | 「我们做 300 子代理进度条」 |
| Kimi Code Swarm / CLI | A | B 的编码形态 | 主从并行写代码 | CLI；过程不回流 | 无硬 DAG、无横向通信 | 当完整小队 OS |
| Kimi Claw 群聊 | C | C. 多端预关联 | Conductor + 已关联 Claw | Thread / 侧栏 | Thread ≠ 主群记忆 | 「预登记群 = 即时 spawn」 |
| AgentTeams（HiClaw） | C | C. 房间+容器 | Manager → Leader → Workers | 进 Matrix 房间；HITL | 分房/分容器；制品 MinIO | 再造房间产品 |
| OpenClaw | B | 通道路由 | Gateway 多 persona | IM 按机器人分气泡 | 各 Agent 自有 memory | 路由当成小队协作 |
| QwenPaw / CoPaw | B | IM 助理 | 一实例多通道 | 钉/飞/微气泡 | 通道常把 tool/thinking 刷屏 | 编排台做成全能 IM 助理 |
| Hermes | B | 执行端 | 本机/容器 Worker | 终端或经通道 | 可靠性叙事，Team 证据薄 | 当主竞品 |
| Cursor Cloud Agents | A | C. 云作业 | 每任务独立 VM | Agents 列表 / PR / 日志 | 任务隔离；可关本机 | 只做云 VM 并行 |
| Codex 云 / Desktop | A | C. 云/本机沙箱 | 每任务沙箱 | 作业 / PR；网络默认收紧 | 任务隔离 | 权限门当装饰 |
| 扣子 Local Agent | C | 远程本机通道 | 单本地 bridge | 扣子侧一条聊 | **spawn 新进程**，非续会话 | spawn 冒充 attach |
| Trae / trae-agent | A | （低优先级） | 研究向 CLI | 无 IM Team 产品面 | 框架改造非周活 | 当协作平面 |

我方目标：**C 为主，吸收 A/B 机制**（独立上下文 + 分轨可见 + 结构化回执），执行面仍是真实 Host Bridge / 已有编码会话。

---

### 10.1 Claude Code Agent Teams — 本机小队 OS（A）

资料：https://code.claude.com/docs/en/agent-teams （约 v2.1.178+；实验特性，默认关，`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`）。

**编排什么**：一个 Lead 会话 + 多个独立 Teammate 会话，同一台机器并行。不是远程 IM、不是跨机。

```
你 ↔ Lead（主会话）
      ├─ 共享 Task List（pending / in_progress / completed，可依赖）
      ├─ Mailbox（~/.claude/teams/{team}/inboxes/{agent}.json）
      └─ Teammates（各自独立 context window）
            ├─ 可互相 SendMessage
            └─ 人可直接进某个 teammate 说话
```

**与 Subagent 勿混**（官方对照）：

| | Subagents | Agent Teams |
|--|-----------|-------------|
| 上下文 | 自己的窗口，结果回主 Agent | 完全独立窗口 |
| 通信 | **只能向主 Agent 汇报** | **队友之间可直接互聊** |
| 协调 | 主 Agent 全管 | 共享任务表 + 自协调 |
| 适合 | 短任务、只要结果 | 要讨论、对抗、并行探索 |
| Token | 较低（摘要回流） | **很高**（每人一份完整 Claude） |

**人怎么看**：默认 in-process 主终端下方 agent panel（↑↓ 选队友 → Enter 看 transcript / 直接发话；Esc 打断；Ctrl+T 任务列表）。Idle 行会折叠，**进程仍在**。可选 tmux / iTerm2 每队友一块 pane。

**模型吃什么**：

- 每个 teammate **自己的 context window**；spawn 时加载 CLAUDE.md、MCP、skills + Lead 给的 spawn prompt。
- **不继承 Lead 对话历史**。
- 中间思考/工具过程 **不会**自动灌进 Lead 主 transcript。
- Lead 收到的是消息与任务状态。idle notify = 「闲了」，**不含输出**（结论靠 SendMessage / 任务表）。

**协调**：Lead 建任务，可指派或自领；文件锁防抢单；A 完成才 unblock B；可选 plan approval；权限弹窗浮到 Lead 由**人**批，队友不能代批；Hooks：`TeammateIdle` / `TaskCreated` / `TaskCompleted`。

**已知坑**：mailbox / Waiting for results 卡住要 Esc/击键（E020、E021）；任务完成状态滞后要手动 nudge（E023、E024）；in-process `/resume` **不恢复 teammates**（E022）；一会话一 team、无嵌套、token 线性涨。

**我方偷**：独立上下文、点开某一个 Worker、任务表当协调源、idle 不含输出、人直接跟 Worker 说话、权限由人批。  
**不偷**：本机 TUI 当产品面；云里虚空 spawn teammate 扮小队；把 mailbox JSON 文件当必须形态（事件语义即可）。

---

### 10.2 Kimi Agent Swarm / Code Swarm — 平台内可见并行（B）

资料：https://www.kimi.com/zh-cn/help/agent/agent-swarm ；https://www.kimi.com/blog/agent-swarm ；CLI https://moonshotai.github.io/kimi-code/zh/customization/agents.html ；评测 https://blog.csdn.net/weixin_43236007/article/details/162702297

**编排什么**：主 Agent（指挥官）对一个用户任务 **动态 spawn 大量同平台子代理**（宣传可到 ~300，E062）。执行面在 Kimi 算力，不是用户 cwd / 自有容器。

**人怎么看（产品卖点，E061）**：任务清单；子代理并行；推理链路、工具与网址；**每位**进展与结果；关页后台继续。这是「编排可见」的高水位，接近编排台 UX，但卡片上的 Worker 是平台内进程。

**模型吃什么**：

- **上下文分片**：子 Agent 各记「小本子」，**只把关键结论报指挥官**（E064）。
- CLI：子过程思考/工具 **不回流主历史**（E066）。
- 与 Claude 对照：Claude = 队友互聊 + 本机独立会话；Swarm = 规模并行 + UI 可见 + **要求回流结论、过程隔离**。

**缺口**：peer 通信、DAG、并行宽度可控仍弱（E063、E065）。Code Swarm 常无「A 完再启 B」硬依赖边，无子 Agent 横向通信，文件冲突靠拆分。

**我方偷**：每位一轨、关页后续跑、过程不进 Lead、结论可拉。  
**不偷**：子代理数量当 KPI；平台内虚空并行进度条当主句。我方结论拉取用 `dispatch_result_read`（对方没写文件也能读），**不强制** Worker 必须先交一份总结（与 Swarm「必须报结论」不同，更接近 Claude idle + mailbox 拉取）。

---

### 10.3 Kimi Claw 群聊 — 预关联多端（C）

资料：https://www.kimi.com/zh-cn/help/kimi-claw/kimiclaw-group-chat ；介绍 https://www.kimi.com/zh-cn/resources/kimi-claw-introduction

**编排什么**：Kimi Conductor 调度 **已经关联** 的 Claw（云端 KimiClaw / 本机 OpenClaw / Android Claw）。须先选已关联实例入群，再调度——**不是**运行时在任意新容器里即时 spawn（E067–E068）。

**人怎么看**：Thread 入口看进度、侧栏工作空间、围观（E069）。与 Swarm 的差别：这里编排的是**不同机器上的执行端**，但是预登记名单。

**摩擦**：关联本机 OpenClaw 后群里 @ 无响应，先查私聊是否在线（E070）；合上笔记本本地 OpenClaw 会停，云端可持续（E059）。编排台必须分列 **Bridge/机器在线** vs **会话健康**。

**我方偷**：多端可见、Thread ≠ 主记忆。  
**不偷**：把「预登记 Bot 列表 + 群调度」说成「指定容器即时 spawn」。

---

### 10.4 AgentTeams（原 HiClaw）— 企业房间 + 容器（C）

资料：https://github.com/agentscope-ai/agentteams ；Manager 指南 https://github.com/agentscope-ai/HiClaw/blob/main/manager/agent/worker-agent/AGENTS.md ；峰会口径 https://www.alibabacloud.com/blog/agentteams-and-claude-tag-both-enter-group-chatmode-is-it-a-new-paradigm-or-a-new-narrative_603358

**编排什么**：K8s 风格 CRD reconcile → 起容器 Worker + Matrix 账号；Manager → Team Leader → Workers；人机同房。Worker 可共存 OpenClaw / QwenPaw / Hermes（E012）。仅有效 @mention 才唤醒，无 mention 静默丢弃（E055）。

**人怎么看**：进房间看；治理 / HITL / 权限分级（L1/L2/L3）。通道宣称钉/飞/企微，同时可用 Element+Matrix 绕过企业审批（E009–E011）。

**模型吃什么**：分房 / 分容器；制品上 MinIO；不是共享一份主 transcript。

**已知坑（必须吸收进编排台）**：

| 摩擦 | 证据 | 对我方 |
|------|------|--------|
| Human 进房仍无 @ 权限 | E001 | 「在房」≠「有执行权」；须回执，禁止静默 |
| Manager 不在 Worker allowFrom，消息被忽略 | E002 | Lead→Worker 默认信任链 |
| Manager 死循环催同一清理 | E003 | Coordinator 事件驱动，禁止空转催 |
| 询问关系时又建一套一模一样的团队 | E004 | 幂等创建；查询 vs 变更分开 |
| 处理中无法中断 | E005 | 一键 cancel（C-16） |
| manager 配置反复重置 | E006 | 配置持久化 |

**我方偷**：声明式生命周期、凭证与执行分离、HITL、可中断。  
**不偷**：房间即产品、全家桶依赖、再造 Matrix。

---

### 10.5 OpenClaw — Host Gateway 路由，不是小队（B）

资料：钉钉连接器 https://github.com/DingTalk-Real-AI/dingtalk-openclaw-connector/ ；多 Agent 配置见其 `docs/MULTI_AGENT_SETUP.md`。

**编排什么**：长驻 Gateway；`channel+peer → agentId` **bindings，最具体优先**。多 Agent = 同一 Gateway 内多 persona（独立 workspace/session），**不是**动态跨机 spawn，也不是任务表小队。

**人怎么看**：IM 按机器人分气泡。同群可 @ 不同 Agent（E040、E041）；钉钉文档可做异步接力。

**摩擦**：macOS 休眠后通道挂、会话静默重置、`.jsonl` 被改名为 `.reset.*`（E036–E039）；个人微信群覆盖缺口（E042、E043）；weclaw 可把微信接到本机 Claude/Codex（E044）——那是远程通道，不是编排台。

**我方偷**：@ 只达被提及者（C-01）；一机一 Bridge、多 Agent。  
**不偷**：把通道路由当成「小队协作」；不管用户已开的编码 CLI 会话。

---

### 10.6 QwenPaw / CoPaw — 国内 IM 助理（B）

资料：https://github.com/agentscope-ai/QwenPaw ；频道 https://qwenpaw.agentscope.io/docs/channels ；可作 AgentTeams Worker（E019）。

**编排什么**：一实例多通道（钉钉推荐、飞书、微信、Discord、Telegram）。Coding Mode 有入口；**兼容 Claude Code 仍 Planned（E017）** → 续本机 CLI 未闭环。

**人怎么看**：IM 聊。摩擦是飞书/钉钉多步工具任务 **过程消息刷屏**（E013、E014）——即使 `show_tool_details=false` 仍不断发调用通知。

**我方偷**：按通道折叠 tool/thinking（C-24）；钉钉通道适配可参考。  
**不偷**：把 Team 做成又一个全能 IM 助理。

---

### 10.7 Hermes — 可作 Worker 的执行端（B）

部分用户从 OpenClaw 迁出求稳（E045）；睡眠/断网恢复比 OpenClaw 强（指数退避、DNS 回退，E046）。可被 AgentTeams 用作编码 Worker（E012）。公开周活与 Team 证据薄。

**我方定位**：Worker 类型之一，不构成编排台主竞品。跨机后「endpoint offline」仍是编排台红灯问题。

---

### 10.8 Cursor Cloud Agents — 云 VM 作业（A/C）

资料：https://cursor.com/docs/cloud-agent

**编排什么**：每任务独立 VM；可关本机；多仓改代码并开 PR；远程桌面接管；结束可附截图/视频/日志（E031）。并行靠 git worktree；目前偏 GitHub（E034）。

**人怎么看**：Agents 列表 / PR / 制品。Pro 并发上限 8；删环境后旧记录仍占槽，要到 cursor.com/agents 归档才释放（E033）。企业视角：止于 PR，无生产部署/BYOC（E035）。

**我方偷**：关页后台继续、产物槽、配额可见（C-25）。  
**不偷**：只做云 VM 并行（与 Cursor/Codex 无差异）；执行面必须仍能是用户本机 cwd。

---

### 10.9 Codex 云 / Desktop — 沙箱作业 + 权限门（A）

资料：https://developers.openai.com/codex/agent-approvals-security

**编排什么**：云端 agent 阶段默认离线；本地默认无网络 + 工作区。每任务沙箱隔离，结果以 PR/制品回流。

**摩擦**：push 报 hostname / sandbox 不允许（E027）；Desktop 自动化忽略 Full access、静默落到 workspace-write（E028）；配置了 `network_access=true` 新会话仍 DISABLED（E029）。**交互策略与无人值守策略必须分开保存。**

**我方偷**：任务卡片展示网络/审批状态（C-14）；Cloud/ephemeral adapter 受同一套 endpoint 生命周期约束。  
**不偷**：权限门当装饰、配置与生效不一致。

---

### 10.10 扣子 Local Agent — 远程本机，spawn≠续会话（C）

资料：https://docs.coze.cn/cozespace/local_agent ；拆解 https://developer.cloud.tencent.com/article/2681646

**编排什么**：coze-bridge；人不在电脑旁可在扣子侧对话、本机执行（E048）。市场已教育「远程动本机」。

**关键行为**：每次派工 **spawn 新子进程**，不是附着用户已开着的 CLI 会话（E049）。官方自承：须保持 bridge 运行并建议盒盖不休眠；关机/断网/休眠则断开，掉线需重连（E047）。接入 Hermes 等需 shim，缓存失败要清 `frameworksCache`（E050）。

**人怎么看**：扣子侧一条聊；无多 Agent 编排产品面。

**我方差异必须落在**：「Host + 多 Agent + Session attach 明示」，禁止 spawn 冒充续会话。Live attach（远程命令回显到已开 TUI）调研范围内竞品均无，禁止当卖点（手册 §18）。

---

### 10.11 Trae / trae-agent — 研究向 CLI（A，低优先级）

https://github.com/bytedance/TRAE-agent ：文件/bash/轨迹记录的软件工程 Agent，非 IM 房间式 Team（E051）。多 Agent 多为框架改造，不是用户周活证据（E052）。不当协作平面，不当编排台对标。

---

### 10.12 机制对照：该偷什么

| 机制 | Claude Teams | Kimi Swarm | 房间/云作业 | 我方落地 |
|------|--------------|------------|-------------|----------|
| 独立上下文 | teammate 不继承 Lead 历史 | 子代理分片，只报结论 | 分房 / 分 VM | 每 endpoint(+thread) 私有 runtime session |
| 回流 | idle 不含输出；结论靠 mailbox | **要求**回流结论 | PR / MinIO 制品 | 人看预览卡；Lead **按需** `dispatch_result_read`（不 dump 行、不代总结） |
| 协调总线 | 共享 task list + 文件邮箱 | 平台内调度 | 房间 @ / 作业队列 | `TeamTask` + EventBus；Coordinator 按板 enqueue |
| 人看的面 | panel / tmux | 每位进展 UI | 房间或 Agents 列表 | Web：主会话回执+预览；点开私有流；舰队露执行端 |
| 人跟 Worker 说话 | Enter 进 teammate | 较弱 | 房间 @ | `@codex 补充` 只派 codex |
| 规模 | 建议 3–5 | 宣传 ~300 | 容器/并发配额 | 真实执行端数量；不用子代理数当 KPI |

禁止的达法：在云里再 spawn 一堆 ms-agent 扮 teammate 再画 Swarm 进度条——与 Kimi 正面撞车，且丢掉「真实执行端」。
