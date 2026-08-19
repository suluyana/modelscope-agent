# Agent Team Phase A 实现规格

> 版本：v0.3 | 日期：2026-08-06  
> 前置：`Agent_Team_架构设计.md`（v0.4）、`Agent_Team_Host_Bridge架构.md`、`Agent_Team_定位叙事与功能拆解.md`、`Agent_Team_编排台_功能与架构.md`  
> 性质：**可开工的一期实现规格**（接口 / 字段 / 时序 / 验收 / 不做清单）；不是全量详细设计。  
> 范围：仅 **Phase A — 薄不可拆包**。Phase B（LifecycleSpawn）与 Phase C（TeamTask 树 / 程序汇合）不在本文交付内，仅标注边界。  
> v0.2：对照落地代码补充 S1 实现备注与 API 路径。  
> **v0.3**：按 Host Bridge 纠正——**一机一 Bridge WS**，其上挂多个 **Agent**；废除 `/endpoints/pair` 1:1 绑 WS。发现已有 runtime 并 attach 为一等路径。  
> **UI**：真正的前端在独立仓库 `/Users/luyan/workspace/ms-agent-webui`（侧栏「Agent Team」→ `/team`）。  
> Team HTTP 仍由 `modelscope-agent` 的 `webui/backend/team` + `ms_agent/team` 提供，经 `ms-agent-webui` 的 `team_mount` 挂载（`MS_AGENT_TEAM_ROOT`）。  
> **不要**再往 `modelscope-agent/webui/frontend` 加 Team 页面。

---

## 0. 一期要证明什么

用户可感知结果（删掉任一项则主卖点句不成立）：

| 卖点 | Phase A 必须可演示 |
|------|-------------------|
| **派得到** | Web（必做）/@ 钉钉（可 WoZ）把指令送到**已配对 Host Bridge** 上登记的 Agent（真实 runtime，优先 Claude ACP attach） |
| **看得住** | 编排台经 **SSE** 看到：Bridge/Agent 在线态、dispatch 起止、流式片段；不依赖刷新 REST。**分轨**：`team.stream` 按 `dispatch_id`/`at_name` 归属，禁止写入单一 assistant 缓冲（对标 Claude panel / Swarm 每位一轨；手册 §19.5–§19.6 P0） |
| **收得回** | 可 **cancel** 进行中的 dispatch；失败有明确错误码/回执；**session_mode=attach\|fresh** 显式且写入 Timeline |

**验收句（对内）**：去掉「真实 Bridge 执行端」或「附着/新开会话二选一可见」后，对外主句是否仍成立？仍成立 → 不合格。

---

## 1. 范围

### 1.1 In Scope（必须交付）

| ID | 工作包 | 一句话 |
|----|--------|--------|
| A1 | SessionDirectory + attach/fresh | 稳定 `runtime_session_id`；禁止再用 `dispatch_id` 冒充 session |
| A2 | EndpointHealth 状态机 | online / degraded / offline / need_reauth；心跳驱动；编排台可见 |
| A3 | Event SSE | 对外订阅 `TeamEvent`；与 UI 焦点解耦 |
| A4 | Cancel + 失败回执 + 熔断 | 按 dispatch 取消；forbidden/failed 不静默；同指纹空转熔断 |
| A5 | 钉钉入口薄或 WoZ | 已有通道可继续；允许人工代发，但 Web 路径必须闭环 |

### 1.2 Out of Scope（明确不做）

- LifecycleCoordinator / Spawner / 临时容器一等 API（Phase B）
- `TeamTask.parent_task_id` / TaskPlan / 程序化汇合 / 覆盖对账（Phase C；原则仍遵守：不把 LLM 汇总当唯一真相）
- Peer Graph、自组织选伴（§3.9.2）
- Matrix 房间、平台内虚空 Swarm、飞书/微信必做、remote_invoke 默认放开
- Hermes / OpenClaw / Codex adapter 去 stub（可留接口，不验收）
- 华丽多 Agent 时间线但无真实 Bridge

### 1.3 尖刺（编码大批量前先跑）

| Spike | 问题 | 通过标准 | 失败降级 |
|-------|------|----------|----------|
| S1 | ACP / Claude 能否 **load 已有 session** 并续写 | 同一 `runtime_session_id` 两次 dispatch，第二次能看到前次上下文痕迹（或官方 API 明确支持 load） | `attach` 标为 unsupported → 仅 `fresh` + Timeline 明示；改卖点措辞 |
| S2 | **Bridge**（非单 Agent）心跳在合盖/断网后进 degraded/offline；下属 Agent 不可派 | 停心跳 >T1 → bridge degraded；>T2 → offline；恢复 → online；发 `bridge.status` / agents unavailable | 调阈值；至少 offline 可见 |
| S3 | 同机 **多 Agent** 共用一条 Bridge WS | 两 `@name` dispatch 均经同一 `bridge_id` 连接 demux | 不合格则退回架构纠正前不可验收 |

**实现备注（S1）**：控制面已落地稳定 `runtime_session_id` + `session_mode`。Host Bridge 主路径为 **真 ACP**（长驻 `codex-acp` / `agent acp` / Claude ACP + `session/load|new`）。默认 `MS_AGENT_SESSION_ATTACH_FALLBACK=error` 与 `MS_AGENT_ACP_ATTACH_FALLBACK=error`——禁止静默 fresh。交互 TUI / IDE Composer **不可附着**。已删除 `codex exec` 旁路与 Team `.env` 灌 key。

**门禁**：S1 结论写入 ADR 后再把 attach 标为默认强承诺；S1 未闭环不阻塞 A2–A4。

---

## 2. 现状基线（相对缺口）

| 能力 | 落地后 |
|------|--------|
| Session | `SessionDirectory` + envelope.`session_mode` / `runtime_session_id` |
| Health | `HealthMonitor`：degraded / offline / need_reauth + `endpoint.status` 事件 |
| 事件 | `GET /api/v1/team/projects/{id}/events` SSE |
| Cancel | `POST /api/v1/team/dispatches/{id}/cancel` → Bridge `cancel` |
| 熔断 | `CircuitBreaker`（project+endpoint+prompt 指纹） |
| 钉钉 | 保持薄入口 / WoZ |

关键落点：`ms_agent/team/`、`ms_agent/bridge/`、`webui/backend/team/`。

---

## 3. 数据模型增量

### 3.1 `DispatchEnvelope`（扩展）

| 字段 | 类型 | 说明 |
|------|------|------|
| `session_mode` | `attach` \| `fresh` | 必填（平台解析后写入） |
| `runtime_session_id` | `str \| None` | attach 时非空；fresh 时由目录分配 |
| `cancel_token` | `str \| None` | 可选；可与 dispatch_id 同 |
| `parent_dispatch_id` | `str \| None` | 重派链 |
| `session_resolution` | `bound` \| `created` \| `attach_fallback_fresh` \| `forced_fresh` | 审计 |

### 3.2 `SessionBinding` / `EndpointHealth`

见 `ms_agent/team/models.py`。查找键：`(endpoint_id, project_id, thread_id)`。

### 3.3 事件 kinds

`team.session`、`team.dispatch_cancelled`、`team.circuit_open`、`team.dispatch_error`、`endpoint.status` 等（见 `events.py`）。

### 3.4 错误码

`forbidden` / `endpoint_offline` / `endpoint_degraded` / `session_attach_failed` / `need_reauth` / `cancelled` / `circuit_open` / `bridge_unreachable` / `internal`（稳定字符串，见 `errors.py` 常量）。

---

## 4. 组件与落点

| 组件 | 路径 |
|------|------|
| SessionDirectory | `ms_agent/team/session_dir.py` |
| HealthMonitor | `ms_agent/team/health.py` |
| CircuitBreaker | `ms_agent/team/circuit.py` |
| Event SSE | `webui/backend/team/api_events.py` |
| Cancel API | `webui/backend/team/api_dispatch.py` |
| Health/Sessions API | `webui/backend/team/api_registry.py` |

---

## 5. 接口契约（落地路径）

前缀：`/api/v1/team`

| Method | Path |
|--------|------|
| `POST` | `/bridges/pair-token`、`/bridges/pair` |
| `GET` | `/bridges`、`/bridges/{bridge_id}` |
| `POST` | `/bridges/{bridge_id}/agents`、`/bridges/{bridge_id}/tokens` |
| `GET` | `/bridges/{bridge_id}/candidates` |
| `GET` | `/projects/{project_id}/events` |
| `GET` | `/endpoints/{endpoint_id}/health`（Agent 级；实现名仍为 endpoint） |
| `GET` | `/endpoints/{endpoint_id}/sessions` |
| `DELETE` | `/endpoints/{endpoint_id}/sessions/{binding_id}` |
| `POST` | `/dispatches/{dispatch_id}/cancel` |
| `POST` | `/projects/{project_id}/messages`（body 可带 `session_mode`） |

**废除本机路径**：`POST /endpoints/pair`（1 Agent 1 WS）。Cloud Agent 仍可用 `POST /endpoints` upsert（无 `bridge_id`）。

Bridge WS：鉴权 **仅** `BridgeToken` → 连接键 `bridge_id`。下行 `dispatch` / `cancel`；上行 `heartbeat`（可含 `agents[]` / `candidates[]`）/ `stream_event` / `dispatch_done`。

---

## 6. 配置项

| 变量 | 默认 | 含义 |
|------|------|------|
| `MS_AGENT_TEAM_PERSIST` | `0` | 文件持久化（含 sessions.json） |
| `MS_AGENT_BRIDGE_HEARTBEAT_MISS_S` | `45` | → degraded |
| `MS_AGENT_BRIDGE_OFFLINE_S` | `120` | → offline |
| `MS_AGENT_SESSION_ATTACH_FALLBACK` | `fresh` | attach 无绑定时：`fresh` \| `error` |
| `MS_AGENT_SESSION_ATTACH_SUPPORTED` | `1` | `0` 强制 forced_fresh |
| `MS_AGENT_CIRCUIT_N` | `3` | 失败次数阈值 |
| `MS_AGENT_CIRCUIT_WINDOW_S` | `600` | 窗口秒 |
| `MS_AGENT_TEAM_CLOUD_DRY_RUN` | `0` | `1` 时 cloud endpoint 仅回显；默认走真实 LLMAgent |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | — | cloud `@agent` 必需（或 `~/.ms_agent/settings.json` providers） |

---

## 7. 测试

- `tests/team/test_phase_a.py`：Session / Health / Circuit / Queue cancel / Ingress session+circuit
- `tests/team/test_team_core.py` 等既有用例保持通过

---

## 8. 开放问题

1. Claude ACP 是否支持跨进程 session load？→ **S1 未闭环**（控制面已就绪）  
2. SSE replay：当前内存环约 256 条 + 请求时 `replay` 参数  
3. 编排台：分轨订阅契约见 [Agent_Team_编排台_功能与架构.md](Agent_Team_编排台_功能与架构.md)；`ms-agent-webui` `/team` 禁止单一 assistant 缓冲  
4. 钉钉自动化回执：允许 WoZ  

---

**一句话**：Phase A 按 **Host Bridge** 纠正——一机一 sidecar、多 Agent、发现候选 + attach/fresh 明示；补齐 SessionAttach + Health + Event SSE + Cancel/熔断。详见 [Agent_Team_Host_Bridge架构.md](Agent_Team_Host_Bridge架构.md)。
