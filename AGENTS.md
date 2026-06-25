# AGENTS.md — LLM Proxy

## 项目概述

类似 LiteLLM 的 LLM 代理服务（Python + FastAPI）。对外暴露 Anthropic Messages API 格式，内部统一为 OpenAI 风格格式，再转发到多个供应商（第一阶段只接入 DeepSeek Anthropic 端点）。同时集成 mem0 风格的 Memory 管理，按会话自动检索和注入记忆。

第一阶段交付：Auth + 限流、Anthropic ↔ Internal ↔ DeepSeek、SSE 流式、重试 fallback、Admin CRUD、预算、请求日志、Memory 写入/检索/LLM 提取。

## 技术栈

| 环节 | 选型 | 说明 |
|---|---|---|
| API 框架 | FastAPI | 对外暴露 `/v1/messages` 和 Admin 端点 |
| ORM | SQLAlchemy 2.0 async | MySQL 8.0，驱动 asyncmy |
| 迁移 | Alembic | 模型变更后生成 revision |
| HTTP 客户端 | httpx | 调用上游供应商 |
| 向量库 | ChromaDB | 本地持久化，接口保留可切服务化 |
| Embedding | DeepSeek Embedding API | 通过 httpx 直接调用 |
| 配置 | pydantic-settings | 环境变量 + `.env` |
| 工具链 | uv + ruff + pyright + pytest | Python >=3.10 |

## 项目结构

```
llm-proxy/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI 入口，注册 router / 中间件
│   ├── config.py               # pydantic-settings 配置
│   ├── db/
│   │   ├── models.py           # SQLAlchemy ORM 模型
│   │   └── session.py          # async engine + sessionmaker
│   ├── middleware/
│   │   ├── auth.py             # x-api-key 鉴权
│   │   └── rate_limit.py       # 固定窗口限流
│   ├── adapters/
│   │   ├── schemas.py          # Internal 统一格式
│   │   ├── anthropic_in.py     # Anthropic → Internal
│   │   └── anthropic_out.py    # Internal → Anthropic（JSON / SSE）
│   ├── providers/
│   │   ├── base.py             # Provider 抽象接口
│   │   └── deepseek.py         # DeepSeek Anthropic 端点实现
│   ├── services/
│   │   ├── fallback.py         # 多 provider 重试与 fallback
│   │   ├── budget.py           # 预算检查与扣费
│   │   ├── memory.py           # Memory 检索/注入/提取
│   │   └── proxy.py            # 请求生命周期编排
│   ├── admin/                  # Admin CRUD 端点
│   └── router.py               # /v1/messages 路由注册
├── tests/                      # pytest 测试
├── alembic/                    # 数据库迁移
├── docs/
│   ├── superpowers/specs/      # 设计文档
│   └── plans/                  # 阶段计划
├── pyproject.toml
└── AGENTS.md
```

## Multi-Agent 使用约定

### 授权触发

当用户说以下任一表达时，视为授权按需使用多 agent / 子任务委派：

- “按 AGENTS.md 做”
- “按多 agent 策略做”
- “需要时并行”
- “你自己调度”
- “delegate / parallel / multi-agent”

未出现时，默认由主 agent 自行完成。

### 通用角色

| 角色 | 职责 | 适用场景 |
|---|---|---|
| Explore | 只读探索、跨文件搜索、调用链梳理 | “找到所有调用 budget 的地方” |
| Plan | 方案设计、模块边界、重构路径 | “设计 Memory 子系统的接口” |
| Worker | 实现具体功能、修 bug、补测试 | “实现 providers/deepseek.py” |
| Review | 检查改动风险、测试缺口、回归问题 | “review fallback 改动” |

### 并行策略

- 多个互不依赖的探索问题 → 并行 Explore。
- 多个写入范围互不重叠的实现任务 → 并行 Worker。
- 同一模块内的“设计 → 实现 → 验证”按顺序推进。
- 主 agent 负责任务拆分、结果整合、改动复核和最终交付。
- 子代理不得回退用户或其他 agent 的改动；修改代码时须声明负责的文件范围。

### 使用限制

- 小任务不拆多 agent。
- 不让多个 Worker 同时修改同一文件或强耦合模块。
- 关键路径不外包给子代理。

## 开发前检查清单

- [ ] 已读 `AGENTS.md` 和 `docs/plans/2026-06-25-llm-proxy-phase-1.md`
- [ ] 已读 `docs/superpowers/specs/2026-06-24-llm-proxy-design.md`
- [ ] 确认目标模块与其他模块的依赖关系
- [ ] 确认不违反「不做什么」列表

## 核心设计原则

- **格式网关**：Anthropic API ↔ Internal ↔ 供应商 Anthropic API，关键字段不丢失。
- **Provider 可扩展**：通过 `BaseProvider` 接口接入新供应商，不修改核心编排。
- **Memory 内聚**：嵌入存储、LLM 提取、检索注入集中在 `services/memory.py`。
- **不做提前抽象**：一个模块一个职责，不为“未来可能”引入无意义抽象。
- **删代码 > 加代码**：没有显式要求的验证/错误处理/fallback 不要加。

## 第一阶段范围

1. `pyproject.toml` + `app/config.py` 项目初始化。
2. SQLAlchemy 模型 + Alembic 迁移：8 张表。
3. Auth（x-api-key SHA-256）+ 固定窗口限流。
4. Anthropic ↔ Internal 适配器（JSON / SSE）。
5. DeepSeek provider 非流 + 流式实现。
6. Fallback：最多 3 次指数退避，5xx/超时/429 重试，4xx 切 provider，耗尽返回 502。
7. Budget：请求前检查 402，请求后按实际用量扣费。
8. Memory：检索注入 + 响应后异步提取写入。
9. Proxy 编排 + `/v1/messages` 主路由。
10. Admin CRUD（users/keys/providers/models/budgets/logs）。
11. 集成测试 + lint/format/type check + 本地冒烟。

Token 预检和 Tool use 透传留到第二阶段。

## 编码约定

- Python >=3.10，工具链 uv + ruff + pyright + pytest。
- 代码注释用中文。
- Commit 消息不加 `Co-Authored-By` 或类似 trailer。
- 所有函数参数和返回值加显式类型标注。
- SQLAlchemy 2.0 async 风格：`select(Model)`、`await session.execute(...)`、`await session.commit()`。
- 不写赘余注释（解释 WHAT），只在 WHY 不显而易见时注释。
- 不做提前抽象，一个函数一个职责。

## 常用命令

```bash
uv sync --all-groups                # 安装依赖
uv run ruff check . --fix           # Lint + 自动修复
uv run ruff format .                # 格式化
uv run pyright                      # 类型检查
uv run python -m pytest             # 运行测试
uv run alembic upgrade head         # 应用数据库迁移
uv run uvicorn app.main:app --reload # 启动开发服务器
```

## 不做什么

- 不实现生产部署配置（Docker、K8s）和日志中间件。
- 不引入 Redis，限流用内存固定窗口。
- 不做 Token 预检（第二阶段）。
- 不做 Tool use 透传校验（第二阶段）。
- 不做 Memory 图检索、记忆合并、TTL 清理。
- Admin 不做 Web UI 和复杂分页。
