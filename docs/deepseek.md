# 第五阶段：DeepSeek 真实模型适配

代码支持 `mock` 和 `deepseek`。默认仍是 mock，不会产生模型费用。
**模拟上游测试与真实 API 验收分别记录：154 项模拟/回归测试通过；2026-10-20 的 3 次真实短请求全部通过。**
本机已配置 DeepSeek，重启应用即可生效；密钥未写入报告或 Git。
后续文档接入阶段已扩展至 204 项离线回归，并增加用户资料原文和引用校验；未追加真实模型调用。
新资料的限制与离线验收见 [资料接入说明](references.md)，不能用此前三次指南验收代替新资料的实测。

## 本机配置

在项目根目录已有的 `.env` 中增加或修改以下字段，保留原有数据库密码和 JWT 密钥。
不要用 `.env.example` 覆盖已有 `.env`。

```dotenv
MODEL_PROVIDER=deepseek
MODEL_BASE_URL=https://api.deepseek.com
MODEL_NAME=deepseek-flash
MODEL_API_KEY=
MODEL_TIMEOUT_SECONDS=30
MODEL_MAX_TOKENS=1024
```

`MODEL_API_KEY` 的等号后由你在本机编辑器中填写真实密钥，不要发到聊天或前端。
该字段使用 `SecretStr`，`.env` 已被 `.gitignore` 忽略。服务端、前端响应和应用日志都不输出密钥，
错误响应也不会转发供应商原始错误正文。也可以通过操作系统环境变量配置，环境变量优先于 `.env`。

| 配置 | 含义 |
| --- | --- |
| `MODEL_PROVIDER` | `mock` 为固定模板，`deepseek` 才发送真实请求；不会失败后自动改用 mock |
| `MODEL_BASE_URL` | 默认 `https://api.deepseek.com`，可带 `/v1` 或结尾 `/`，自动拼接 `/chat/completions` |
| `MODEL_NAME` | DeepSeek 模型 ID，默认 `deepseek-flash`；mock 始终显示 `mock-campus-v1` |
| `MODEL_API_KEY` | 服务端专用密钥；DeepSeek 模式缺失时发送聊天返回 HTTP 503，且不创建消息 |
| `MODEL_TIMEOUT_SECONDS` | 总超时和上游读写超时，默认 30 秒，允许 0.1～120 秒 |
| `MODEL_MAX_TOKENS` | 发送给上游的 `max_tokens`，默认 1024，本站允许 1～8192 |

请求固定 `stream=true`、`thinking={"type":"disabled"}`。**自动重试次数为 0**，不自动重连，
也不跟随 HTTP 重定向，避免重复生成和计费。单条助手回复仍限制 16000 字符，超出会标记失败，
不能通过提高 token 上限绕过数据库长度限制。Base URL 不接受账号、密码、查询参数和片段；
只允许 HTTPS，另允许本机 HTTP 供无费用的模拟验收脚本使用。

## 初始化与启动

```bash
cd /home/sun/projects/campus-assistant
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
docker compose up -d --wait
python -m alembic upgrade head
python -m alembic check
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

继续使用原 `.venv`、数据库和数据卷。新迁移 `0004_message_model` 只增加两列：
`model_provider`、`model_name`。旧助手记录标注为 mock，内容、时间、状态、指南不变。
不要删除卷或重建数据库。HTTPX 现在属于运行依赖，已有虚拟环境若已安装它，无需重建环境。

每次修改 `.env` 后停止并重启 Uvicorn，`--reload` 不保证监听 `.env` 变化。
关闭终端里的应用按 `Ctrl+C`；停止依赖服务执行 `docker compose stop`，保留数据卷。
改回 `MODEL_PROVIDER=mock` 并重启即可继续免费演示。

## 聊天链路与边界

浏览器访问 <http://127.0.0.1:8000/>，先登录、创建或选择自己的会话，然后发送问题。
页面从 `/chat/config` 读取当前模式及模型，不展示 API Key。`ready=true` 仅表示必要配置存在，
不代表密钥有效、余额充足或真实连接已经验证。每条历史助手消息保留当时的供应商和模型，
切换全局配置不会把旧 mock 回复显示成 DeepSeek 回复。

请求复用第四阶段的归属检查、指南检索和上下文：

1. 短事务保存用户消息和 `generating` 助手记录，再归还数据库连接。
2. 系统消息只包含固定约束。实际检索的指南以独立 `user` 参考数据消息发送，包含 ID、
   标题、材料、流程、部门、来源、更新时间和演示标识；资料中的指令不能改变系统约束。
3. 之后依次发送最近已完成的用户/助手问答，最后发送本次用户问题，当前问题只出现一次。
4. 接收上游分段，转换为现有 `meta / delta / done / error`。`meta` 新增 `model`，
   `mode=deepseek`、`simulated=false`，参考指南仅来自本轮实际检索结果。
5. 只有有效文本、`finish_reason=stop` 和 `[DONE]` 全部满足才发 `done` 并保存 `completed`。
   EOF、中断、截断、过滤及异常结束发安全 `error`，保留可见部分为 `failed`。
6. 点击“停止生成”或关闭客户端时取消任务、关闭上游 HTTP 流、保存 `cancelled`。
   取消不会承诺供应商免收已经生成的 token 费用。

生成期间不保持数据库事务。失败和取消的问答不进入正常上下文；成功后仍使用 Redis 缓存
最近完整问答，过期或故障回退 PostgreSQL。单会话并发保护、过期任务恢复、删除清理缓存沿用原实现。
DeepSeek 模式隐藏 mock 故障选项，伪造非 normal 的 `mock_scenario` 请求会在 SSE 开始前返回 422。

检索仍是**字面关键词匹配**，并非向量检索。特定简短追问可以沿用上一轮问题重新检索。
一般评价、学习规划等允许模型使用知识和推理；没有匹配资料时照常生成，不返回固定拒答。
金额、资格、期限、审批等具体校内规定缺依据的部分须说明“无法确认”，并给出查询建议；
有资料也不要求整段复述。策略、引用与缓存兼容性见 [回答策略说明](answer-policy.md)。
当前 8 条指南全部为虚构演示数据，真实模型回答也必须注明。引用面板由服务端本轮检索元数据生成，
不会从模型文本抽取额外引用。模型正文仍可能出错，正式使用前需要核对官方资料。

## 可读错误提示

以下均为 SSE `error`，不是本站的 HTTP 401，不会清除本站登录令牌。

| 上游情况 | 事件 code | 含义 |
| --- | --- | --- |
| 401 / 403 | `upstream_authentication` | 检查本机 DeepSeek 密钥或权限 |
| 402 | `insufficient_balance` | 检查供应商账户余额 |
| 429 | `rate_limited` | 稍后手动重试 |
| 408 / 504、读写或总超时 | `timeout` | 供应商响应超时 |
| 500 / 503 等 | `upstream_service` | 供应商故障或繁忙 |
| 网络连接/传输故障 | `upstream_connection` | 检查网络后手动重试 |
| 400 / 404 / 422 / 重定向等 | `upstream_request` | 检查模型、地址和参数 |
| `finish_reason=length` | `output_truncated` | 达到输出上限，不是完整回复 |
| `finish_reason=content_filter` | `content_filtered` | 上游未提供完整回复 |
| 缺少结束标记、`aborted`、无效事件等 | `upstream_incomplete` | 内容不完整，不作为成功历史 |

## 免费自动化与真实验收的区别

```bash
python -m pytest -q tests
python -m scripts.verify_deepseek_http
# 本机已准备好 Chromium 与临时依赖时，可额外验收页面：
LD_LIBRARY_PATH=/tmp/campus-assistant-browser-libs/root/usr/lib/x86_64-linux-gnu \
PLAYWRIGHT_BROWSERS_PATH=/tmp/campus-assistant-browsers \
python -m scripts.verify_deepseek_http --browser
```

自动化使用 HTTPX MockTransport，HTTP/浏览器验收使用本机临时模拟上游，**不访问真实 DeepSeek，
不消耗真实额度**。测试环境强制置空真实模型密钥，再注入测试专用假密钥。
所有测试使用独立随机数据库和明确的 Redis 测试键；不清空开发库或整个 Redis。
HTTP 脚本还验证旧记录迁移、正常/多轮、截断、中断、超时、客户端断开后的上游关闭、
失败后的继续生成，以及错误场景没有自动重试。浏览器检查模式、模型、引用、刷新恢复和
上游鉴权失败时本站登录仍有效。

真实验收需先在本机 `.env` 填写 `MODEL_API_KEY`。本次已获得填写完成通知并执行了
**3 次短请求**，没有批量付费测试或自动重试，验收范围如下：

1. 简短连接请求，确认真实 SSE、模型标识及正常结束。
2. 询问“校园卡补办需要什么材料？”，确认匹配指南、演示标注、回答依据和持久化。
3. 在同一会话追问“办理流程呢？”，确认成功历史与资料一起传入、当前问题不重复。

任一次鉴权、余额、限流或网络异常都如实记录，不以 mock 测试结果代替真实验收。
当前结果请看 [README 验收记录](../README.md#9-本次实际验收记录)。

## 已执行的真实 API 验收

时间：2026-10-20 21:55（北京时间）。使用本机密钥、官方 Base URL 和 `deepseek-flash`，
复用应用的注册/登录、聊天、指南、Redis 上下文及 PostgreSQL 保存链路，不绕过生产适配器。
仅在临时验收进程将输出上限设为 256 tokens；本机 `.env` 原有 1024 上限及其他配置不变。

| 请求 | 结果 | 增量事件 | 总耗时 |
| --- | --- | --- | --- |
| 连接：只回复“连接成功” | 通过，实际返回“连接成功” | 2 | 1.140 秒 |
| 校园卡补办材料，两句话回答 | 通过，提及有效身份证件、学生证或学号，明确演示数据性质 | 43 | 0.929 秒 |
| 同一会话追问“办理流程呢？” | 通过，沿用校园卡补办指南，回答挂失、申请、领取流程并注明演示性质 | 65 | 0.790 秒 |

三次都是 HTTP 200，经过正常 `stop` / `[DONE]` 完整性检查后发送 `done`，
数据库记录均为 `completed`，正文与流式增量拼接一致。已完成上下文轮数依次为 0、1、2，
第三次使用 `last_completed_question` 检索来源，与材料问题引用同一条演示指南。
本次没有验证真实故障、真实限流或真实断开计费；这些情况仍以模拟上游测试结果为依据。
没有测量实际费用，报告中的 256 是每次输出上限而非实际消耗。

独立临时数据库、验收账号/会话及明确的 Redis 测试键已清理，开发数据保留。
完整脱敏结果：[deepseek-live-result.json](deepseek-live-result.json)，不含 API Key、JWT 或数据库密码。

`scripts.verify_deepseek_live` 是手动付费验收入口，不属于 pytest 或免费浏览器验收。
脚本预算硬限制为 3 次；报告已有请求记录时直接退出，不重复请求。本轮授权额度已用完，
后续不会自动追加付费调用。使用日常聊天页面不受验收脚本的三次限制。

## 官方协议

- [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)：端点、模型、thinking、流结束标记和 finish_reason。
- [DeepSeek 错误码](https://api-docs.deepseek.com/quick_start/error_codes/)：鉴权、余额、限流与服务错误。
