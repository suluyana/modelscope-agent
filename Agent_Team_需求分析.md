# Agent Team 需求分析

> 基于 [Local_Agent.md](./Local_Agent.md) 方案调研、实验场原型规划及 Coze / Claude Code Agent Teams / Kimi Claw 等竞品分析。
>
> **核心目标（v0.16）**：以 **自己远程自己的本地 Agent** 为主（MS-Agent UI + 钉钉）；**团队协作优先走云端 Agent + 项目会话**；**同一人多机协作**时编排归 Lead Agent，平台只做路由与原语。  
> **v0.16 纠正**：废除「Bridge ↔ Agent 1:1 / 每 Agent 一条 WS」。正确形状为一机一 **Host Bridge**、其上挂多个 **Agent**；发现已有 runtime 并 **attach** 为一等需求。详见 [Agent_Team_Host_Bridge架构.md](Agent_Team_Host_Bridge架构.md)。  
> **「别人 @ 我的本地 Agent」**：仅 **预留接口**，一期不实现；有明确需求时再谨慎开放。

---

## 1. 背景与动机

### 1.1 问题陈述

当前 **MS-Agent** 以**单人本地使用**为主。开发者离开工位后无法推进任务；团队其他角色（产品、测试、运营）无法直接参与开发协作。

与此同时，MS-Agent 平台已具备多 Agent 工作流（DAG）、子 Agent 委托、Project/Session 基础设施等能力，但缺少：

- **Task List 模式 |
| Kimi Claw | IM 群聊 @ 路由；Coordinator + Worker | 钉钉/IM 通道设计参考；但我们不做 OpenClaw 绑定 |
| OpenClaw Gateway | 钉钉/飞书等 25+ IM 渠道 | 通道层可参考；OpenClaw 运行时层可复用 |
| MS-Agent 现有能力 | DAG 工作流、AgentTool、delegate_task | 作为 Worker 执行层，需补协作层 |

### 1.3 产品定位

**Agent Team** 一期聚焦：

> **把个人本地 Agent（Claude Code、Codex、qoder、cursor、opencode 等）接到平台和钉钉，primarily 给自己远程用；同一人可挂多个端点（如 `@我-gpu` / `@我-eas-amd`），跨机由 Lead Agent 编排；项目内多人协作靠云端 Agent 与人找人。**

与 Local Agent Bridge 的关系：Bridge 是 **执行层接入手段**；Agent Team 是 **上层协作产品**。EAS 容器内 bridge 的 **安装与写入镜像由构建侧 Agent 完成**（非平台任务触发），见 §1.6 / §3.9。

### 1.6 平台 vs Agent：谁编排、谁执行（v0.13 原则）

**结（如 `@我-eas-amd`） | 执行被派发的子任务（下 amd 包、冒烟等） | 不假设容器外有持久状态 |
| **任务板（可选）** | Agent **写入**的进度镜像，供人查看；可参考 Claude Agent Teams 的共享 task list | 不是平台侧 CI/CD 引擎 |

**bootstrap 归属**：新 EAS 镜像里的 bridge 自启动脚本，由 **NV 侧 Agent 在 `docker build` 时写入镜像**（Dockerfile `ENTRYPOINT` 等）；平台只提供 **endpoint token** 与 **参考模板**。容器启动后 entrypoint 自行连平台——**与平台任务队列无关**。

#### 1.6.1 知识放哪：Skill 不是默认答案（v0.14）

**结论**：并非「跨机发布 = 全塞进 Skill」。按内容类型分层；Skill 只承载模型默认不会的、可复用的薄 cookbook。

| 内容 | 该放哪 | 不该放哪 | 说明 |
|------|--------|----------|------|
| 镜像仓库地址、EAS 服务名、region、tag 规则 | **用户 prompt** 或 **Project 配置**（`release_config` / env） | Skill（可选 **薄 Skill** `bake-bridge-into-image` | 「EAS 发布」厚 Skill | 通用于所有 ephemeral 端点，不止 EAS |
| 短命端点契约（`endpoint_id` 持久、`instance_id` 变、换镜像后 reconnect） | **多端点启用时的产品说明**（注册页、Project instruction、bridge README） | 仅靠 `/skill` 触发 | 产品行为应始终可见 |
| EAS 创建/更新服务、排障 | **可复用官方 Skill**（如 `alibabacloud-pai-eas-service-deploy` / `diagnose`）或 CLI | 平台内置 EAS 模板 | 管「已有镜像 → EAS API」，不管跨机构建 |

**Skill 适合**：多步领域 playbook、组织约定、模型默认不会的步骤链。  
**Skill 不适合**：会话参数、通用 CLI 入门、平台原语说明书、个人 registry。

**推荐调用栈（EAS AMD + GPU 示例）**：

```
用户 prompt / Project config     → registry、服务名、tag
模型常识                         → docker build / push
平台 Tools                       → delegate / wait_online / ke_policy`、`remote_profile` 字段 | 用户可改 `owner_only=false` 等（需功能开关） |
| **平台路由** | 非 owner 的 @ 请求 → `403 AGENT_OWNER_ONLY` | 实现完整校验链后再放行 |
| **bridge** | 仅处理 `caller_is_owner=true` 的 dispatch | 再实现档位 / 沙箱 / 审计 |
| **安全栈** | 本人远程：身份校验 + 项目边界即可，**接近全功能** | 他人远程：再上 collaborative/open 等 |

**预留的数据结构（须实现，但行为固定）**

```json
{
  "at_name": "张三",
  "owner_user_id": "u-zhangsan",
  "invoke_policy": "owner_only",
  "remote_profile": "owner_only",
  "remote_invoke_enabled": false
}
```

字段含义：

| 字段 | 含义 |
|------|------|
| `invoke_policy` | 谁可以 @ 这个 Agent（一期固定 `owner_only`） |
| `remote_profile` | 别人 @ 时，Agent 权限受限程度（一期固定 `owner_only`） |
| `remote_invoke_enabled` | 是否允许别人 @ 的平台总开关；一期全局 `false` |

- 即使有人改配置èg`）

**刻意不做的（P2+ / 按需）**

- 他人 @ 本地 Agent 的完整链路（invoke 校验、远程沙箱、脱敏、审计）
- 非开发者指挥开发者本机的主场景叙事

### 1.4 产品形态（v0.2 确认）

用户确认的目标产品形态为 **双通道 + 多运行时**：

```
                    ┌─────────────────────────────────────┐
                    │         MS-Agent 平台（云端）         │
                    │  协作服务 │ @ 路由 │ 编排 │ Agent 注册表 │
                    └───────────┬─────────────┬───────────┘
                                │             │
              ┌─────────────────┘             └─────────────────┐
              ▼                                                 ▼
   ┌───â       WebSocket（每用户可有多条 bridge / 多端点）
                                │
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                 ▼
     ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
     │ 张三         │   │ 李四         │   │ 王五         │
     │ ──┴──────┐   ┌──────┴──────┐   ┌──────┴──────┐
     │ Claude Code │   │ Codex CLI   │   │ OpenClaw    │
     │ (多运行时)   │   │ ...     |
|------|------|
| **通道无关** | MS-Agent UI 与钉钉使用同一套 @ 路由、Agent 注册表、任务板；用户在哪发指令，结果回到哪 |
| **Agent 无关** | 本地执行层通过 adapter 接入，不绑定单一 CLI；一期`）不会跑到李四机器上 |
| **多端点** | 同一用户可注册多个 **AgentEndpoint**（如 `@我-gpu`、`@我-eas`），@ 路由按端点派发，不按用户笼统派发 |
| **持久 vs 短命** | GPU 构建机等 **持久端点**长期在线；EAS 容器等 **短命端点**随换镜像重å½ @ 到谁由**授权策略**决定 |
| **跨团队可协作** | 同群或互授权的用户可 @ 彼此 Agent；Team 仅是组织标签，不是路由前提 |
| **项目上下文按需解析** | 不依赖 `#项目` 口令；以**话题线程 + ç--------|------|----------|------------|
| 1 | **话题/回复线程已绑定项目** | 在同一条需求话题里追问「修好了吗」 | 低 |
| 2 | **交互式确认卡片**ï®题」→ 匹配候选 → 用户点选确认 | 中（须确认，不自动执行） |
| 4 | **Agent 主人当前/默认项目** | `@张三` 只读查进展时可用张三正在做的项目 | 中（仅只读） |

**写操作 vs 只读**

| 操作类型 | 项目未明确时 |
|----------|--------------|
| **只读**（查进展、探索代码） | 可用 A服务] [支付后台]
       产品：（点击「支付服务」）
       机器人：已绑定本话题 → 支付服务。开始执行…

       产品：（同话题回复ï息提取「支付」「登录」「后台」等线索，在用户**有权限的项目**里检索
- 唯一匹配 → 只读可直接执行；写操作仍弹卡片确认
- 多个å---|------|
| 已绑定话题/线程 | 线程内最近 **10 条** |
| Project 时间线 | 同项目最近 **20 条** 或 **8k tokens** |
| 群主会话其他话题 | **不注入** |

**谁能 @ 谁的 Agent（授权，非 Team 绑定）**

| 适合协作群） |
| `project_members` | 仅在某 Project 内与我有交集的成员可 @（执行仍须项目权限） |
| `org` | 组织内所有人可 @ |
| `allowlist` | 显式白名单用户 |
| `owner_only` | 仅本人可 @（最严） |

跨 Team 派活：不依赖 Team 关系，只要 **Agent 授权通过** + **目标 Project 权限通过**ã 作为本地运行时 |
| **Claude Code** | ACP（`claude-agent-acp`） | P0 | Coze 已验证，开源 adapter 可用 |
| **Codex CLI** | ACP（`codex-agent-acp`） | P1 | 与 Claudway 代理 | P1 | Coze bridge 已识别；也可复用 OpenClaw 多 Agent 路由 |
| **Hermes Agent** | 自定义 adapter（stdio / RPC） | P1 | MS-Agent 已有 Hermes MCP 集成，需待 adapter | P2 | 产品定位目标清单；一期不阻塞，按适配成熟度推进 |

**与竞品的差异**

- **vs Coze**：同样支持本地 Agent + @，但我们增加 **钉钉原生通道**，且 Agent 运行时清单含 OpenClaw / Hermes
-ridge 层 Agent 可替换

---

## 2. 目标用户与角色

### 2.1 用户画像

| 角色 | 典型诉求 | 使用频率 | 技术门槛 |
|------|----------|----------|----------|
| **开发者** | 远程操控本地代码 Agent；**多端点跨机发布**（EAS+GPU）；多 Agent 并行开发 | 高 | 高 |
| **产品经理** | 查询开发进展；确认功能是否实ç列表；审计操作记录 | 低 | 中 |

### 2.2 人载 Agent：不按角色预置，按「人 + 运行时」注册

**不必预置 Coder / Reviewer / Tester 等角色 Agent。**

Claude Code、Codex、OpenClaw 等通用 Agent 本身就能写代码、审查、跑测试、探索仓库。若再让人注册 `@张三-coder`、`@张三-reviewer`、`@张三-t注册单位** | `@张三` 或 `@张三-claude`；多端点 `@我-gpu` / `@我-eas-amd` | 一个人一种运行时一个入口；*才的改动` — **review 已在句子里**，无需 Mode |
| **并行协作** | @ **不同的人** | `@张三 写接口` + `@李四 帮 review 下张三的改动` |
| *关键词 | 非开发者 → bridge `restricted`（与意图无关） |

### 2.3 不做 Mode；做指代消解与上下文绑定

**例如用户原话**：`@张三 帮 review ä改动」绑到具体对象，再连本地 Agent

| 片段 | 是否清晰 | 谁解决 |
|------|----------|--------|
| 「review」 | ✅ 清晰 | **本地 Agent** 读 prompt 即懂，无需平台分类 |
| 「刚才的改动」 | ❌ 不清æ_bundle）**

派发前组装，随 prompt 一并发给 bridge → 本地 Agent：

| 上下文块 | 来源 | 示例 |
|----------|------|------|
| `thread_messages` | 本话题æ®近期 Agent 执行摘要 | 上次 `@张三` 产出的 diff 摘要、任务 id |
| `git_snapshot` | **bridge 本地**拉取（可选） | `git diff HEAD~1`、当前 branch、未提artifacts[]` | 上游任务 / 平台制品库 | T1 产出的 deb/wheel 包 `artifact_id`、checksum、下载 URL |
| `deployment_context` | 上游任务 / 项目配置 | 新镜像 `image:tag`、EAS 服务名、待验证 endpoint |

**「刚才的改动」消解策略**

```
1. 同话题内最近一次 @å[最近一次 commit]
4. 用户点选或回复序号 → 写入 referenced_task_id → 再派发
```

**DispatchEnvelope（去掉 task_mode_hint）**

```json
{
  "prompt": "帮 review text_bundle": {
    "thread_messages": ["..."],
    "project_timeline": ["..."],
    "git_snapshot": { "branch": "feat/login", "diff_stat": "3 files..." },
    "referenced_task_id": "task-abc123"
  },
  "permission_tier": "restricted",
  "sender_user_id": "..."
}
```

本地 Claude Code 收到的是**已消歧的完整 prompt + 上下文**，自行执行 review。

**跨机流水线 DispatchEnvelope 示例（§3.8 T3）**

```json
{
  "prompt": "用登记好的制品打 linux/amd64 ér-01",
  "context_bundle": {
    "artifacts": [
      { "artifact_id": "pkg-001", "sha256": "...", "url": "https://..." }
    ],
    "deployment_context": { "registry": "registry.cn-xxx.aliyuncs.com/foo", "image_name": "bar" }
  },
  "permission_tier": "owner",
  "sender_user_id": "u-me"
}
```

**bridge 职èode）**

| 做 | 不做 |
|----|------|
| 按 `permission_tier` 限制工具（非开发者远程） | 不把「review」映射成 mode |
| 拉取 `git_snapshot`、工作区状态是 reviewer」类 system 前缀（除非用户消息本身需要） |

**快捷指令（保留，但不是 Mode）**

帮非开发者生成**更明确的指代**，例如：

- 「review 下 **本话题里张三 15:32 那次修改**」
- 本质是写好 prompt，不是切模式

**实验场 explore/plan/verify 模板**

不映射为 Mode；若需要，作为 **Skil`@张三-eas-amd`（区分**执行环境**，见 §3.8）
- 避免 `@张三-coder` / `@张三-reviewer` 这类角色后缀（除非用户主动自定义别名）

---

## 3. 核åe 通过 `agent-bridge` 注册为 `@张三`
2. 产品经理在项目会话：`@张三 登录功能开发到哪了？`
3. Claude Code 读取项目代码库状态，流式回复
**价值**：开发者不必实时响应 IM；团队对进展有统一视图。

### 3.2 场景二：多人 Agent 并行 — 写 / Review / 测试

**参与者**：开发者张三、开发者李四、测试王五（各有一台本地 Agent）

1. Tech Lead 在项目会话输入：
   ```
   @张三 实现用户注册 API
   @李四 review 张三刚才的改动
   @王五 API 上线前跑注册相关测试
   ```
2. 编排层åª证一下昨天报的登录超时问题是否修复了`
2. 平台组装 context：关联 bug 话题、`#payment-service`、可选 `git_snapshot`（相关测试路径）
3. 张三æ追问 `@张三 …` 自动带上测试报告上下文

**价值**：产品 @ 人 + 说人话；平台补**指代**，不替 Agent 识别「这是 test 模式」。

### 3.4 场景å·支付模块回归`
3. 开发者（同时）：`@张三 修复支付回调的空指针`
4. 编排层将三条消息路由到不同人载 Agent，并行执行；各带各自 project 与 context_bundle
5. 项目会话按 Agent 分组展示回复

**价值**：团队像 @ 同事一样 @ Agent；任务类型å¼ 三`
3. 产品经理在群里：`@张三 登录接口今天能联调吗？`
4. 张三有多个相关项目 → 机器人回复**项目选择卡片**「支付服务 / 支付后台」
5. 产品点击「支付服务」→ 绑定本话题 → 路由 bridge 执行
6. 后续同话题：`修好了吗？` 无需能跟哪段配置有关`（口语，无 slug）
3. 机器人匹配到 `platform-infra` 与 `payment-service` 两个候选 → 卡片让用户选
4. 产品选 `platform-infra` → 李åºº载 Agent + 授权**，不需要把群绑到某个 Team，也不要求大家在同一项目组。

### 3.7 场景七：UI 与钉钉双通道（同人同 Agent）

**参与者**：开发者（MS-Agent UI）、产品经理（钉钉）

1. 开发者在 UI 的 `payment-service` 项目会话：`@张三 修复支付回调 bug`
2. 产品经理在钉钉群同è）【一等场景】

**背景**：EAS 在 AMD + Docker 内运行；amd64 镜像须在 GPU 机构建。需 `@我-eas-amd` 与 `@我-gpu` 配合。

**编排主体：Lead Agent ``
@我-gpu 完成 eas amd 镜像发布：下包在 eas、在 gpu 打 amd64 镜像，
推到 registry.cn-xxx.aliyuncs.com/ns/foo:v1.2.3，更新 EAS 服务 my-svc 并验证
```

**Lead Agent 自行编排**（知识分层见 §1.6.1；平台不å1. 下 amd 依赖 | Lead | `delegate_to_endpoint` → `@我-eas-amd` | **平台 Tool** |
| 2. 传制品 | Lead | `upload_artifact` / `download_artifact` | **平台 Tool** |
| 3. build + push | Lead | 本机 docker（含 `--platform linux/amd64`） | **模型常识** + prompt 中的 registry/tag |
| 4.ndpoint_token` 注入 ENV | **平台 Tool** + **示例模板**（可选薄 Skill） |
| 5. 更新 EAS 部署 | Lead | `aliyun eas …` 或官方 EAS Skill | **官方 Skill / CLI**（非 MS-Agent 平台模板） |
| 6. 等新容器上线 | Lead | `egate` → `@我-eas-amd` 冒烟 | **平台 Tool** |

**平台不出现**「T1–T6 任务板自动串联」「平台触发 bootstrap」「把 registry 写进 Skill」等。

**与 v，不是平台编排 Agent。

**价值**：跨机能力落在 **通用 Tools + 模型常识 + 用户参数**；平台可复用到任意多机场景，不绑死 EAS 发布 playbook。

### 3.9 短命端点与容器重建（EAS 特有问题）

**问题**：换 EAS 镜像 → 新容器 → 旧容åas-amd` |
| **状态上云、制品出容器** | 会话、制品元数据在平台；大包走制品库，不赌容器磁盘 |
| **bootstrap 由构建侧 Agent 写入镜像** | NV Lead 在 `docker build` 时把 bridge 启动脚本 + token 打进镜像；**新容器启动 = entrypoint 自举**，非平台下API 取 endpoint-scoped token，写入 `ENV`/`ARG`；轮换策略见安全 |

**换镜像后的时序（Agent 视角）**

```
Lead: docker push 完成
  → 调 EAS API 更新镜像 tag
dpoint_id 重连 → online
  → Lead: wait_for_endpoint_online 返回
  → delegate @我-eas-amd：装依赖 + 冒烟
```

**对用户的预期**：换镜像后 EAS 端点短暂 `reconnecting`；**Lead Agent 负责等待与续跑**，人可在会话里看进度，不必重跑 gpu build（制品与 tag 已存在----|----------|--------|----------|
| F-Team-01-1 | 每个 Project 拥有独立的对话空间、文件工作区、Agent 列表 | P0 | 创建项目后可添加 Agent、发起对话 |
| F-Team-01-2 | 项目内所有对话消息持久化，支持
| F-Team-01-4 | 项目级配置继承全局配置（MCP、Skill、模型） | P0 | 复用实验场 F3 分层配置设计 |

#### F-Team-02：成员与权限

| 编号 | 需求描述 | 优先级 | 验收标准 |
|------|----------|-------Editor / Viewer | P2 | Viewer 只能看不能 @ Agent |
| F-Team-02-3 | 与 F-Team-03-5 对齐：Agent 主人配置 invoke_policy | P2 | 控制谁可 @ 自己的 Agent（一期平台强制 owner_only） |
| F-Team-02-4 | 高风险操作需审批（如 shell 执行、文件删除） | P2 | 非开发者 @co---|----------|
| F-Team-03-1 | 用户通过 bridge 注册**个人**通用 Agent（`@张三` 或 `@张三-claude`） | P0 | 一种运行时一个入口 |
| F-Team-03-2 | 同用户可注册多个 Agent（**运行时不同**，如 claude + hermener 或系统级） | P1 | 如公共 `@planner`；**团队协作主路径** |
| F-Team-03-4 | 展示：所属用户、运行时、**端点标签**、在线状态、默认项çke_policy** 字段；一期固定 `owner_only`，平台强制 | P0 | 字段可存；非 owner @ 返回明确错误 |
| F-Team-03-6 | 预留 **remote_invoke_enabled** 平台开关，一期 `false` | P0 | 配置层可扩展，行为关闭 |
| F-Team-03-7 | Agent 离线时 @ 提示不可用（如「张ä 需求描述 | 优先级 | 验收标准 |
|------|----------|--------|----------|
| F-Team-04-1 | 消息中 `@agent-name` 解析为路由目标 | P0 | `@coder hello` 只发给 Coder Agent |
| F-Team-04-2 | 单条消息支持 @ 多个 Agent | P0 | `@coder 写代码 @reviewer 同时审查` |
| F-Tea| @ 路由携带上下文：已绑定话题 + Project 时间线 + 任务状态（见 §1.4 条数上限） | P0 | Agent 能引用同话题前文 |
| F-Team-04-5 | 项目未明确时触读广播，不触发执行） | P2 | 用于同步信息 |

#### F-Team-05：非开发者友好交互

| 编号 | 需求描述 | 优先级 | 验收标准 |
|------|----------|--------|----------|
| F-Team-05-1 | 提供「快捷指令」模板（查进展、跑测试、审查代码） | P0 | 一键填充 @候选列表 → 用户确认 | P1 | 不依赖 `#slug` |
| F-Team-05-4 | 操作结果提供「人话摘要」+「技术详情」折叠区 | P0 | 默认展示摘要，开发者可展开详情 |

#### F-Team-06：会话展示

| 编号 | 需求 |
| F-Team-06-2 | Agent 执行过程流式展示（thinking、tool call、输出） | P0 | 与现有 WebUI 流式能力一致 |
| F-Team-06-3 | 多 Agent 并行时，各 Agent 输出分区或时间线展示 | P0 | 不混在一个气泡é7 ~ F-Team-10）

#### F-Team-07：任务板（Task Board）

**定位**：Agent 可读写共享进度列表（参考 Claude Agent Teams），供人观察；**不是**平台 CI/CD 编排引擎。领域流水线由 Lead Agent 驱动。

|ending / in_progress / completed / failed） | P0 | UI 可查看 |
| F-Team-07-2 | 任务支持 `blocked_by`（**由 Agent 写入**时生效） | P1 | Lead 拆子任务后可标依赖 |
| F-Team-07-3 | 任务关联：触发人、目标 Agent、输入 prompt、输出结果 | P0 | 可追溯 |
| F-Team-07-t 触发） | P1 | 失败可续跑 |

#### F-Team-08：并行编排

| 编号 | 需求描述 | 优先级 | 验收标准 |
|------|----------|--------|----------|
| F-Team-08-1 | 无依赖ç¶冲突风险时串行或隔离（worktree） | P1 | 两 coder 改同一文件时排队或告警 |
| F-Team-08-3 | 单 Agent 同时只处理一个任务（队列） | P0 | 避免本地 Claude Code 并发冲突 |
| F-Team-08-4 | 编排器汇总多 Ag
|------|----------|--------|--------|
| F-Team-09-1 | 派发前组装 `context_bundle`（话题、项目时间线、referenced_task、**artifacts**、**deployment_context**） | P0 | 平台 |
| F-Team-09-2 | bridge 补充 `git_snapshot`（branch、diff stat、最近 commit） | P1 | bridge |
| F-Team-0指令：生成**指代更明确**的 prompt（非 Mode） | P0 | 平台 UI |
| F-Team-09-5 | 可选云端公共 Agent | P2 | 平台 |

#### F-Team-12b：bridge 上下文与权限（补充 F-Team-12）

| 编号 | 需求描述 | 优先级 |rompt + context_bundle + permission_tier） | P0 | 无 task_mode 字段 |
| F-Team-12b-2 | `permission_tier` 限制工具（按发送者身份，非按 review/test 分类） | P0 | é并入发给本地 Agent 的 prompt / ACP 附件 | P0 | Agent 能看到「刚才的改动」具体指什么 |
| F-Team-12b-4 | 本地拉取 git 状态注入 context_bundle | P1 | 「刚才的改动」可落到 diff |

#### F-Team-10：Lead Agent（编排协调者）

| 编号 | 需求描述 | 优先级 | 验收标准 |
|------|----------|--------|----------|
| F-Team-10-ead 编排 |
| F-Team-10-3 | Lead 汇总 Worker 输出，回答人类综合问题 | P1 | 「发布完成了吗？」→ Lead 综合状态 |
| F-Team-10-4 | Lead 可使用 **delegate_to_endpoint** 工具向 `@我-eas-amd` 等派发子任务 | P0 | 平å· | 需求描述 | 优先级 | 验收标准 |
|------|----------|--------|----------|
| F-Team-11-1 | 云端 Agent 运行在平台侧，复用 LLMAgent + AgentTool | P0 | @ 云端 Agentpace 文件（只读/读写按角色） | P0 | Reviewer 可读不可写 |
| F-Team-11-3 | 云端 Agent 可使用项目级 MCP / Skill | P0 | 继承分层配置 |

#### F-Team-12：本地 Agent Bridge

| 编号 | 需求描述 | 优先级 | 验收标准 |
|------|----------|--------|----------|
| F-Team-12-2 | Bridge 发现本机 **已安装 CLI** 与 **可附着会话候选**（Claude / Codex / …） | P0 | 配对后 `/candidates` 可见；未 bind 不可 @ |
| F-Team-12-3 | 通过统一 Adapter 层驱动本地 Agent（ACP 或自定义） | P0 | 见 F-Team-16 |
| F-Team-12-4 | 流式回传；Bridge 断连 → 下属本机 Agent 不可派 | P0 | 编排台区分 bridge offline vs agent unavailable |
| F-Team-12-7 | 远程执行权限可配置（bypass / restricted + 审批） | P1 | 钉钉非开发者触发时强制 restricted |
| F-Team-12-9 | **多机 = 多 Bridge**；**同机多 Agent = 单 Bridge 多登记**（禁止再写「每 Agent 一条连接」） | P0 | GPU 机与笔记本各一 Bridge；同机 `@coder`+`@reviewer` 共用一条 WS |
| F-Team-12-10 | Bridge 重连：新 `instance_id` 绑定原 `bridge_id`，再重新 advertise Agents | P0 | 换 EAS 镜像后同 `@我-eas-amd` 仍可 @ |

**定位**：平台只提供 **通用 Tools/API + tool description + 示例模板**（§1.6.1）。  
registry / 服务名来自用户 prompt 或 Project 配置；`docker build` äº§ | 验收标准 |
|------|----------|--------|----------|
| F-Team-17-1 | **制品库**：`upload_artifact` / `download_artifact`；`artifact_id`、sha256、OSS URL | P0 | Agent è7-2 | **delegate_to_endpoint**：Lead 向指定 `@name` 派发子 prompt + `context_bundle`；**tool description 写清语义** | P0 | Agent 无需专门 Skill 即可正确委派 |
| F-Team-17-3 | **get_endpoint_status** / **wait_for_endpoint_online**；tool description 说明 ephemeral 重建场景 | P0 | Lead 在换镜像后自行等待，平台不自动续跑领域任å 打镜像时写入 ENV，非平台写镜像 |
| F-Team-17-5 | `context_bundle` 支持 `artifacts[]`、`deployment_context`（**由 Agent 从 prompt/上游结果填充**） | P0 | reg：bootstrap Dockerfile 参考（`examples/bridge-bootstrap/`）；可选薄 Skill `bake-bridge-into-image` | P1 | 非 EAS 专用；不写 registry / docker 入门 |
| F-Team-17-7 | Project `release_config`（可选）：registry、默èF-Team-17-8 | 文档标明：EAS 部署/诊断可复用阿里云官方 Skill；跨机编排不依赖其存在 | P2 | 与 `alibabacloud-pai-eas-service-deploy` 等对齐说æ---------|---------------|
| `persistent` | GPU 构建机 | 长期在线 | 用户一次性配对或 Agent 本机维护 |
| `ephemeral` | EAS 业务容器 | 随换镜像重建 | **构建侧 Agent 写入镜像 entrypoint**；容器启动自连平台 |

**刻意不做（平台）**

- ❌ 「EAS 发布æ/ `wait_for_endpoint_online` 等 tool description  
- ❌ 平台任务触发 bridge 安装 / EAS 部署更新  
- ❌ 平台解析 docker build / EAS API 语义

#### F-Team-13：执行隔离与安全（一期仅「本人远程」）

**一期**：不为「他人 @」实现档位/沙箱；本人远程接近_only` | P0 |
| F-Team-13-2 | 本人远程：项目 workspace 边界 + 敏感路径黑名单（底线） | P0 |
| F-Team-13-3 | 本人远程：不继承本地终端会话，但 **不人为阉割** tool 能力 | P0 |
| F-Team-13-4 | 非 owner 的|------|------|
| F-Team-13-5 | `remote_profile` 三档 open / collaborative / owner_only |
| F-Team-13-6 | 他人 @ 时的审计、通知、上行脱敏 |

---

### 4.4 通道层（F-Team-14 ~ F-Team-15）

#### F-Team-14：MS-Agent UI 通道

| 编号 | 需求描述 | 优先级 | 验收标准 |
|------|----行时、在线状态） | P0 | 输入 `@` 弹出可选列表 |
| F-Team-14-2 | Agent 执行过程流式展示（tool call、文件 diff、日志） | P0 | 复用现有 WebUI WebSocke Agent 可区分 |
| F-Team-14-4 | 本地 Agent 管理页：配对 bridge、选择运行时、注册 @name | P0 | 一键复制配对命令 |
| F-Team-14-5 | 任务板、Agent 状态面板嵌入项目页 | P1 | 与对话并列或 Tab 切换 |

#### F-Team-15：钉钉通道

| 编号 | 需求描述 | 优先级 | 验收标准 |
|------|----------|--------|----------|| 本人在钉钉 @ 自己的 Agent |
| F-Team-15-6 | 非 owner @ 返回「尚未开放他人调用」 | P0 | 预留接口，明确拒绝 |
| F-Team-15-7 | 卡片回复含：Agent 主人、**项目中文名**、摘要 | P0 | 可辨识 |
| F-Team-| F-Team-15-10 | 执行记录写入对应 Project 时间线 | P1 | `channel: dingtalk` |
| F-Team-15-11 | 可选：高级用户仍可用 `#slug` 跳过卡片（可配置关闭） |录接口什么时候能联调？
→ [支付服务] [支付后台]  请选择项目

（选定后，同话题内）
修好了吗？
@王五 跑一下登录回归

# 口语 + 消歧（跨项目线索）
@李四-explorer 帮看下支付回调的配置
→ 匹配到多个项目，请确认：[平台基çaude Code / Codex / OpenClaw / Hermes |
| F-Team-16-2 | Claude Code adapter：ACP stdio，复用 `claude-agent-acp` | P0 | 可执行 prompt 并流式回传 |
| F-Team-16-3 | Codex CLI adapter：ACP stdio | P1 | 与 Claude Code 同接口 |
| F-Team-16-4 | OpenClaw adapter：ACP 或调用 OpenClaw Gateway API 6-5 | Hermes adapter：spawn `hermes` 子进程或 Hermes RPC | P1 | 复用现有 Hermes 集成经验 |
| F-Team-16-6 | 用户可选择「默认运行时」及每个 @name 绑定的运行æcute(prompt, session_id) → stream events` | P0 | 编排层不感知底层差异 |
| F-Team-16-8 | 各运行时权限策略可独立配置（如 Hermes 默认 restricted） | P1 | 按 A (ACP)
Phase 2.5: Codex (ACP)
Phase 3: OpenClaw, Hermes
```

---

## 5. 非功能需求

### 5.1 性能

| 编号 | 需求 | 指标 |
|------|------|------|
| NF-01 | @ 路由延迟 | 消息发出 → Agent 开始响应 < 3s（云端）/ < 5s（本地 bridge） |
| NF-02 | 流式首 token | < 10s（受上游 LLM 约束） |
| NF-03 | 并行 Agent 数 | 单项目同时|
|------|------|------|
| NF-05 | Bridge 进程资源占用 | 空闲时 < 100MB 内存 |
| NF-06 | 非开发者上手 | 无需阅读文档即可完成一次 @tester 操作 |
| NF-07 | 移动端 | MS-Agent UI 响应式；钉钉 App 原生可用ï：一次性 pair-token |
| NF-09 | 授权 | 项目级 RBAC + Agent 级 ACL |
| NF-10 | 数据 | 本地代码不上传云端，仅传执行结果摘要（可配置） |
| NF-11 | 审è----|
| NF-12 | 本地 Agent 运行时 | P0: MS-Agent CLI、Claude Code；P1: Codex、OpenClaw、Hermes；P2: qoder / cursor / opencode 等 |
| NF-13 | 协议 | ACP（Claude/Codex/OpenClaw）；Hermes 自定义 adapter |
| NF-14 | 平台 | 复用 MS-Agent SDK、Project/Session、Permission、WebUI |
| NF-15 | IM 通道 | 一期钉钉；架构预留飞书/企微扩展 ──────────────────────────┐
│                           接入通道层（Channel）                           │
│  ┌──âI（Web）         │  │  钉钉群聊（DingTalk Bot）        │  │
│  │  ·项目会话 ·@ 补全 ·任务板   │  │  ·Stream/Webhook ·@ 解析 ·卡片  │  │
│ 
└────────────────┼─────────────────────────────────┼────────────────────┘
                 │         统一消息入口              │
                 └────────────────┬────â       │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ │
│  │ 通道适配器    â·并行调度    │ │ ·流式推送     │ │
│  └──────────────┘ │ ·@ 路由      │ │ ·依赖管理    │ │ ·多通道回写   │ ────────────────────────────────────────────────┐  │
│  │ 云端 Agent 运行时（LLMAgent + 角色模板 + MCP/Skill）               │  │
│  └─────────────────────────────────────â───────────┐
        ▼                     ▼                     ▼
 ┌─────────────┐       ┌─────────────┐       ┌─────────────â─┬──────┘       └──────┬──────┘       └──────┬──────┘
        │ Adapter 层          │            │ Codex CLI │ OpenClaw │ Hermes │ ...      │
 └────────────────────────────────────────────────────────┘
```

### 6.2 核心数据模型

> **纠正（v0.16）**：不再存在「每个 Agent 至多一条在线 WS」。WS 属于 **MachineBridge**。

```
User
├── id, name, …
├── bridges[]: MachineBridge            # 一机一个 sidecar
│   ├── bridge_id, owner_user_id, machine_label
│   ├── status, current_instance_id, last_heartbeat
│   ├── candidates[]: RuntimeCandidate  # 发现目录（未/已绑定）
│   └── agents[] → AgentEndpoint        # 可 @ 身份（实现名）
│       ├── endpoint_id, at_name, bridge_id  # 本机必填 bridge_id
│       ├── runtime, adapter_kind, status
│       └── …
└── cloud_agents[]: AgentEndpoint       # adapter_kind=cloud，无 bridge_id

BridgeToken（仅绑 bridge_id，用于一条 WS）
SessionBinding（agent × project/thread → runtime_session_id）
…
```

详见 [Agent_Team_Host_Bridge架构.md](Agent_Team_Host_Bridge架构.md)。

<!-- 旧错误表述「BridgeConnection（每个 AgentEndpoint 至多一条在线 WS）」已废止。 -->

```
LEGACY_REMOVED_BridgeConnection_per_endpoint_ws
```

Artifact / Project / Task / DingTalk 等其余模型见下文历史段落（编码损坏处不改语义）。

```
UserAgentProfile（人载 Agent，挂在某 Bridge 下的 Agent 记录）
├── at_name, owner_user_id, endpoint_id, bridge_id
├── role_template, runtime, type
├── ...

Artifact（åk_id
└── expires_at?

Project（执行上下文，与群/Team 解耦）
├── id, name, workspace_path
├── members[]: { user_id, role }
├── sessions[], tasks[]├── release_config?                     # registry、EAS 服务 id、bootstrap 模板
└── （可选）org_team_id?

Task（任务板条目，可选；**Agent 写入**）
├─nt_context?   # Agent 填写，平台存储
└── blocked_by[]                              # Agent 维护，平台不自动调度

DingTalkGroupInstallation（群 ≈ 通道）
├── chat_id, bot_app_key
└── （无 project_id / team_id 绑定）

DingTalkThreadContext
├── chat_id, thread_id, project_id?

# 路由时：@我-gpu + project eas-release
#   → AgentEndpoint（owner=我, endpoint_id=gpu-docker）
#  行
# 若 endpoint_type=ephemeral 且 offline → 任务 waiting_endpoint 或提示「EAS 容器重建中」
```

### 6.3 @ 路由流程（双通道统一）

```
1. UnifiedMessage { send?, thread_id? }

2. 解析 project_id：
   a. 话题/线程已绑定？→ 用
   b. 写操作？→ 弹项目选择卡片（或 NL 消歧后确认）
   c. 只读？→ Agent 默认项目 / 唯一 NL 匹配
   d. 仍不明 → 追问，禁止å：发送者能否调用该 Agent？
      - owner_only（一期默认）：sender == owner
      - group_members / project_members / org / allowlist（P4+）
   c. Project RBAC：sender 对 project_id 是否有权（读/写按操作类型）？
   d. Endpoint 在线？
      - online → 入队 → 该  Agent 决定**

4. 回写触发通道；持久化到 project_id 时间线；登记 output_artifacts / deployment_context
```

**不需要 Team 参与路由。**

### 6.4 并行写/Revi过后跑测试"

任务板:
┌────────┬──────────┬───────────┬──────────────┐
│ Task   │ Agent    │ Status    │ Depends On   │
├──â1           │
│ T3     │ tester   │ pending   │ T2           │
└────────┴──────────┴───────────┴──────────────┘

执行时序:
  T1 ──执行──▶ 完成 ──▶ T2 unblock ──执行──â                                  ▼
                                                                    编排器生成协作摘要
```

### 6.5 跨机发布：Agent 编排示ämd 镜像发布（registry/服务名在 prompt 或项目配置中）

@我-gpu (Lead) 内部计划:
  1. delegate @我-eas-amd → 下 amd 依赖，upload_artifact
  2. download_art+ token）
  3. docker push → 调 EAS API / 官方 EAS Skill 更新镜像
  4. wait_for_endpoint_online(eas-amd)
  5. delegate @我-eas-amd → 冒烟验证
  6. 回复用户摘要

平台全程: 路由、制品库、endpoint 状态ã# Phase 1：MS-Agent UI + 云端 Agent Team（6 周）

**目标**：在 MS-Agent UI 中完成项目内 @ 多个云ç|----------|
| Project + Agent 注册表（仅云端） | F-Team-01, F-Team-03-1/3 |
| MS-Agent UI @ 路由与流式展示 | F-Team-04, F-Team-06, F-Team-14-1/2 |
| 任务板（基础版） | F-Team-07-1/2/3 |
| 上下文绑定 + 指代消解 | F-Team-09-1/3, F-Team-12b-1/3 |
| bridge git_snap（无依赖） | F-Team-08-1/3 |

**不包含**：本地 Bridge、钉钉、多人协作。

### Phase 2：本地 Bridge + 自用远程  Agent；支持 **同一用户多 bridge**；他人 @ **接口预留、行为拒绝**。

| 交付项 | 对应需求 |
|--------|-----Team-12, F-Team-16-1/2/7 |
| 本地 Agent 注册、在线状态 | F-Team-03-1/2/4, F-Team-12-8, F-Team-14-3/4 |
| **多端点注册**（`@我-gpu` / `@我-eas-amd`） | F-Team-12-9, F-Team-03-4 |
| invoke_policy / remote_invoke_enabled 预留 | F-Team-03-5/6 |
| 本人远程æ13-4, F-Team-15-6 |
| **制品库 + delegate + endpoint 工具** | F-Team-17-1/2/3, F-Team-10-4, F-Team-12-11 |

### Phase 3：钉钉自用通道 + 跨端点原语（8 周）

**目æ编排，知识分层见 §1.6.1）。

| 交付项 | 对应需求 |
|--------|----------|
| 钉钉机器人 + 本人路由 |  OpenClaw / Hermes adapter | F-Team-16-4/5 |
| **delegate + wait_for_endpoint + issue_endpoint_token**（tool description 完备） | F-Team-17-2/3/4, F-Team-10-4 |
| **bootstrap 示例模板**（可选薄 Skill `bake-bridge-into-image`） | F-Tconfig**（registry 等，亦可仅靠 prompt） | F-Team-17-7 |

### Phase 4：他人 @ 本地 Agent（按需，功能开关）

**前置**：`remote_invoke_enabled=true` + 需求评审 + 安全方案。

| 交付项 | 对应需求 |
|--------|----------|
| invoke_policy 完整校验 | F-Team-03-（持续）

| 交付项 | 对应需求 |
|--------|----------|
| Lead Agent 自动拆任务 | F-Team-10 |
| 文件冲突检测 / worktree 隔离 | F-Team-08-2 |
| **bootstrap 示 NF-15 扩展 |
| Agent Hub 导出 | F-Team-09-4 |
| 钉钉「处理中」卡片 | F-Team-15-8 |

---

## 8. 依赖与约束

### 8.1 平台内依赖

| 依赖模块 | 状态 | 影响 |
|----------|------|------|
| F1 Project 管理 | 待*阻塞** |
| F3 分层配置 | 待开发（P0） | Agent 模板配置，**阻塞** |
| F4 权限管控 | 待开发（P0） | 审批流，Phase 2 阻塞 |
| AgentTool / LLMAgent | 已有 | 云端 Agent 执行层 |
| WebUI 流式展示 | 已有 | 前端基础 |

### 8.2 外部依赖

| 依è ACP 协议成熟度 | 生态不完善 | 仅作本地层协议；云端不依赖 ACP |
| 用户网络环境 | bridge 断连 | 自动重连 + 离线排队 |

### 8.3 已知限制（一期接受）

1. 本地终端会话与平台会 自举 bridge，非平台触发
5. EAS 容器内未上传制品库的依赖 **不跨容器保留**
6. endpoint token 由 **Lead Agent 在 build 时**申请并注入镜像；须 者指令模糊，Agent 执行偏差 | 体验差、误操作 | 快捷指令模板 + 审批流 + 结构化确认 |
| 多 Agent 并行改同一文件 | 代码冲突 | 任务板依赖 + 文件锁 + 冲突告警 |
| 他人 @ 导致敏感信息泄|
| Token 成本激增（多 Agent 并行） | 费用不可控 | 限制并行数；Reviewer/Tester 用轻量模型 |
| Bridge 稳定性 | 本地 Agent 频繁离线 | 心跳 + 重连 ad `wait_for_endpoint_online` |
| **跨机制品传递** | 大包传输慢/失败 | 平台 OSS + 断点续传 + sha256 校验 |
| **bootstrap 凭证** | 泄露可远程控容器 | endpoint-scoped token、轮换、不进镜像层明文 |
| 钉钉消息格式限制 | 长代码/log 展示差 | 摘要卡片 + 链接跳转 MS-Agent UI 详情 |
| 多运行时 adapter 维æ多个 Agent 时，无依赖任务并行完成
- [ ] 多人（不同用户 Agent）并行/流水线任务可完成
- [ ] 任务板正确展示状态流转

### 10.2 Phase 2 验收指æ¯经平台从一端上传到另一端下载
- [ ] `invoke_policy=owner_only` 生效；他人 @ 返回明确「未开放」
- [ ] 配置中可看到预留字段，但 `remote_invoke_enabled=false`

### 10.3 Phase 3 验收指标

- [ ] 本人可从钉钉 @ 自己的多端点 Agent 并收到回复
- [dge，Lead `wait_for_endpoint_online` 后续跑
- [ ] 平台无「发布流水线」硬编码；registry 可不进 Skill
- [ ] 仍无他人 @ 本地 Agent 能力
- [ ] /摘要形式可读

### 10.4 长期指标

| 指标 | 目标 |
|------|------|
| 项目内 Agent 协作使用率 | ≥ 3 协作真正在用） |
| 非开发者消息占比 | ≥ 20% |
| 本地 Agent 在线率 | ≥ 60%（工作时间） |
| 多运行时项目占比 | ≥ 15% 项目使人通过 @ 彼此的人载 Agent 协作；可跨 Team、跨项目 |
| **人载 Agent（UserAgentProfile）** | 用户经 bridge 注册的个人通用 Agent，`@张三` 即该人的执行延伸 |
| **AgentEndpoint** | 用户名下某一执行环境（GPU 机构建机、EAS 容器等）；`@我-gpu` 路由到此端点 |
| **短命端点（ephemeral）** | 随 EAS 换镜像重建的端点；`endpoint_id` 持久，`instance_id` æprompt/配置；docker→模型常识；跨端点→Tools；bootstrap→示例/薄 Skill；EAS API→官方 Skill/CLI |
| **Lead Agent** | 编排者（如 `@我-gpu`）；拆任务ã 派发子任务，平台只路由 |
| **制品库（Artifact）** | 平台侧跨端点传递的文件槽（OSS + `artifact_id`），不依赖容器内磁盘 |
| **deployment_context** | 镜像 tag、EAS 服务 id 等；由 **Agent** 填入 `conte线）；与钉钉群无绑定关系 |
| **项目上下文** | 单条指令关联的 Project；主路径为话题绑定 + 卡片点选，非 `#slug` |
| **Agent Binding** | 同 UserAgentProfile（历史术语，文档统一用「人载 Agent」） |
| **context_bundle** | 派发时附带的指代上下文（话题、时间线、git、referenced_task） |
| **指代多 Agent 执行顺序 |
| **Lead Agent** | 负责拆任务、派工、汇总的协调者 Agent |
| **agent-bridge** | 运行在用户本地的桥接程序，连接平台与多种本ånnel** | 消息接入通道：MS-Agent UI（web）或钉钉（dingtalk） |
| **运行时（Runtime）** | 本地 Agent 实现：MS-Agent CLI、Claude Code、Codex、OpenClaw、Hermes；目标清单含 qoder / cursor / opencode 等 |
| **ACP** | A

## 12. 附录：与现有文档的关系

| 文档 | 关系 |
|------|------|
| [Local_Agent.md](./Local_Agent.md) | Bridge 技术方案 → F-Team-12 / F-Team-16 |
| examples/capability/hermes_integration | Hermes adapter 参考 |
| examples/capability/openclaw_integration | OpenClaw adapter 参考 |
| pla → 协作层基础设施 |
| [aliyun/alibabacloud-aiops-skills](https://github.com/aliyun/alibabacloud-aiops-skills) | 官方 `alibabacloud-pai-eas-service-deploy` / `diagnose`：已æ建与 Agent Team 原语 |
| 实验场子 Agent / Skill | 可选扩展；跨机场景以 Tools 为主，Skill 非默认答案（§1.6.1） |

---

*文档版本：v0.15 | 日期：2026-07-20 | 变更：同步 Agent_Team.md 策略改动（MS-Agent 问题陈述、运行时清单、字段注释、owner-only 注册表 UX、F-Team-02→P2、MS-Agent CLI=P0）；保留 v0.12–v0.14 多端点与知识分层*

