# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.3.0] - 2026-04-20

### Added
- **Inspector 实时监控面板**: LLM / Tool / Message / Cron / Error 事件流，垂直时间线 UI
- Inspector JSONL 审计日志持久化 (`~/.xjd-agent/inspector.jsonl`)
- Inspector 历史事件 API (`GET /api/admin/inspector/events?limit=100&type=tool_call`)
- Gateway Inspector 事件桥接: 消息收发、Cron 执行、错误事件自动推送
- 企业微信智能机器人 WebSocket 长连接模式 (aibot adapter)
- Inspector UI: 统计卡片、彩色 Badge、搜索过滤、自动滚动、展开/折叠详情

### Fixed
- Inspector 历史 API 响应解析: 前端正确解析 `{events: [...]}` 结构
- Inspector 历史 API `limit` 参数校验: 非数字输入不再抛异常
- JSONL 写入并发安全: 添加 `asyncio.Lock` 保护
- Gateway Inspector 异常日志: `_emit_inspector` 失败时记录 debug 日志

## [0.2.0] - 2026-04-19

### Security
- WebSocket: origin validation, 1MB message size limit, per-IP connection limit (10)
- Terminal exec requires auth when secret_key configured
- Default admin password written to chmod 600 file instead of logs
- Password hashing upgraded to PBKDF2-SHA256 600k iterations (OWASP 2023), backward compatible
- JWT token expiry reduced from 24h to 2h
- API Key hashing upgraded from SHA256 to HMAC-SHA256 with server secret
- CORS headers with explicit origin allowlist
- CSRF validation checks header value, not just presence
- File path endpoints reject symlinks to prevent traversal
- Memory API: user_id format validation (injection prevention)
- PII redaction filter on logging pipeline (phone, ID card, email, API key auto-masked)
- Memory/workspace API rate limited to 100 req/min
- Dependency `aiosqlite` upper bound pinned (<1)

### Added
- `GET /api/auth/status` — public endpoint for frontend auth state detection
- First-user setup flow: register without admin token when no users exist
- Frontend auth-aware UI: login/setup dialogs, auto auth headers on all fetch calls
- Session isolation: `run_turn(session_messages=...)` for concurrent Gateway sessions
- Learning loop auto-refinement: proven skill marking, automatic optimize on degradation
- `request_user_approval` tool: interactive approval gate for pipeline workflows (CLI + Gateway)
- E-commerce image pipeline skill: research → confirm → generate with self-evolution
- Pipeline-level learning: extract successful queries, user preferences, generation params into skill cache
- Pipeline cache injection (Tier 3): proven patterns auto-injected to skip redundant research
- Pipeline skills auto-bump tool rounds to 20 (from default 10)
- `get_skills_prompt(max_inject_tokens=2000)` token budget control
- Circuit breaker for model providers (3 consecutive failures → 60s cooldown)
- Periodic cleanup task for session locks, rate buckets, expired sessions
- MCP connection `close()` method with pending request timeout (60s)

### Fixed
- Memory leak: session_locks dict now cleaned up every 5 minutes
- Memory leak: rate_buckets cleanup threshold lowered from 5000 to 1000
- WebSocket send timeout (5s) prevents slow clients from blocking event loop
- Adapter send wrapped in 30s timeout for crash isolation
- Provider response validation: malformed tool_calls set to None
- Graceful shutdown: WebSocket background tasks drained on stop
- Gateway adapter start wrapped in 30s timeout
- Learning loop callback wrapped in 10s timeout
- Memory feedback recording wrapped in 5s timeout
- TTS pipeline cleanup on server stop
- Terminal command timeout reduced (60→30s exec, 10→5s proc.wait)
- Tool stats dict eviction when exceeding 10k entries
- Skill cache staleness check (300s auto-reload)
- Inspector subscription cleanup on WebSocket disconnect

## [0.1.0] - 2026-04-16

### Added

**Core Engine**
- Agent Engine with 25-round tool calling loop
- Smart model router with cheap/strong classification and auto-failover
- Credential rotation with exponential backoff (429 → rate-limited, 401 → expired)
- Execution sandbox with Docker/subprocess isolation
- Multi-agent orchestration with sub-agent delegation
- Context window management with sliding window strategy
- Multi-profile management with independent config/memory/skills per profile
- Auto-updater with version checking

**AI Providers (14)**
- OpenAI, Anthropic, Google Gemini, DeepSeek, SiliconFlow, Groq, Together
- Zhipu, Doubao, Kimi, Qwen, Bedrock, Mistral, OpenRouter
- Ollama local model support (http://localhost:11434)

**Tools (40+)**
- Built-in: terminal, file read/write/edit, directory listing
- Extended: web search, web fetch, code execution, grep, git, download, DNS, env, text transform
- Computer Use: mouse/keyboard control, window management, screen OCR
- Browser: Playwright-based web automation
- Vision: image analysis via GPT-4o
- Patch: unified diff application
- Skill audit: security scanning of skill YAML
- Canvas: create/update interactive UI artifacts
- Terminal management: list/switch backends
- Toolset composition: named tool groups with apply/reset

**Messaging Gateway (20+ platforms)**
- Telegram, Discord, Slack, WeChat, DingTalk, Feishu, WhatsApp, LINE, Matrix
- Twitter, Reddit, Signal, Email, Teams, SMS, Google Chat, IRC, iMessage, Facebook, Web

**Skills System**
- Skill manager with YAML-based skill definitions
- Learning loop: evaluate → extract skills → optimize → persist
- Skill optimizer with GEPA iteration
- Skill evaluator with rule-based + LLM scoring
- Skill composer for pipeline orchestration
- Skill marketplace with remote index, search, install, publish
- Skill community export (Markdown format)
- Procedural memory bridge
- 70 preset skill templates across 7 categories

**Memory System**
- Pluggable memory providers (built-in SQLite, extensible)
- Semantic vector search with OpenAI/SentenceTransformers embeddings
- Memory types: fact, preference, episode, skill, context
- Importance-based retention and decay

**Plugin System**
- Hot-loadable plugins with lifecycle hooks
- MCP Client: connect to external MCP tool servers
- MCP Server: expose tools to VS Code/Cursor via JSON-RPC 2.0 (stdio + SSE dual transport)
- 3 built-in example plugins: GitHub, Weather, Knowledge Base
- Home Assistant plugin: smart home device control via REST API

**Terminal Backends (6)**
- Local subprocess, SSH (asyncssh), Docker exec, Tmux, Daytona, Singularity/Apptainer

**Voice & Media**
- Voice pipeline: Whisper STT (local + API) + Edge TTS / ElevenLabs / OpenAI TTS
- Wake word detection: keyword matching + Porcupine + OpenWakeWord engine support
- Media processor: DALL-E / Stable Diffusion image generation, image analysis, OCR, video processing

**Canvas / A2UI**
- Interactive UI generation: HTML, Markdown, Mermaid, Chart.js, React
- Real-time update via WebSocket push
- Canvas renderer with auto-detection

**Production Infrastructure**
- Authentication: JWT, API Key, OAuth2, RBAC with 4 roles
- Resilience: retry with exponential backoff, circuit breaker, token bucket rate limiting, bulkhead isolation, fallback decorators
- Monitoring: Prometheus metrics, alert rules, distributed tracing
- Redis integration: message queues, session cache, rate limiting, pub/sub
- Cron scheduler with natural language parsing
- PII redactor: phone, ID card, email, bank card, API key, password detection and masking
- Persistent audit logger: JSONL per-day files with SHA-256 hash chain integrity verification
- Context reference resolver: @file, @dir, @url, @symbol, @memory inline expansion
- Agent identity templates: 4 built-in personas (assistant, coder, researcher, ops) + custom YAML
- Cost tracker: per-provider/model usage statistics, budget alerts, CSV export
- Event hooks system: before/after lifecycle hooks with priority ordering
- Webhook inbound: external HTTP POST triggers with HMAC-SHA256 signature verification
- Heartbeat health monitoring: periodic checks, consecutive failure alerts, proactive notifications
- Wake word detection: text keyword matching + Porcupine/OpenWakeWord audio detection
- RAG pipeline: document loading → chunking (fixed/paragraph/sentence) → embedding → vector retrieval → context injection
- Home Assistant plugin: device control, scene activation, automation triggers via REST API
- Anti-detection browser: stealth mode with webdriver flag hiding, fingerprint spoofing, init script injection

**RL Training**
- Reward model with multi-dimensional scoring
- Experience replay buffer
- Policy optimizer
- Curriculum scheduler
- A/B experiment framework

**Interfaces**
- CLI: interactive chat with slash command autocomplete, setup wizard, model/config/plugin/skill/profile/identity management, doctor, MCP server mode
- Web: HTTP API + WebSocket streaming chat with file upload
- Python SDK: HTTP and embedded modes
- Docker: multi-stage build with Compose (agent + web + redis)

**Testing**
- 434 tests covering all modules
- pytest + pytest-asyncio + pytest-xdist
