# 第四阶段：模拟多轮聊天与流式显示

本文介绍第四阶段的 mock 固定模板流程，不需要真实 API Key。
第五阶段已新增 DeepSeek 适配，配置和验收见 [DeepSeek 说明](deepseek.md)。
有历史、有参考资料和逐段输出，均不代表具备真实语义理解能力。

## 启动与演示

保留现有 `.venv` 和 `.env`，在项目根目录执行：

```bash
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
docker compose up -d --wait --wait-timeout 120
python -m alembic upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

本机已完成 `0003_streaming_chat` 迁移。新机器还需按 README 配好数据库、JWT 密钥，
并执行 `python -m scripts.import_demo_guides`。启动后打开 <http://127.0.0.1:8000/>：
登录 → 创建/选择会话 → 发送“校园卡补办需要什么材料？” → 再问“还需要什么材料？” → 刷新历史。
页面可以注册、停止生成、切换正常/失败/超时场景，以及删除空闲会话。

登录状态保留在当前标签页的 `sessionStorage`。刷新页面后重新读取数据库历史，
关闭标签页后需要重新登录；数据库记录不会因此消失。令牌失效时需要重新登录。
历史先展示最后一页，可点击“查看更早的消息”。用户输入和模型文本均作为纯文本显示。

## HTTP 与 SSE 协议

在 README 登录步骤取得当前终端的 `$TOKEN` 和 `$CONVERSATION_ID` 后，可执行：

```bash
curl -N -sS "http://127.0.0.1:8000/conversations/$CONVERSATION_ID/chat" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"content":"校园卡补办需要什么材料？","mock_scenario":"normal"}'
```

`content` 为 1～4000 字符，不能全是空白。`mock_scenario` 可选，为 `normal`、`failure`、
`timeout`；不能传角色、用户 ID 等字段。未登录 401，其他用户/不存在的会话 404，
同一会话正在生成 409，参数错误 422，真实模型配置缺失时在开始 SSE 前返回 503。
这些错误发生在开始 SSE 之前，不会创建不应存在的消息。

正常响应类型为 `text/event-stream; charset=utf-8`，带 `Cache-Control: no-cache, no-store,
no-transform` 和 `X-Accel-Buffering: no`。每个事件由空行结束，`data` 为 JSON：

| 事件 | 主要字段及含义 |
| --- | --- |
| `meta` | `conversation_id`、`user_message_id`、`assistant_message_id`、`mode: mock`、`model: mock-campus-v1`、`simulated: true`、`context_rounds`、`references`、`retrieval_source` |
| `delta` | `text`，新增文本，客户端按顺序追加 |
| `done` | 助手消息 ID、`status: completed`；只有数据库成功提交之后才发送 |
| `error` | 安全的 `code` 和 `message`，例如 `timeout`、`generation_failed`、`persistence_failed`，不包含供应商内部异常或密钥 |

正常为 `meta → delta… → done`，生成失败为 `meta → delta… → error`。
SSE 开始后 HTTP 已是 200，因此以 `error` 事件表达生成失败，不能再改 HTTP 状态码。
等待供应商分段时每 5 秒可发送 `: heartbeat` 注释。断开时无法继续向该客户端发送事件，
后端会取消生成并尝试保存 `cancelled` 状态。

页面使用 `fetch` 携带 Authorization 发送 POST，不使用只能方便发起 GET 的 `EventSource`。
`TextDecoder` 跨网络块恢复 UTF-8，独立的 `SSEParser` 缓冲不完整行与事件，兼容 CRLF 跨块、
多事件同块、多行 `data` 和注释。只有收到 `done` 才显示正常完成，提前 EOF 会提示刷新历史。

## 指南与回答依据

复用指南表、响应结构和关键词筛选，按当前问题中的字面词匹配标题，最多取 3 条。
这是有界规则检索，不是向量检索、语义理解或真实模型判断。
只有“还需要什么材料？”等明确简短追问，才尝试用上一轮已完成问题重新检索；
无关的新问题不会自动沿用旧资料。

实际匹配指南的完整材料、流程和来源传入独立模型接口；`meta.references` 和数据库历史
提供候选指南 ID、标题、来源说明及 `is_demo`；完成事件与数据库仅保留实际引用的来源。
无匹配也会调用当前模型：真实模型可做一般分析，仅对缺依据的具体校内规定说明无法确认。
mock 仍只演示模板，不代表真实语义理解。详见 [回答策略说明](answer-policy.md)。
内置指南均为虚构演示数据，页面和回复都会标注，不能当成真实校园政策。

## PostgreSQL 状态与并发恢复

先在短事务内校验归属、检查占用、读取上下文、保存用户消息和助手 `generating` 记录，
提交并归还连接后才开始 SSE。完整历史只从 PostgreSQL 读取。
生成结束再开启短事务，保存部分/完整文本、参考资料与最终状态并更新会话时间。
失败或取消的部分回复保留在历史中便于查看，但不会当成正常问答上下文。

| 状态 | 含义 |
| --- | --- |
| `generating` | 已创建生成记录，等待或正在输出 |
| `completed` | 正常结束并成功保存；用户消息也使用此状态 |
| `failed` | 模型失败或超时，可能保存部分文本 |
| `cancelled` | 客户端停止/断开，或旧任务占用已过期 |

同一会话只允许一个生成：父会话行锁负责短事务串行化，部分唯一索引防止两个
`generating` 记录并存；生成期限为模型总超时再加 10 秒的清理余量。
占用不依赖 Redis，Redis 故障时仍能正确限制并发。

生成期间，其他聊天、原始消息写入和删除均返回 409，历史读取仍可进行。
正常完成、失败、超时和断开都会结束占用并关闭模型生成器。
若进程被强制终止，下一次读取消息或写入操作会将过期生成标为 `cancelled`。
页面对仍在生成的历史会定时刷新；迟到的旧任务不能覆盖已结束状态或新任务结果。

强杀进程来不及保存部分增量时，仍保留已提交的问题和生成记录，但部分文本可能为空。
数据库临时不可用时不能承诺即时完成最终保存，此时不会发送 `done`，占用可在数据库恢复
且期限届满后恢复。关闭服务器不能用删除数据卷来代替。

## Redis 短期上下文

键为 `<CHAT_CONTEXT_PREFIX>:<用户UUID>:<会话UUID>`。默认保留最近 6 轮完整成功问答，
总文本最多 12000 字符，TTL 600 秒。超出字符预算时裁剪上下文文本，数据库原文不变。
`/messages` 单独保存的未配对用户消息，及失败/取消的问答，不进入已完成轮次。

每次使用缓存前先校验 PostgreSQL 最新完成消息版本，以及用户、会话和长度配置。
缓存缺失、过期、损坏或 Redis 读写失败时，从 PostgreSQL 中的成功问答重建。
缓存写入与会话删除通过父行锁协调，提交失败不能让缓存充当成功记录。

删除空闲会话时同步删除它的明确缓存键。若 Redis 当时不可用，只能尽力清理；
残留缓存按 TTL 到期，且数据库归属/存在性校验会先返回 404，不会使用已删除会话的缓存。
不会清空整个 Redis，也不会扫描删除其他业务键。

## 配置

修改 `.env` 后重启应用，新配置均可参考 `.env.example`：

| 配置 | 默认值 / 用途 |
| --- | --- |
| `MODEL_PROVIDER` | 默认 `mock`；第五阶段可切换 `deepseek` |
| `MODEL_NAME` | DeepSeek 默认 `deepseek-flash`；mock 固定标记为 `mock-campus-v1` |
| `MODEL_API_KEY` | mock 可留空；DeepSeek 必填，使用 SecretStr，不响应或记录 |
| `MODEL_BASE_URL` | 默认 `https://api.deepseek.com`，mock 不使用 |
| `MODEL_TIMEOUT_SECONDS` | 30，允许 0.1～120 秒，生成总时间限制 |
| `MOCK_CHUNK_CHARS` | 32，允许 1～256，控制每段字符数 |
| `MOCK_DELAY_SECONDS` | 0.04，允许 0～2，控制段间等待 |
| `MOCK_FAILURE_AFTER_CHUNKS` | 2，失败场景在指定分段数后抛出模拟异常 |
| `CHAT_CONTEXT_PREFIX` | `campus-assistant:context:v1` |
| `CHAT_CONTEXT_TTL` | 600 秒，允许 10～86400 |
| `CHAT_CONTEXT_ROUNDS` | 6 轮，允许 1～20 |
| `CHAT_CONTEXT_MAX_CHARS` | 12000 字符，允许 100～64000 |

想快速演示超时，可临时设置 `MODEL_TIMEOUT_SECONDS=2`，重启后选择页面的超时场景。
正常回复也受相同总超时约束，分段越慢越可能超时。

## 自动验收

```bash
python -m pytest -q tests
python -m scripts.verify_chat_http
```

所有记录在随机命名的独立数据库；结束后清理专用测试库与明确缓存键。
真实 HTTP 验收包含断开取消、无长事务、Redis 真实连接拒绝和重启恢复。
浏览器验收另加 `--browser`，验证增量显示、多轮、停止、刷新、引用以及解析器分块。

其他机器可用 `python -m playwright install --with-deps chromium --only-shell` 安装自动化浏览器。
本机用到的浏览器与补充库都在 `/tmp`，可这样复测：

```bash
LD_LIBRARY_PATH=/tmp/campus-assistant-browser-libs/root/usr/lib/x86_64-linux-gnu \
PLAYWRIGHT_BROWSERS_PATH=/tmp/campus-assistant-browsers \
python -m scripts.verify_chat_http --browser
```

这些 `/tmp` 文件可能随系统清理而消失，仅影响自动化浏览器测试，不影响用户在自己的
浏览器中打开聊天页面。迁移回滚若遇到空/长助手回复不能满足旧约束，会拒绝回滚而非删除历史。

## 第五阶段真实模型

已经选用 DeepSeek，异步适配器位于 `app/deepseek.py`，沿用本文的状态、会话归属、
上下文和取消机制。请按 [第五阶段说明](deepseek.md) 配置本机密钥，并区分模拟测试与真实 API 验收。
正式使用仍需经学校确认的指南及来源；现有 8 条虚构示例不能替代正式资料。
