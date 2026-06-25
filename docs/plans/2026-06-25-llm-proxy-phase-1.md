# LLM Proxy 第一阶段实现计划

## 上下文

依据 `../superpowers/specs/2026-06-24-llm-proxy-design.md` 设计文档，从零搭建一个 FastAPI 版的 LLM Proxy。对外暴露 Anthropic Messages API，内部统一为 OpenAI 格式，并接入 DeepSeek Anthropic 端点；同时实现 mem0 风格的 Memory 管理、fallback/retry、预算与用量管理。

本计划只覆盖第一阶段范围：Auth + 限流、Anthropic ↔ Internal ↔ DeepSeek、SSE 流式、重试 fallback、Admin CRUD、预算、请求日志、Memory 写入/检索/LLM 提取。Token 预检和 Tool use 透传留到第二阶段。

---

## 步骤 1：项目初始化与配置

- **任务边界**
  - 创建 `pyproject.toml`，按 CLAUDE.md 默认工具链配置 uv、ruff、pyright、pytest、pytest-asyncio。
  - 添加第一阶段依赖：FastAPI、uvicorn、SQLAlchemy[asyncio]、asyncmy、httpx、ChromaDB、pydantic-settings、alembic、python-dotenv（如需要）。
  - 创建 `app/config.py`（pydantic-settings），定义数据库 URL、ChromaDB 持久化目录、admin_key、默认模型路由等配置项。
  - 不实现生产部署配置（Docker、K8s 等）和日志中间件。

- **行为目标**
  - `uv sync` 能成功安装依赖。
  - `app/config.py` 能从环境变量读取配置并提供类型安全访问。
  - ruff / pyright / pytest 工具链可正常跑通空项目。

- **涉及模块**
  - `pyproject.toml`
  - `app/config.py`

- **验证方式**
  - 运行 `uv sync` 无报错。
  - 运行 `uv run python -c "from app.config import settings; print(settings)"` 能打印默认值。
  - 运行 `ruff check .` 和 `pyright` 通过（空项目阶段）。

---

## 步骤 2：数据库模型与迁移

- **任务边界**
  - 使用 SQLAlchemy 2.0 async ORM 在 `app/db/models.py` 中定义 `users`、`api_keys`、`providers`、`model_routes`、`budgets`、`model_pricing`、`request_logs`、`conversations` 表。
  - 初始化 Alembic，生成第一张 revision 创建所有表。
  - `app/db/session.py` 提供 async engine 和 `async_sessionmaker`。
  - 不预填 seed 数据，不实现复杂索引优化。

- **行为目标**
  - 所有表结构与 spec 中 SQL 定义一致，外键、CHECK 约束、默认值正确。
  - `session.py` 提供可注入 FastAPI 依赖的 `get_db_session()`。
  - Alembic `upgrade head` 能创建表。

- **涉及模块**
  - `app/db/models.py`
  - `app/db/session.py`
  - `alembic/alembic.ini`
  - `alembic/env.py`
  - `alembic/versions/xxx_initial.py`

- **验证方式**
  - 本地启动 MySQL 8.0，执行 `uv run alembic upgrade head` 后检查 8 张表已创建。
  - 编写最小 pytest 用例：使用 `session.py` 插入并查询 `User`、`ApiKey`、`Provider` 记录，确认 ORM 映射正确。

---

## 步骤 3：认证与限流中间件

- **任务边界**
  - `app/middleware/auth.py`：读取 `x-api-key` header，SHA-256 哈希后查 `api_keys` 表；校验 `is_active`；通过 `request.state` 注入 `user_id`、`api_key_id`。
  - `app/middleware/rate_limit.py`：固定窗口 60s，窗口内请求数上限取自 `api_keys.rate_limit`；超限时返回 429。
  - 认证失败返回 401。
  - 限流使用内存计数（本阶段不引入 Redis）。

- **行为目标**
  - 每个 `/v1/messages` 请求必须先过 auth，再过 rate limit。
  - 缺失/错误 key 返回 401；停用 key 返回 401。
  - 超过 rate_limit 后同一窗口内返回 429，下一窗口重置。

- **涉及模块**
  - `app/middleware/auth.py`
  - `app/middleware/rate_limit.py`

- **验证方式**
  - 编写 `tests/test_auth.py` 和 `tests/test_rate_limit.py`，覆盖：无 key、错误 key、停用 key、正常 key、超限 429、窗口重置。
  - 使用 FastAPI `TestClient` + 内存 SQLite（或注入 mock session）验证中间件行为。

---

## 步骤 4：内部统一格式与适配器

- **任务边界**
  - `app/adapters/schemas.py` 定义 `InternalRequest`、`InternalResponse`、`InternalStreamChunk`、`Usage`，均使用 Pydantic v2。
  - `app/adapters/anthropic_in.py`：将 Anthropic Messages 请求解析为 `InternalRequest`；处理 `messages`、`system`、`model`、`max_tokens`、`temperature`、`stream`、`stop`。
  - `app/adapters/anthropic_out.py`：将 `InternalResponse` / `InternalStreamChunk` 转回 Anthropic Messages 格式；支持非流 JSON 和 SSE 流式。
  - `extra` 字段保留格式特有字段但不展开复杂转换。
  - 第一阶段 tool 相关字段透传但不校验。

- **行为目标**
  - Anthropic 请求能完整映射到 OpenAI 风格内部格式，关键字段不丢失。
  - 内部响应能生成符合 Anthropic Messages API 的 JSON 和 SSE 行。
  - SSE 流能正确输出 `content_block_delta`、`message_delta` 等事件。

- **涉及模块**
  - `app/adapters/schemas.py`
  - `app/adapters/anthropic_in.py`
  - `app/adapters/anthropic_out.py`

- **验证方式**
  - 编写 `tests/test_anthropic_in.py`：给定 Anthropic 请求 JSON，断言 `InternalRequest` 字段正确。
  - 编写 `tests/test_anthropic_out.py`：给定 `InternalResponse` / chunk 列表，断言输出 JSON / SSE 事件符合 Anthropic 结构。
  - 使用 snapshot 或字面量断言关键字段。

---

## 步骤 5：Provider 层

- **任务边界**
  - `app/providers/base.py`：定义 `BaseProvider` 抽象接口 `chat_completion(request: InternalRequest) -> InternalResponse` 和 `chat_completion_stream(...)`。
  - `app/providers/deepseek.py`：实现 DeepSeek Anthropic 端点调用；
    - 将 `InternalRequest` 转回 Anthropic Messages 格式发向 `https://api.deepseek.com/anthropic`；
    - 解析非流响应为 `InternalResponse`；
    - 解析流式 SSE 为 `InternalStreamChunk` 生成器。
  - provider 配置从 `providers` 表读取（base_url、api_key、provider_model）。
  - 不实现其他供应商。

- **行为目标**
  - 给定 `InternalRequest`，DeepSeek provider 能构造正确的外部请求体并返回内部格式结果。
  - 流式请求返回异步生成器，逐块产出 `InternalStreamChunk`。
  - 能处理 DeepSeek 返回的 usage 并填入 `Usage`。

- **涉及模块**
  - `app/providers/base.py`
  - `app/providers/deepseek.py`

- **验证方式**
  - 编写 `tests/test_providers.py`，使用 `respx` 或 `httpx.AsyncClient` + `httpx.MockTransport` mock DeepSeek 端点。
  - 覆盖非流成功、流式成功、5xx 异常抛出、4xx 异常抛出。
  - 断言请求体字段和返回的 `InternalResponse` / chunk 内容。

---

## 步骤 6：Fallback 与重试服务

- **任务边界**
  - `app/services/fallback.py`：
    - 根据 `model_name` 查 `model_routes`，按 `priority` 排序得到可用 provider 列表；
    - 对单个 provider 最多重试 3 次，指数退避 1s → 2s → 4s；
    - 触发重试的条件：5xx、网络超时、连接错误、429；
    - 4xx（非 429）直接切下一个 provider；
    - 所有 provider 耗尽返回 502。
  - 区分流式与非流式调用路径，流式失败时整条流失败并触发 fallback。

- **行为目标**
  - 主 provider 成功时直接返回结果。
  - 主 provider 连续失败达到条件后自动切到次优先级 provider。
  - 重试间隔符合指数退避；429 也被重试。
  - 全部失败返回明确 502。

- **涉及模块**
  - `app/services/fallback.py`
  - `app/db/models.py` 中的 `model_routes`、`providers`

- **验证方式**
  - 编写 `tests/test_fallback.py`，mock provider 调用。
  - 覆盖：一次成功、三次失败后切换 provider、429 触发重试、4xx 直接切换、全部耗尽 502。
  - 使用 `pytest-asyncio` 和 `freezegun` / 手动计时验证退避间隔。

---

## 步骤 7：预算服务

- **任务边界**
  - `app/services/budget.py`：
    - 请求前检查 `budgets` 表：若 `current_cents + 预估费用 > amount_cents`，返回 402；
    - 请求成功后 `UPDATE budgets SET current_cents += actual_cost`；
    - 预估费用按 `model_pricing` 与请求 `max_tokens` 粗略估算（或简化为 0，本阶段优先保证实际扣减）。
  - 预算可按 `user_id` 或 `api_key_id` 绑定；两者都有时优先按 `api_key_id` 检查。

- **行为目标**
  - 有预算且充足时放行。
  - 预算不足时返回 402，不转发请求。
  - 请求结束后按实际 token 用量和 `model_pricing` 计算费用并更新 `current_cents`。
  - 无预算记录时不拦截。

- **涉及模块**
  - `app/services/budget.py`
  - `app/db/models.py` 中的 `budgets`、`model_pricing`

- **验证方式**
  - 编写 `tests/test_budget.py`，使用内存 session 或 mock DB。
  - 覆盖：无预算放行、预算充足放行并扣费、预算不足 402、多预算冲突策略。
  - 断言扣费后 `current_cents` 正确。

---

## 步骤 8：Memory 子系统

- **任务边界**
  - `app/services/memory.py`：
    - 初始化 ChromaDB client，collection 名为 `memories`，持久化目录来自 `config`；
    - `retrieve(query, api_key_id, conversation_id, top_k=5)`：调用 DeepSeek Embedding API 获取 query 向量，ChromaDB 搜索后按同 conversation 加权 1.5，返回 top 3 记忆文本；
    - `inject(memories, messages)`：将记忆拼成 `<memories>...</memories>` 注入到第一条 system message 前；
    - `extract_and_store(conversation_id, api_key_id, user_id, messages)`：构造提取 prompt，调用 proxy 自身（轻量模型），解析新增记忆，embed 后写入 ChromaDB；内容相同则跳过。
  - `app/db/models.py` 中的 `conversations` 表在首次请求时自动创建记录。
  - 不实现图检索、记忆合并、TTL 清理。

- **行为目标**
  - 每次 `/v1/messages` 请求前同步检索记忆并注入 system。
  - 响应后异步触发记忆提取与写入。
  - 重复记忆 content 不重复写入。
  - 新 conversation 在数据库中自动创建。

- **涉及模块**
  - `app/services/memory.py`
  - `app/db/models.py` 中的 `conversations`

- **验证方式**
  - 编写 `tests/test_memory.py`，mock embedding API 和 ChromaDB（或用内存 ChromaDB client）。
  - 覆盖：检索返回加权排序、注入后 messages 结构正确、提取 prompt 构造正确、去重逻辑。
  - 使用代理端点自身调用时，mock 该内部请求。

---

## 步骤 9：Proxy 编排服务与主路由

- **任务边界**
  - `app/services/proxy.py`：编排请求生命周期：
    1. Auth / rate limit（中间件层完成）；
    2. Budget check；
    3. Memory retrieve + inject；
    4. `anthropic_in` 转 `InternalRequest`；
    5. fallback 选择 provider 并调用；
    6. `anthropic_out` 转 Anthropic 响应；
    7. Budget update；
    8. 异步写 `request_logs`；
    9. 异步 Memory extract。
  - `app/router.py`：注册 `POST /v1/messages`，依赖 auth/rate_limit，调用 `proxy.handle_messages()`，返回 JSON 或 SSE。
  - `app/main.py`：组装 FastAPI app，注册中间件、router、admin router、health endpoint。
  - 流式请求 `[8][9]` 在流结束后触发。

- **行为目标**
  - 非流请求返回 Anthropic Messages 格式 JSON。
  - 流请求返回 `text/event-stream` SSE。
  - 请求日志记录 model、provider、token 用量、latency、status、cost。
  - 异常按 fallback 策略处理，最终失败返回 502/429/402 等。

- **涉及模块**
  - `app/services/proxy.py`
  - `app/router.py`
  - `app/main.py`

- **验证方式**
  - 编写 `tests/test_proxy.py`，使用 `TestClient` + mock 所有外部依赖（DB、embedding、DeepSeek）。
  - 覆盖：非流成功、流式成功、预算不足 402、fallback 切换、请求日志写入、记忆注入/提取触发。
  - 运行 `uv run python -m pytest`。

---

## 步骤 10：Admin 管理端点

- **任务边界**
  - `app/admin/` 下实现 users、keys、providers、models、budgets、logs 的 CRUD API。
  - Admin 端点统一使用 `x-admin-key` header 鉴权，值取自 `config.admin_key`。
  - `/admin/keys` 创建时返回完整 key，其他接口不返回原始 key；`/admin/providers` 对 `api_key` 脱敏显示。
  - `/admin/budgets` 列出时计算消耗百分比。
  - `/admin/logs` 支持 key_id、model、from、to 过滤。
  - 不实现 Web UI 和复杂分页。

- **行为目标**
  - 所有管理接口受 admin_key 保护，错误 key 返回 403。
  - CRUD 操作正确映射到数据库表。
  - 创建 API key 时生成随机字符串并存储 SHA-256 哈希。
  - 查询日志支持过滤条件。

- **涉及模块**
  - `app/admin/users.py`
  - `app/admin/keys.py`
  - `app/admin/providers.py`
  - `app/admin/models.py`
  - `app/admin/budgets.py`
  - `app/admin/logs.py`
  - `app/main.py` 注册 admin router

- **验证方式**
  - 编写 `tests/test_admin.py`，使用 `TestClient` + 内存 session。
  - 覆盖：admin_key 错误 403、用户 CRUD、key 创建返回明文且只返回一次、provider api_key 脱敏、budget 消耗百分比、logs 过滤。

---

## 步骤 11：集成与端到端验证

- **任务边界**
  - 完善 `tests/conftest.py`：提供 FastAPI app fixture、内存/测试 DB session、mock provider、mock embedding。
  - 运行全量测试，修复失败项。
  - 执行 `ruff check . --fix`、`ruff format .`、`pyright`。
  - 本地启动 MySQL + 应用，用 curl / HTTP 客户端调用 `/health`、`/admin/users`、`/v1/messages` 做冒烟测试。
  - 不实现 CI/CD 流水线。

- **行为目标**
  - 全量测试通过。
  - lint、format、类型检查无错误。
  - 本地端到端调用 `/v1/messages` 能返回 Anthropic 格式响应（mock 外部 API 或真实 key）。

- **涉及模块**
  - `tests/conftest.py`
  - 所有已创建模块

- **验证方式**
  - `uv run python -m pytest`
  - `ruff check .` && `ruff format .`
  - `pyright`
  - 本地启动：`uv run uvicorn app.main:app --reload`，验证 `/health` 返回 OK，使用真实 DeepSeek key 验证一次非流 `/v1/messages`（可选，取决于环境）。

---

## 实现顺序图

```
步骤 1 项目初始化
    ↓
步骤 2 数据库模型与迁移
    ↓
步骤 3 认证与限流中间件
    ↓
步骤 4 内部统一格式与适配器
    ↓
步骤 5 Provider 层
    ↓
步骤 6 Fallback 与重试服务
    ↓
步骤 7 预算服务
    ↓
步骤 8 Memory 子系统
    ↓
步骤 9 Proxy 编排服务与主路由
    ↓
步骤 10 Admin 管理端点
    ↓
步骤 11 集成与端到端验证
```

> 说明：每个步骤完成后即运行对应测试，不累积到末尾统一验证。
