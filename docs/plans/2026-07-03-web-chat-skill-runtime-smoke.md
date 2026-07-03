# Web Chat Skill Runtime Smoke Notes

## Services

Start the local MySQL and Qdrant services from the shared dev-services checkout:

```bash
cd ~/dev-services
docker compose up -d mysql qdrant
```

Return to this project worktree:

```bash
cd /Users/fu/Coding/tiny-llm-proxy-local/.worktrees/codex-web-chat-skill-runtime
```

Make sure the database named by `DATABASE_URL` exists. The default is:

```text
mysql+asyncmy://root@localhost:3306/llm_proxy
```

Configure model access before the app starts, for example in `.env`:

```bash
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_API_KEY=...
DEFAULT_MODEL=deepseek-chat

SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_API_KEY=...
EMBEDDING_MODEL=BAAI/bge-m3
RERANK_MODEL=BAAI/bge-reranker-v2-m3
```

Chat completion and skill routing use the Anthropic Messages-compatible endpoint
above. mem0 embedding uses SiliconFlow through its OpenAI-compatible embedding
endpoint. `RERANK_MODEL` is recorded for the retrieval roadmap, but mem0 rerank is
not enabled in this MVP because the installed mem0 OSS package does not expose a
SiliconFlow reranker provider.

## Migrate and Seed

Apply the schema migration:

```bash
uv run alembic upgrade head
```

Seed the default workspace, local user, and demo `contract-reviewer` skill:

```bash
uv run python -m app.seed
```

The seed helper is idempotent, so running it again should not create duplicate
skills, versions, or installs.

## Start the App

```bash
uv run uvicorn app.main:app --reload
```

Open the web chat page:

```text
http://127.0.0.1:8000/
```

## Browser Smoke

Send this message from the page:

```text
帮我审查这份合同有没有风险：付款后不可退款，违约责任由乙方单独承担。
```

Expected behavior:

- The assistant response streams into the chat page.
- Metadata shows the skill routing reason.
- Metadata shows the retrieved memory count.
- The selected skill is the seeded `contract-reviewer` demo skill.
