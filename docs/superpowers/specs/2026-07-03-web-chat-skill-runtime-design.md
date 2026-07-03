# Web Chat Prompt Skill Runtime 设计文档

## 概述

第一版从现有 LLM Proxy 方向扩展为一个 Web Chat 入口的 Hosted Prompt Skill Runtime。用户在网页中用自然语言对话，服务端自动判断是否触发某个托管 prompt skill，并在调用模型前完成 memory 检索、skill-aware context assembly、context trimming、模型调用和 invocation 日志记录。

第一版只实现 `single_call` 执行模式：每次用户消息最多触发一次 skill，组装一次上下文，发起一次模型调用，然后返回结果。不实现 agent-loop、script/tool execution、MCP facade、marketplace 支付、多人协作 UI，也不接入 Claude Agent SDK。后续版本可把 Claude Agent SDK 作为 execution backend，用于需要多步工具调用或自主循环的 skill。

## 目标

- 提供一个 ChatGPT-like Web Chat 入口，让用户通过自然语言触发托管 skill。
- 提供一个最小 Web Chat 页面，包含会话列表、消息区、输入框、流式响应展示和轻量 routing/memory metadata。
- 支持 prompt 型 skill 的注册、版本、启停和 workspace 安装。
- 根据用户消息和已安装 skill manifest 自动选择 skill 或 `no_skill`。
- 将 skill private prompt、memory、会话历史和当前用户消息组装为一次模型请求。
- Web Chat 支持流式输出：后端返回 `text/event-stream`，前端用 `fetch + ReadableStream` 消费 POST 响应。
- Memory 默认使用不依赖 cloud 的 mem0 OSS library backend，不单独部署 mem0 server。
- 按简单 token budget 规则裁剪上下文，保证关键上下文优先保留。
- 记录每次 skill invocation，展示路由结果、耗时、token、状态和 routing reason。
- 数据结构预留 `execution_mode` 和 `execution_engine`，方便后续接入 Claude Agent SDK。

## 非目标

- 不实现 agent-loop。
- 不接入 Claude Agent SDK。
- 不使用 Claude Agent SDK filesystem skills 作为托管 skill 机制。
- 不实现任意 script 执行或 tool calling。
- 不实现 MCP server / MCP facade。
- 不实现 marketplace、订阅支付和作者分成。
- 不实现复杂团队协作 UI；只在数据模型上预留 workspace。
- 不实现完整 Admin Web UI；skill 管理第一版通过 Admin API / seed 数据完成。
- 不实现 WebSocket、断点续传或可恢复流。
- 不使用 mem0 cloud。
- 不单独部署 mem0 self-hosted server。
- 不承诺强 prompt 防泄漏；第一版重点是服务端托管和 UI/API 不直接暴露 private prompt。

## 产品边界

第一版产品形态是一个 Web Chat，而不是 Claude Code / Cursor / Codex 请求中转。这样服务端可以完整控制请求生命周期：

1. 用户输入自然语言。
2. 服务端保存 user message。
3. 服务端基于公开 skill manifest 自动 routing。
4. 服务端读取被选中 skill 的 private prompt。
5. 服务端检索 memory。
6. 服务端组装和裁剪上下文。
7. 服务端调用模型。
8. 服务端保存 assistant message。
9. 服务端记录 invocation。
10. Web Chat 展示结果和轻量 routing 信息。

Web UI 可以展示本次是否使用 skill、使用的 skill 名称、routing reason 和 memory 命中数量，但不展示 private prompt。

第一版包含一个最小 Web Chat 页面：

- 左侧会话列表。
- 中间消息区。
- 底部输入框和发送按钮。
- Assistant 回复流式追加显示。
- Assistant 消息下展示 skill 名称、routing reason、memory hit count 和 token usage。
- 不提供完整 skill marketplace 或 Admin 管理界面。

## 部署形态

第一版只有 Gateway 一个后端服务。mem0 作为 Python library 运行在 Gateway 进程内，不作为独立 HTTP 服务部署。

本地开发：

- FastAPI Gateway
- 复用 `~/dev-services` 中 docker compose 管理的 MySQL（`localhost:3306`，数据库 `llm_proxy` / `llm_proxy_test`）
- mem0 OSS library
- 复用 `~/dev-services` 中 docker compose 管理的 Qdrant（HTTP `localhost:6333`，gRPC `localhost:6334`）
- Redis（`localhost:6379`）作为本地共享服务可用，但第一版不依赖 Redis
- 配置好的 LLM / embedding provider

正式部署：

- FastAPI Gateway
- MySQL
- mem0 OSS library
- 持久化 vector store（Qdrant）
- 配置好的 LLM / embedding provider

后续只有在需要 mem0 dashboard、独立 auth、独立 memory API 或跨服务复用 memory 能力时，才考虑 mem0 self-hosted server。

## 核心概念

### Workspace

第一版可以只有一个默认 workspace，但数据库层保留 `workspace_id`。这样单人使用体验简单，后续可以自然扩展到团队。

### Skill Manifest

用户可见的 skill 描述，用于展示和 routing。

字段包括：

- `name`
- `description`
- `tags`
- `trigger_examples`
- `input_guidance`
- `output_expectation`
- `is_active`

### Skill Version

每次发布产生一个版本。private prompt 挂在版本上，invocation 记录必须绑定具体版本，保证后续可追踪。

字段包括：

- `skill_id`
- `version`
- `private_prompt`
- `routing_hint`
- `execution_mode`
- `execution_engine`
- `is_published`

第一版固定：

- `execution_mode = "single_call"`
- `execution_engine = "model_gateway"`

### Workspace Skill Install

表示某个 workspace 可使用某个 skill。第一版不做订阅支付，只做安装/启停关系。

### Memory

第一版 MemoryService 封装 memory backend，默认使用 `mem0ai` OSS library 作为 `Mem0Backend`。mem0 作为进程内 Python library 被 Gateway 调用，不依赖 mem0 cloud，也不单独部署 mem0 self-hosted server。

MemoryService 负责产品语义和边界：

- 将 `workspace_id`、`user_id`、`conversation_id`、后续的 `skill_id` 映射为 mem0 metadata / filters。
- 将 mem0 search 结果归一化为 ContextAssembler 可消费的 memory blocks。
- 控制哪些 memory scope 能进入当前请求上下文。
- 记录 memory 检索和写入的审计信息。

mem0 负责 memory engine：

- 从对话中提取可长期保存的事实。
- 存储和检索 memory。
- 处理语义检索、多信号检索、实体和时间相关能力。

第一版支持三类产品 scope：

- `user`：跨会话用户偏好和长期事实。
- `workspace`：workspace 级共享背景。
- `conversation`：当前会话摘要或关键信息。

MemoryBackend 设计：

- `Mem0Backend`：第一版默认，使用 mem0 OSS library、本地配置的 LLM / embedding / vector store。
- `LiteMemoryBackend`：仅用于测试和降级，不作为产品主路径。

检索结果以 top-k memory blocks 注入上下文。ContextAssembler 不直接依赖 mem0，只依赖 MemoryService 返回的统一结构。

### Skill Invocation

一次用户消息触发一次 skill routing 后产生一条 invocation。即使结果是 `no_skill`，也可以记录一条 routing log，便于调试自动触发行为。

字段包括：

- `workspace_id`
- `user_id`
- `conversation_id`
- `message_id`
- `skill_id`
- `skill_version_id`
- `routing_mode`
- `routing_reason`
- `routing_confidence`
- `execution_mode`
- `execution_engine`
- `memory_hit_count`
- `prompt_tokens`
- `completion_tokens`
- `latency_ms`
- `status`
- `error_message`
- `started_at`
- `finished_at`

## 请求生命周期

```text
Web Chat 用户输入
  ↓
ChatService 保存 user message
  ↓
SkillRouter 从已安装 skill manifest 中选择 skill 或 no_skill
  ↓
MemoryService 检索 user/workspace/conversation memory
  ↓
ContextAssembler 注入 base prompt + skill private prompt + memory + history
  ↓
ContextTrimmer 按 token budget 裁剪
  ↓
ModelGateway 发起 single model call
  ↓
保存 assistant message
  ↓
InvocationLogger 记录 skill invocation
  ↓
返回 Web Chat
```

## 模块设计

### ChatService

职责：

- 接收 Web Chat 请求。
- 创建或读取 conversation。
- 保存 user message。
- 调用 routing、memory、context 和 model gateway。
- 保存 assistant message。
- 返回前端需要展示的数据。

ChatService 是业务编排入口，但不直接实现 routing、memory 和裁剪逻辑。

### SkillRegistry

职责：

- 管理 skill、skill version 和 workspace install。
- 对 Web Chat 暴露可见 manifest。
- 对 ContextAssembler 提供 private prompt。
- 保证只有已安装且启用的 skill 能被 routing 和调用。

第一版 Admin API 即可，不需要 Web 管理界面。

### SkillRouter

职责：

- 输入用户消息、conversation 摘要和当前 workspace 的可用 skill manifest。
- 输出 `no_skill` 或一个 skill 选择结果。
- 返回 routing reason 和 confidence。

第一版推荐使用模型做轻量分类：

```text
给定用户消息和 skill manifest 列表，选择最合适的 skill。
如果没有明显匹配，返回 no_skill。
只输出 JSON。
```

为了避免每次 routing 成本过高，可以先用 tags/keyword/embedding 取候选 top-k，再让模型从候选中选择。

### MemoryService

职责：

- 初始化和调用 memory backend，第一版默认 `Mem0Backend`。
- 将 workspace/user/conversation scope 转换为 backend metadata / filters。
- 基于当前用户消息检索相关 memory。
- 返回可注入上下文的 memory blocks。
- 响应后异步把新对话交给 backend 提取和保存 memory。
- 记录 memory search / add 的轻量审计信息。

第一版 memory 注入优先级：

1. user memory
2. conversation memory
3. workspace memory

检索结果统一交给 ContextAssembler，而不是直接拼进用户消息。

### ContextAssembler

职责：

- 组装模型请求上下文。
- 明确上下文来源和优先级。
- 保证 private prompt 不进入 Web UI 响应。

组装顺序：

1. 平台 base system prompt。
2. 被触发 skill 的 private prompt。
3. skill output expectation。
4. memory blocks。
5. 最近 conversation messages。
6. 当前 user message。

如果未触发 skill，则跳过 private prompt，只使用 base prompt、memory、history 和当前消息。

### ContextTrimmer

职责：

- 在模型上下文超出预算前裁剪。
- 保留关键内容。
- 返回裁剪统计，供 invocation log 使用。

第一版裁剪优先级：

1. base system prompt 必须保留。
2. skill private prompt 必须保留。
3. 当前 user message 必须保留。
4. memory top-k 尽量保留，超出时从低分到高分裁剪。
5. conversation history 按新到旧保留。
6. 更早历史直接丢弃；摘要留到后续版本。

### ModelGateway

职责：

- 执行 single model call。
- 复用现有 provider、fallback、budget 和 request log 能力。
- 返回 assistant text、usage、latency 和 provider 信息。

第一版不通过 Claude Agent SDK。模型调用仍走现有 LLM proxy/provider 抽象。

### InvocationLogger

职责：

- 记录 routing、memory、context、model 和结果状态。
- 支持后续排查为什么某个 skill 被触发或未触发。
- 为未来计费、分析和调优提供基础数据。

## 数据模型草案

```text
workspaces
  id
  name
  created_at

users
  id
  workspace_id
  name
  email
  created_at

conversations
  id
  workspace_id
  user_id
  title
  created_at
  updated_at

messages
  id
  conversation_id
  role
  content
  created_at

skills
  id
  owner_workspace_id
  name
  description
  tags
  trigger_examples
  is_active
  created_at
  updated_at

skill_versions
  id
  skill_id
  version
  private_prompt
  routing_hint
  output_expectation
  execution_mode
  execution_engine
  is_published
  created_at

workspace_skill_installs
  id
  workspace_id
  skill_id
  enabled_version_id
  is_enabled
  installed_at

memory_events
  id
  workspace_id
  user_id
  conversation_id
  skill_id
  scope
  operation
  backend
  backend_memory_id
  memory_preview
  created_at

skill_invocations
  id
  workspace_id
  user_id
  conversation_id
  message_id
  skill_id
  skill_version_id
  routing_mode
  routing_reason
  routing_confidence
  execution_mode
  execution_engine
  memory_hit_count
  prompt_tokens
  completion_tokens
  latency_ms
  status
  error_message
  started_at
  finished_at
```

## API 草案

### Web Chat

```text
POST /chat/conversations
GET  /chat/conversations
GET  /chat/conversations/{conversation_id}
POST /chat/conversations/{conversation_id}/messages
```

非流式 `POST /chat/conversations/{conversation_id}/messages` 返回：

```json
{
  "message": {
    "role": "assistant",
    "content": "..."
  },
  "routing": {
    "skill_used": true,
    "skill_name": "contract-reviewer",
    "reason": "用户请求审查合同风险",
    "confidence": 0.86
  },
  "memory": {
    "hit_count": 3
  },
  "usage": {
    "prompt_tokens": 1200,
    "completion_tokens": 420
  }
}
```

流式请求使用同一路径并传入 `stream=true`，响应 `Content-Type` 为 `text/event-stream`。前端用 `fetch + ReadableStream` 读取 POST 响应体，不使用 `EventSource`。

事件类型：

```text
event: routing
data: {"skill_used":true,"skill_name":"contract-reviewer","reason":"用户请求审查合同风险","confidence":0.86}

event: memory
data: {"hit_count":3}

event: delta
data: {"text":"这是"}

event: delta
data: {"text":"回答的一部分"}

event: done
data: {"invocation_id":123,"usage":{"prompt_tokens":1200,"completion_tokens":420}}
```

如果发生错误，返回：

```text
event: error
data: {"message":"模型调用失败"}
```

### Admin / Skill 管理

```text
POST /admin/skills
GET  /admin/skills
POST /admin/skills/{skill_id}/versions
POST /admin/workspaces/{workspace_id}/skills/{skill_id}/install
PUT  /admin/workspaces/{workspace_id}/skills/{skill_id}
GET  /admin/skill-invocations
```

第一版可以继续使用简单 admin key 鉴权。

## 错误处理

- SkillRouter 失败：降级为 `no_skill`，继续普通 chat。
- Memory 检索失败：记录 warning，继续不带 memory 的 chat。
- Memory 写入失败：不影响本次响应，记录 warning 和 memory event。
- Context 超预算：按裁剪优先级裁剪；如果仍超预算，返回 400。
- ModelGateway 失败：沿用 provider fallback；耗尽后返回 502。
- InvocationLogger 失败：不阻塞用户响应，但记录应用日志。

## 测试策略

- SkillRouter：给定 skill manifest 和用户消息，断言选择结果、no_skill 结果和 JSON 解析失败降级。
- ContextAssembler：断言 private prompt、memory、history 和当前消息按顺序组装。
- ContextTrimmer：断言关键内容保留，低优先级 history 先被裁剪。
- MemoryService：mock `Mem0Backend`，断言不同 scope 的 metadata / filters 正确，memory blocks 能被检索和注入。
- ChatService：端到端覆盖 skill 命中、no_skill、memory 命中、model failure。
- InvocationLogger：断言 skill version、execution fields、usage 和 status 被记录。

## 后续版本

### Claude Agent SDK Backend

当某个 skill 需要多步工具调用或自主循环时，新增 execution backend：

```text
execution_mode = "agent_loop"
execution_engine = "claude_agent_sdk"
```

此时请求链路保持前半段不变：

```text
skill routing
→ memory 检索
→ context assembly
→ context trimming
→ ClaudeAgentSdkBackend
→ final result
→ invocation log
```

Claude Agent SDK 只负责执行阶段，不负责产品级 skill registry、private prompt 管理、memory ownership、权限和审计。

### Script / Tool Runtime

后续可增加 HTTP tool proxy、MCP facade 或 sandboxed script runtime。任意作者上传 script 是质变，需要单独设计沙箱、网络权限、依赖、资源限制和 secret 注入。

## 成功标准

- 用户可以在 Web Chat 中自然语言触发 prompt skill。
- Web UI 可以展示使用了哪个 skill 和 routing reason。
- Skill private prompt 不通过 API 或 UI 返回。
- 同一个用户跨会话可以检索到已有 memory。
- Invocation 日志能解释一次请求的 routing、memory、模型调用和最终状态。
- 第一版实现只依赖 `single_call`，但数据模型能支持后续接入 Claude Agent SDK。
