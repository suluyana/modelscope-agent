# Agent Team：Host Bridge 架构（纠正 1:1）

> 版本：v0.2 | 日期：2026-08-12（增补 §8 竞品绑定形态对照）  
> 状态：**纠正性架构**。取代「Bridge ↔ Agent 1:1 / 每 Agent 一条 WS」的错误形状。  
> 实现名：产品称 **Agent**；代码可暂留 `AgentEndpoint`。  
> 调研依据：[Agent_Team_竞争力调研操作手册.md](Agent_Team_竞争力调研操作手册.md) §18；编排台（分轨/上下文）见 [Agent_Team_编排台_功能与架构.md](Agent_Team_编排台_功能与架构.md)。

---

## 0. 一句话

**一机一个 Bridge（sidecar）**：负责发现本机已有 runtime、附着会话、一条 WS 连平台。  
**多个 Agent** 挂在该 Bridge 上，平台按 `@name` 路由；dispatch 经 Bridge demux。  
**Attach 已有会话是一等需求**；fresh/spawn 是降级且必须在 Timeline 明示。

---

## 0.1 易用路径（默认用户故事）

用户不应理解 `pair-code` / `--agents` / candidate。默认只做三步：

```
1. 打开 Web「连接本机」→ 复制一条命令（内含临时配对码）
2. 本机终端粘贴运行 → Bridge 上线并自动发现
3. 网页出现「发现到的会话」→ 点「用作 @xxx」或一键「启用默认 @me」
```

设计原则：

| 原则 | 含义 |
|------|------|
| **配对认机器，不认 Agent** | `pair-code` 只证明「这台电脑是我的」；用户无感，藏在复制命令里 |
| **发现默认发生** | Bridge 连上后自动扫 CLI/会话，上报候选；**不必**启动时写 `--agents` |
| **启用要一锤** | 候选 → 可 @ 的 Agent：网页一点或「启用默认」；高级用户才手动起多个 @名 |
| **默认 attach** | 启用时若候选可附着 → `session_mode=attach`；不能附着再 fresh，并写明 |
| **CLI 是后备** | `--agents` 仅给脚本/无 UI 场景；文档与示例默认**不写** |

推荐默认：

- 首次连接：自动启用一个 **`@me`**（绑到第一个可附着候选；没有候选则登记为「可 fresh」的本机默认 Agent）。
- 需要第二个身份时：在 UI 对另一候选点「再加一个 @reviewer」，而不是改启动参数。

`--agents coder:claude_code` **不是**「启动一堆干净新进程」的主路径；它是「跳过 UI、直接登记路由名」的高级快捷方式。

### 0.1.1 多 Agent / 多机 / 新开 —— 用户怎么配

| 场景 | 默认行为 | 用户配置（推荐在 Web「本机 / 执行端」页） |
|------|----------|------------------------------------------|
| 本机只有 1 个可附着会话 | 自动启用 **`@me`** | 可改名为 `@coder` 等 |
| 本机多个会话/CLI | **不**都叫 `@me`。自动只启用一个默认；其余留在「发现列表」 | 对每个候选：启用并起名（`@coder` / `@reviewer`），或忽略 |
| 想连第二台机器 | 在那台机器再跑一次「连接本机」→ **另一个 Bridge** | 机器列表里看到笔记本 / GPU 机；Agent 带机器标签 |
| 想新开干净 Agent | 选「新开会话」而不是「附着已有」 | 创建 Agent 时勾选 **fresh**；或对已有 `@name` 某次派工选 fresh |
| 不要自动 `@me` | CLI `--no-auto-me` / UI 关「自动启用默认」 | 全部改为手动启用 |

命名规则建议：

- **`@me`**：仅表示「这台已连机器上的默认 Agent」（每机最多一个默认）。
- 多身份用语义名：`@coder`、`@gpu-build`，不要 `@me-2`。
- 跨机重名：允许同名但展示时带机器标签（如 `coder · gpu-box`）；路由可用 `endpoint_id` 消歧，产品上引导用户起不同 `@名`。

「新开」与「附着」是 **Agent / 某次 dispatch 的策略**，不是再起一个 Bridge：

```
启用 Agent 时：默认 attach（有候选）| 可选「总是新开」
单次 @ 消息：  session_mode=attach|fresh|auto（平台已有）
```

---

## 1. 旧方案为什么错

### 1.1 错误形状

| 旧假设 | 落地 |
|--------|------|
| 配对 = 开一条执行通道 | `/endpoints/pair` → 一个 Agent + 一个 EndpointToken |
| 一条 WS = 一个可 @ 身份 | `BridgeHub[endpoint_id]` |
| Bridge 进程 = 那个 Agent | `BridgeDaemon` 单 endpoint、单 adapter |
| 执行默认 spawn | attach 只是字段，无本机发现供给 |

这与 Coze Local Agent「bridge spawn 新进程、不附着已有会话」同构，打掉相对 Coze 的差异点。

### 1.2 根因

**没把「发现本机已有 Agent 进程/会话并 attach」当作一等需求。**

若默认故事是「平台派活 → bridge 新开 Claude」，1:1 看似够用。  
一旦故事是「附着用户已开着的会话」，发现是**机器局部能力**，一台机器多个 runtime → 必须 **一机一 Bridge、多 Agent**。

结论：1:1 **不是**合理 MVP，而是错误问题定义下的错误形状。相关实现应纠正，不保留 1:1 pair 兼容。

---

## 2. 正确分层

| 层 | 名称 | 是什么 | 不是什么 |
|----|------|--------|----------|
| 运输 | **Bridge** | 一机 sidecar；发现、附着、心跳、一条 WS | 不是可 @ 身份 |
| 路由 | **Agent** | `@coder`；权限、派工目标 | 不是机器 URL / OS 进程 |
| 运行时 | **Runtime / Session** | 已存在或新建的会话 | 不是第二套路由键 |

```
Control:  @mention → Agent → SessionDirectory(attach|fresh)
                ↓
          BridgeHub[bridge_id]  ──1 WS──►  Machine Bridge
                                              ├ Discovery → RuntimeCandidate[]
                                              ├ Agent A → attach/spawn Runtime
                                              └ Agent B → …
```

多机 = 多个 Bridge；同机多 Agent = 单个 Bridge 上多个登记。

---

## 3. 发现 + Attach 主路径

```
用户 @coder …
  → 路由到 Agent A（bridge_id = B）
  → SessionDirectory：attach 优先已绑定 runtime_session_id
        无绑定 → Bridge 侧 candidates / 上次句柄
  → Hub.dispatch(envelope) → Bridge B
  → Bridge demux → attach 已有会话
        失败 → Timeline 明示 attach_fallback_fresh | need_reauth
        无候选且策略允许 → spawn fresh（明示）
```

**Discovery 先于 Bind**：候选只进本机目录，不能被 @；用户/策略 bind 后才成为 Agent。

---

## 4. 双层状态机

| 层 | 含义 | 心跳 |
|----|------|------|
| Bridge | online / degraded / offline / need_reauth | sidecar WS |
| Agent | available / busy / unavailable | Bridge 批量上报；Bridge offline ⇒ 下属本机 Agent 不可派 |
| Session | bound / attach_failed / fresh | SessionDirectory + adapter 回执 |

编排台分列「机器在线」与「Agent/会话健康」。

---

## 5. 对象与 API

| 对象 | 职责 |
|------|------|
| `MachineBridge` | 机器 sidecar；一条 WS |
| `BridgeToken` | 仅绑 `bridge_id` |
| `AgentEndpoint`（Agent） | 可 @；本机路径 **必填** `bridge_id`；cloud 可无 |
| `RuntimeCandidate` | 发现目录项（未/已绑定） |
| `SessionBinding` | `(agent, project/thread) → runtime_session_id` |
| `DispatchEnvelope` | 目标仍是 Agent id |

API 摘要：

- `POST /bridges/pair-token`、`POST /bridges/pair`
- `POST /bridges/{id}/agents`、`GET /bridges/{id}/candidates`
- `GET /bridges`、`POST /bridges/{id}/tokens`
- WS：`BridgeToken` → `bridge_id`；心跳可带 `agents[]` / `candidates[]`
- **废除**本机主路径：`POST /endpoints/pair` + EndpointToken 绑 WS  
- Cloud：继续 `POST /endpoints` upsert（无 bridge）

---

## 6. 成功标准

去掉「发现已有会话并 attach」后主卖点是否仍成立？仍成立 → 不合格。  
一机多 Agent 共用一条 Bridge WS，Timeline 能区分 attach vs fresh → 合格。

---

## 7. 文档索引

本文件为纠正专章。原 [Agent_Team_架构设计.md](Agent_Team_架构设计.md) / 需求 / PhaseA / 定位叙事中「每 Agent 一条 WS」「bridge/Endpoint 各一条连接」等表述以本文为准废止。

---

## 8. 竞品：Bridge 绑什么、能不能同进程 attach（摘要）

调研全文见操作手册 **§18**。架构决策只依赖下面三条：

1. **Live attach（远程指令回显到已开 Claude/Codex TUI）**：公开竞品 **均无**。勿写进卖点或验收。
2. **Session attach vs spawn**：扣子 Local Agent = spawn 新进程、不附着已开会话（E049）——与「1:1 Bridge=Agent + 默认 fresh」同构；我方必须以 **Discovery → Session attach（失败明示）** 拉开。
3. **绑定键**：
   - 扣子 → 偏 **Agent 通道**（一对一）
   - OpenClaw / Hermes / QwenPaw → 偏 **Host/Gateway**（但只管自有 runtime）
   - Kimi Claw / AgentTeams → **预关联实例或容器 Worker**
   - 我方 → **Host（`bridge_id`）**；Agent 是挂载身份，不是配对对象

若实现退回「每 Agent 一条 WS / 默认 spawn」，则相对 Coze 的差异点自我取消（见 §1.1）。

多 Agent **会话组织**（独立上下文 / 任务表 / 分轨 UI）见操作手册 **§19**；与「绑 Host」是不同层：Host 解决派到哪台机器，§19 解决派出去之后主对话脏不脏、人能不能盯住每一位。
