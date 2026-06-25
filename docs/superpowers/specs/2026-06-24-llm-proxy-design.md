# LLM Proxy 设计文档

## 概述

一个类似 LiteLLM 的 LLM 代理服务（Python + FastAPI），对外暴露 Anthropic Messages API 格式，内部做统一格式转换后转发到多个供应商（首发 DeepSeek Anthropic 端点）。同时集成 mem0 风格的 Memory 管理，根据会话上下文自动检索和注入记忆。

## 核心目标

- **格式网关**：Anthropic API ↔ Internal ↔ 供应商 Anthropic API，理解并对比 LiteLLM 内部转换机制
- **Memory 管理**：mem0 风格——嵌入存储 + LLM 提取 + 检索注入
- **多供应商**：可扩展 provider 体系，含 fallback 和重试
- **管理能力**：用户/API Key/预算/用量管理

## 架构总览

```
Agent (Anthropic SDK)
       │ POST /v1/messages
       ▼
┌──────────────────────────────────────────────────┐
│                  FastAPI 网关层                    │
│                                                   │
│  ┌────────┐  ┌───────────┐  ┌───────────────┐   │
│  │  Auth  │→ │ RateLimit │→ │ Budget Check  │   │
│  └────────┘  └───────────┘  └───────┬───────┘   │
│                                     │            │
│  ┌──────────────────────────────────▼────────┐   │
│  │           Memory 子系统                    │   │
│  │  检索: embed(query) → ChromaDB → top-K    │   │
│  │  注入: 拼入 system message 头部            │   │
│  │  提取: 响应后异步 LLM 提取 → 写入向量库    │   │
│  └──────────────────────────────────┬────────┘   │
│                                     │            │
│  ┌──────────────────────────────────▼────────┐   │
│  │            Adapter 层 (M+N)               │   │
│  │                                           │   │
│  │   Anthropic Request                     │   │
│  │       │ anthropic_in.py                  │   │
│  │       ▼                                   │   │
│  │   InternalRequest (OpenAI 格式)           │   │  ← 复用 OpenAI ModelResponse 结构体作为内部格式
│  │       │ provider adapter                  │   │
│  │       ▼                                   │   │
│  │   Anthropic → DeepSeek /anthropic        │   │
│  │   Anthropic ← DeepSeek 响应               │   │
│  │       │ anthropic_out.py                 │   │
│  │       ▼                                   │   │
│  │   Anthropic Response (SSE / JSON)        │   │
│  └──────────────────────────────────────────┘   │
│                                     │            │
│  ┌──────────────────────────────────▼────────┐   │
│  │   Fallback & Retry                        │   │
│  │   主 provider → retry(3次) → fallback      │   │
│  │   provider → retry(3次) → ...              │   │
│  └──────────────────────────────────────────┘   │
│                                     │            │
│  ┌──────────────────────────────────▼────────┐   │
│  │   Usage Log (异步 MySQL)                   │   │
│  └──────────────────────────────────────────┘   │
└──────────────────────┬───────────────────────────┘
                       │
                       ▼
              ┌────────────────┐
              │  DeepSeek API   │  POST https://api.deepseek.com/anthropic
              └────────────────┘
```

## 统一内部格式

复用 OpenAI 格式作为内部结构（与 LiteLLM 策略一致）：

- `InternalRequest` ≈ OpenAI `ChatCompletionRequest`
  - `model`, `messages`, `max_tokens`, `temperature`, `stream`, `tools`, `stop`, `extra: dict`
- `InternalResponse` ≈ OpenAI `ChatCompletion`
  - `id`, `model`, `choices`, `usage`, `extra: dict`
- `InternalStreamChunk` ≈ OpenAI `ChatCompletionChunk`
  - 流式 SSE 逐块，含 delta / finish_reason 等
- `Usage`: `prompt_tokens`, `completion_tokens`, `total_tokens`

### 设计原则

- `extra` 字段存格式特有字段（等效 LiteLLM 的 `hidden_params`），保证来回翻译不丢信息
- 第一阶段不实现 tool use——adapter 透传 tool 相关字段即可

### 与 LiteLLM 对比

| | LiteLLM | 我们 |
|---|---|---|
| 内部格式 | 直接用 OpenAI `ModelResponse` | 同样用 OpenAI 格式 |
| 扩展字段 | `hidden_params` dict | 显式 `extra` dict |
| 流式 | 复用 OpenAI streaming 格式 | 同，SSE 逐块透传 |
| tool use | 原生支持 | 第一阶段透传字段 |

## Adapter 层职责

```
Anthropic 请求                     Anthropic 响应
     │                                   ▲
     ▼                                   │
anthropic_in.py                   anthropic_out.py
     │                                   ▲
     ▼                                   │
OpenAI ChatCompletionRequest ──→ DeepSeek ──→ ChatCompletionResponse
```

| 组件 | 文件 | 职责 |
|---|---|---|
| Anthropic → Internal | `adapters/anthropic_in.py` | 解析 Anthropic Messages 格式 → OpenAI ChatCompletionRequest |
| Internal → Anthropic | `adapters/anthropic_out.py` | OpenAI ChatCompletion → Anthropic Messages/Streaming 格式 |
| Internal ↔ Provider-Anthropic | `providers/deepseek.py` | 对 DeepSeek Anthropic 端点发请求 |

### 与 LiteLLM 对应

| 我们 | LiteLLM |
|---|---|
| `adapters/anthropic_in.py` | `litellm/llms/anthropic/chat/transformation.py` (AnthropicConfig) |
| `adapters/anthropic_out.py` | 同上 transformation.py 响应处理部分 |
| `providers/base.py` | `litellm/llms/base_llm/` (BaseLLM) |
| `providers/deepseek.py` | `litellm/llms/deepseek/` |

## 数据库设计

### ER 图

```
users ──< api_keys ──< request_logs
  │         │
  ├──< budgets
  │
providers ──< model_routes
model_pricing (独立，按模型名查)
```

### 表结构

```sql
-- 用户
CREATE TABLE users (
    id          BIGINT PRIMARY KEY AUTO_INCREMENT,
    name        VARCHAR(128) NOT NULL,
    email       VARCHAR(256),
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- API Key
CREATE TABLE api_keys (
    id          BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id     BIGINT NOT NULL,
    key_hash    VARCHAR(64) NOT NULL UNIQUE,
    name        VARCHAR(128) NOT NULL,
    is_active   BOOLEAN DEFAULT TRUE,
    rate_limit  INT DEFAULT 60,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- 供应商
CREATE TABLE providers (
    id          BIGINT PRIMARY KEY AUTO_INCREMENT,
    name        VARCHAR(64) NOT NULL UNIQUE,
    base_url    VARCHAR(256) NOT NULL,
    api_key     VARCHAR(256) NOT NULL,
    is_active   BOOLEAN DEFAULT TRUE,
    config      JSON,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 模型路由（一个模型可配多个 provider 做 fallback）
CREATE TABLE model_routes (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    model_name      VARCHAR(128) NOT NULL,
    provider_id     BIGINT NOT NULL,
    provider_model  VARCHAR(128) NOT NULL,
    priority        INT DEFAULT 0,      -- 越小越优先
    is_active       BOOLEAN DEFAULT TRUE,
    FOREIGN KEY (provider_id) REFERENCES providers(id)
);

-- 预算
CREATE TABLE budgets (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id         BIGINT,
    api_key_id      BIGINT,
    amount_cents    INT NOT NULL,
    current_cents   INT DEFAULT 0,
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    is_active       BOOLEAN DEFAULT TRUE,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (api_key_id) REFERENCES api_keys(id),
    CHECK (user_id IS NOT NULL OR api_key_id IS NOT NULL)
);

-- 模型单价
CREATE TABLE model_pricing (
    id                      BIGINT PRIMARY KEY AUTO_INCREMENT,
    model_name              VARCHAR(128) NOT NULL,
    input_cents_per_mtok    INT NOT NULL,
    output_cents_per_mtok   INT NOT NULL,
    updated_at              DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 请求日志
CREATE TABLE request_logs (
    id                BIGINT PRIMARY KEY AUTO_INCREMENT,
    api_key_id        BIGINT,
    user_id           BIGINT,
    model             VARCHAR(128),
    provider          VARCHAR(64),
    stream            BOOLEAN,
    prompt_tokens     INT DEFAULT 0,
    completion_tokens INT DEFAULT 0,
    latency_ms        INT,
    status            VARCHAR(16),
    error_msg         TEXT,
    cost_cents        INT DEFAULT 0,
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (api_key_id) REFERENCES api_keys(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
```

## Memory 子系统

### 设计目标

类似 mem0——基于 embedding 向量存储、LLM 自动提取、检索注入以实现对话记忆。

### 组件与选型

| 组件 | 选型 | 理由 |
|---|---|---|
| 向量库 | ChromaDB（嵌入式） | Python 原生，零外部依赖，本地持久化 |
| Embedding API | DeepSeek Embedding | 复用供应商，无需本地 GPU |
| Memory 提取 LLM | 走自己的 proxy | 自举——调自己来提取记忆 |
| 检索时机 | 每次请求前（同步） | 延迟可控 |
| 提取时机 | 响应后（异步后台任务） | 不阻塞用户响应 |

### 数据模型

```python
# ChromaDB collection: "memories"
{
    "id": "mem_<uuid>",
    "content": "用户偏好使用中文回答",
    "metadata": {
        "conversation_id": "ext_123",
        "api_key_id": 1,
        "user_id": 1,
        "source_message_role": "user",
        "created_at": "2026-06-24T10:00:00Z"
    },
    "embedding": [...]  # ChromaDB 自动管理
}
```

```sql
-- 会话表（MySQL）
CREATE TABLE conversations (
    id          BIGINT PRIMARY KEY AUTO_INCREMENT,
    external_id VARCHAR(64) NOT NULL UNIQUE,
    api_key_id  BIGINT NOT NULL,
    user_id     BIGINT NOT NULL,
    title       VARCHAR(256),
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (api_key_id) REFERENCES api_keys(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
```

### 检索流程（同步）

```
1. agent 当前消息 → embed(query)
2. ChromaDB.search(top_k=5, where={"api_key_id": X})
3. 同 conversation 的记忆加权（分数 × 1.5）
4. 取 top 3 条
5. 拼成 XML 注入到 messages 中第一条 system message 前:
   <memories>
   - 用户偏好使用中文回答
   - 用户在开发一个 SEO 工具
   </memories>
```

### 提取流程（异步）

```
1. 响应返回后 → asyncio.create_task
2. 构造提取 prompt:
   "从以下对话中提取用户偏好、事实、上下文信息。
    已有记忆: [...]
    新对话: user: ... / assistant: ...
    只输出新增或更新的记忆，每条一行 JSON。没有则输出 NONE。"
3. 调用 proxy 自己（POST /v1/messages, 模型配专用小模型）
4. 解析输出 → embed → ChromaDB.upsert（content 相同则跳过）
5. 新 conversation 自动写 MySQL conversations 表
```

### 与 mem0 对比

| | mem0 | 我们 |
|---|---|---|
| 向量库 | Qdrant / Pinecone / ChromaDB | ChromaDB |
| LLM 提取 | 可配 OpenAI/Anthropic | 走自己 proxy 的 provider |
| 去重/合并 | 有图检索去重 | content 级去重 |
| 检索权重 | 两阶段（向量 + 图） | 向量 + conversation_id boost |

## 请求生命周期

```
POST /v1/messages  (Anthropic 格式, Header: x-api-key)
        │
   [1] Auth: SHA-256(api_key) → 查 api_keys → 注入 user_id + api_key_id
   [2] Rate Limit: 固定窗口，窗口宽度 60s，上限 api_keys.rate_limit
        │
   [3] Budget Check: 查 budgets 表 → current_cents + 预估 > amount_cents ?
        │ 是 → 402
        ▼
   [4] Memory Retrieve: embed last message → ChromaDB → 注入 system
        │
   [5] Input Adapter: Anthropic → InternalRequest (OpenAI 格式)
   [6] Route Resolve: model_name → model_routes (按 priority 排序)
        │
   [7] Fallback & Retry 循环:
       for provider in route.providers:
           for retry in 0..2 (最多3次):
               成功 → break
               5xx/超时/429 → sleep(2^retry 秒) 后继续
               4xx(非429) → 不重试，切下一 provider
           所有 provider 耗尽 → 502
        │
   [8] Output Adapter: InternalResponse → Anthropic Messages 格式
        │
   [9] Budget Update: UPDATE budgets SET current_cents += actual_cost
  [10] Usage Log: 异步 INSERT request_logs
  [11] Memory Extract: 异步 LLM 提取 → ChromaDB.upsert
        │
        ▼
   返回给 agent
```

### 流式简化

流式通道——`[8]` 变为逐块 SSE 输出，`[9]` 和 `[11]` 在流结束后触发，其余步骤不变。

## 重试与 Fallback

### 重试条件

- 触发：5xx、网络超时、连接错误、429
- 策略：最多 3 次，指数退避 (1s → 2s → 4s)
- 不触发：4xx（除 429）—— 请求本身有问题

### Fallback

- 模型可配置多个 provider（model_routes 同 model_name 多行，按 priority）
- 当前 provider 3 次重试均失败 → 切 priority 次高的 provider
- 所有 provider 耗尽 → 502

### 与 LiteLLM 对比

| | LiteLLM | 我们 |
|---|---|---|
| 重试 | `num_retries` + `RetryPolicy` | 同样指数退避 |
| Fallback | `context_window_fallback` + `default_fallback` | model 级别 fallback，不做窗口感知 fallback |
| 429 | 内置 cooldown | 简单重试（不记录 cooldown 时间） |

## API 端点

### 代理层

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/v1/messages` | Anthropic Messages API 兼容端点 |

### 管理端

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/admin/users` | 创建用户 |
| `GET` | `/admin/users` | 列出用户 |
| `PUT` | `/admin/users/{id}` | 更新用户 |
| `DELETE` | `/admin/users/{id}` | 软删除 |
| `POST` | `/admin/keys` | 创建 key（返回完整 key） |
| `GET` | `/admin/keys` | 列出 key（不返回原始 key） |
| `PUT` | `/admin/keys/{id}` | 更新 key |
| `DELETE` | `/admin/keys/{id}` | 软删除 |
| `POST` | `/admin/providers` | 创建 provider |
| `GET` | `/admin/providers` | 列出 provider（api_key 脱敏） |
| `PUT` | `/admin/providers/{id}` | 更新 provider |
| `DELETE` | `/admin/providers/{id}` | 软删除 |
| `POST` | `/admin/models` | 绑定模型路由 |
| `GET` | `/admin/models` | 列出所有路由 |
| `PUT` | `/admin/models/{id}` | 更新路由 |
| `DELETE` | `/admin/models/{id}` | 删除路由 |
| `POST` | `/admin/budgets` | 创建预算 |
| `GET` | `/admin/budgets` | 列出预算（含消耗百分比） |
| `PUT` | `/admin/budgets/{id}` | 更新预算/重置周期 |
| `DELETE` | `/admin/budgets/{id}` | 删除预算 |
| `GET` | `/admin/logs` | 查询请求日志（过滤: key_id, model, from, to） |

### 系统

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/health` | 健康检查 |

## 项目目录结构

```
/
├── pyproject.toml
├── alembic/
│   ├── alembic.ini
│   ├── env.py
│   └── versions/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py               # pydantic-settings
│   │
│   ├── db/
│   │   ├── __init__.py
│   │   ├── session.py           # async engine + session factory
│   │   └── models.py            # SQLAlchemy 2.0+ ORM
│   │
│   ├── middleware/
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   └── rate_limit.py
│   │
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── schemas.py           # InternalRequest / InternalResponse / Usage
│   │   ├── anthropic_in.py      # Anthropic Messages → InternalRequest
│   │   └── anthropic_out.py     # InternalResponse → Anthropic Messages/SSE
│   │
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── base.py              # Provider 基类
│   │   └── deepseek.py          # DeepSeek Anthropic 端点
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── proxy.py             # 核心转发编排
│   │   ├── fallback.py          # 模型路由 + provider 切换
│   │   ├── budget.py            # 预算检查 & 扣减
│   │   └── memory.py            # Memory 检索 / 提取 / ChromaDB 操作
│   │
│   ├── admin/
│   │   ├── __init__.py
│   │   ├── users.py
│   │   ├── keys.py
│   │   ├── providers.py
│   │   ├── models.py
│   │   ├── budgets.py
│   │   └── logs.py
│   │
│   └── router.py                # POST /v1/messages
│
└── tests/
    ├── conftest.py
    ├── test_anthropic_in.py
    ├── test_anthropic_out.py
    ├── test_proxy.py
    ├── test_memory.py
    └── test_fallback.py
```

## 技术栈

| 用途 | 选型 | 版本要求 |
|---|---|---|
| 框架 | FastAPI | latest |
| 数据库 | MySQL | 8.0+ |
| ORM | SQLAlchemy | 2.0+ (async) |
| 驱动 | asyncmy | latest |
| HTTP 客户端 | httpx (async) | latest |
| 向量数据库 | ChromaDB | latest |
| Embedding | DeepSeek Embedding API | — |
| 数据库迁移 | Alembic | latest |
| 配置 | pydantic-settings | latest |
| Lint | ruff | per CLAUDE.md |
| 类型检查 | pyright | basic 模式 |
| 测试 | pytest + pytest-asyncio | latest |
| 包管理 | uv | per CLAUDE.md |

## 第一阶段范围

| 模块 | 实现 |
|---|---|
| Auth + 限流 | ✓ |
| Anthropic ↔ Internal ↔ DeepSeek | ✓ |
| 流式响应 (SSE) | ✓ |
| 重试 + fallback | ✓ |
| 用户/Key/Provider/Model CRUD | ✓ |
| 预算管理 | ✓ |
| 请求日志 + 用量 | ✓ |
| Memory 写入/检索 | ✓ |
| Memory 提取（LLM） | ✓ |
| Token 预检 | 第二阶段 |
| Tool use 透传 | 第二阶段 |
| Admin Web UI | 不实现（纯 API） |

## 已知待定项

- ChromaDB 数据持久化目录：默认 `./data/chromadb`，通过 config 可配
- Embedding 模型名：DeepSeek `text-embedding-3-small` 或类似，确认后写入 provider config
- Memory 提取用的 LLM model：在 model_routes 配成专用轻量模型
- Admin 端点无独立鉴权：第一阶段用简单的 admin_key 配置项，生产环境改网络隔离
