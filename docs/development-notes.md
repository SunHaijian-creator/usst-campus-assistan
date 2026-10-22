> 历史开发验收记录：此文保留原环境结果；当前项目入口和发布状态以根目录 README.md 及 docs/publication.md 为准。

# 校园办事 AI 助手

当前完成第三阶段：用户认证、个人会话及消息持久化；保留第二阶段的指南查询和详情缓存。
FastAPI 在本机的 `.venv` 中运行，PostgreSQL 17、Redis 7 由 Docker Compose 管理。
历史消息的唯一可靠来源是 PostgreSQL。本阶段不调用大模型，不生成助手回复，
也不把会话或消息保存到 Redis。指南详情仍使用原有的 5 分钟缓存。

**内置 8 条指南均为虚构的演示数据，不可作为实际办事依据。** 每条响应都有
`is_demo: true`，来源说明和办理部门也标注了演示性质。

## 1. 新增文件与用途

| 文件 | 用途 |
| --- | --- |
| `app/main.py` | FastAPI 入口、健康检查及关闭时的连接清理 |
| `app/config.py` | 使用 Pydantic Settings 读取环境变量与 `.env` |
| `app/database.py` | SQLAlchemy 2.x 连接池、模型基类及每次请求的独立会话 |
| `app/models.py` | 办事指南表的 ORM 映射 |
| `app/schemas.py` | Pydantic 响应结构和缓存内容校验 |
| `app/guides.py` | 指南列表、筛选、分页、详情和 404 |
| `app/security.py` | Argon2 密码哈希、JWT 签发/验证及当前用户依赖 |
| `app/auth.py` | 注册、OAuth2 表单登录和 `/users/me` |
| `app/conversations.py` | 校验归属、个人会话、消息历史和事务写入 |
| `app/cache.py` | Redis 客户端、300 秒详情缓存及故障回退 |
| `alembic.ini`、`migrations/env.py` | Alembic 配置，复用应用的数据库配置 |
| `migrations/versions/0001_create_guides.py` | 创建指南表的第一份迁移 |
| `migrations/versions/0002_auth_history.py` | 增量新增用户、会话、消息，不修改指南 |
| `migrations/script.py.mako` | 以后生成迁移文件使用的模板 |
| `scripts/import_demo_guides.py` | 可重复执行的演示数据导入脚本 |
| `scripts/generate_jwt_secret.py` | 为本机 `.env` 生成随机 JWT 密钥，已有密钥不覆盖 |
| `scripts/test_database.py` | 创建和清理唯一命名的临时测试数据库 |
| `scripts/verify_auth_http.py` | 真实 HTTP 全流程及应用重启后的历史验证 |
| `data/demo_guides.json` | 8 条明确标注为演示的指南 |
| `tests/test_guides.py` | 连接真实 PostgreSQL / Redis 的集成测试 |
| `tests/conftest.py` | 收集测试前隔离数据库，自动迁移并导入测试指南 |
| `tests/test_auth_history.py` | 认证、权限隔离、消息顺序、删除及事务回滚测试 |
| `requirements.txt` | 已固定版本的运行依赖及间接依赖 |
| `requirements-dev.txt` | 运行依赖加上测试依赖 |
| `compose.yaml` | 两个服务、健康检查、仅本机端口和持久化数据卷 |
| `.env.example` | 可以公开提交的配置模板，不含真实密码 |
| `.env` | 本机真实配置，被 `.gitignore` 忽略 |
| `.venv/` | 保留并继续使用的项目独立 Python 环境 |

`app/__init__.py`、`scripts/__init__.py` 将对应目录标记为 Python 包。
`.gitignore` 继续忽略 `.env`、`.venv/`、Python 缓存和 pytest 缓存。
本阶段没有进行 Git 提交，也没有覆盖本机 `.env` 中的数据库密码。
第三阶段只为本机 `.env` 补充了缺少的随机 JWT 密钥，没有在代码或示例中保存真实密钥。

## 2. 环境与配置

以下命令用于 Linux / WSL 的 Bash。先启动 Docker Desktop，并开启当前 WSL 发行版的
Docker 集成。在项目根目录执行：

```bash
cd /home/sun/projects/campus-assistant
source .venv/bin/activate
python -c "import sys; print(sys.executable)"
python -m pip install -r requirements-dev.txt
```

解释器路径应以本项目的 `.venv/bin/python` 结尾。本机已有可用的 `.venv`，无需重建。
只运行应用时，可以只安装 `requirements.txt`；执行本文的测试还需要开发依赖。

### 首次在其他机器创建环境

使用 Python 3.12 和 Docker Compose v2 或更高版本：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

如果 Ubuntu / Debian 提示 `ensurepip is not available`，先安装匹配的 venv 包，
然后重新执行创建命令。Python 3.12 的例子：

```bash
sudo apt-get update
sudo apt-get install python3.12-venv
```

如果已有 `.venv/bin/python`、pip 和依赖，只缺少 `activate`，可以保留依赖修复：

```bash
python3 -m venv --without-pip .venv
source .venv/bin/activate
python -m pip check
```

### 准备 `.env`

```bash
cp -n .env.example .env
```

该命令不会覆盖已有文件。第一阶段的数据库密码继续沿用；第三阶段还需要新增 JWT 密钥。
另一台机器首次复制后，需要把示例数据库密码改为自己的密码。

应用自动读取项目根目录下的 `.env`，**不需要执行 `source .env`**。
Pydantic Settings 会校验端口等配置；操作系统环境变量优先于 `.env`。
修改配置后需要重启应用。

| 配置项 | 用途 / 默认值 |
| --- | --- |
| `POSTGRES_HOST` | 应用连接的地址，默认 `127.0.0.1` |
| `POSTGRES_PORT` | 宿主机 PostgreSQL 端口，默认 `5432`；也用于 Compose 端口映射 |
| `POSTGRES_DB` | 数据库名，必须提供；沿用第一阶段值 |
| `POSTGRES_USER` | 用户名，必须提供；沿用第一阶段值 |
| `POSTGRES_PASSWORD` | 密码，必须提供；沿用第一阶段值 |
| `REDIS_HOST` | 默认 `127.0.0.1` |
| `REDIS_PORT` | 宿主机 Redis 端口，默认 `6379`；也用于 Compose 端口映射 |
| `REDIS_DB` | Redis 数据库编号，默认 `0` |
| `REDIS_PASSWORD` | 默认留空，当前 Compose 的 Redis 没有设置密码 |
| `REDIS_SOCKET_TIMEOUT` | 单次连接和读写超时，默认 `0.3` 秒，不自动重试 |
| `GUIDE_CACHE_PREFIX` | 默认 `campus-assistant:guides:v1`，用于隔离详情缓存键 |
| `JWT_SECRET_KEY` | 第三阶段必填，至少 32 字符，不提供固定默认值 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 访问令牌有效期，默认 30 分钟，允许 1～1440 |

`REDIS_PASSWORD` 只配置应用连接，不会给 Compose 中的 Redis 自动设置密码。
当前本地配置请留空；连接其他已有密码的 Redis 时才填写对应密码。

数据库 URL 由上述配置安全构建，Alembic 配置文件里不存密码。
不要提交 `.env` 或输出含密码的完整配置。PostgreSQL 账号和密码仅在空数据卷首次
初始化时生效，之后修改 `.env` 不会修改已有数据库账号。

### 配置 JWT 密钥

```bash
python -m scripts.generate_jwt_secret
```

本机已执行过，可以重复执行：已有密钥会保留，脚本不显示密钥，并将新生成的 `.env`
权限设为 `600`。不要将 `.env` 提交到 Git。没有密钥或密钥不足 32 字符时，应用会拒绝启动。
该脚本保留已有的其他配置。若原本填了过短的密钥，需要自行替换为足够长的随机密钥。

访问令牌使用固定的 HS256 算法，并验证签名、过期时间、签发方、接收方及访问令牌类型。
密钥保存在 `.env`，因此应用正常重启后原有令牌在有效期内仍可使用；更换密钥会使旧令牌失效。
令牌过期后重新登录即可。

## 3. 初始化：容器、迁移和演示数据

在已激活虚拟环境的项目根目录执行：

```bash
docker info
docker compose config --quiet
docker compose up -d --wait --wait-timeout 120
docker compose ps
```

两个服务应显示 `healthy`，端口分别为 `127.0.0.1:5432` 和 `127.0.0.1:6379`。
端口被占用时，可修改 `.env` 中对应的 `*_PORT`，重新执行启动命令，并重启应用。
容器内部端口不变。

创建或更新表结构：

```bash
python -m alembic upgrade head
python -m alembic current
```

当前版本应为 `0002_auth_history (head)`。迁移记录在 `alembic_version` 表中，
重复执行 `upgrade head` 不会重复建表。应用启动时不会自动建表，也不会自动导入数据。
以后修改模型时需要新建并检查迁移；已执行的迁移文件不要直接修改。

导入演示数据，并验证可以重复执行：

```bash
python -m scripts.import_demo_guides
python -m scripts.import_demo_guides
```

全新数据库首次应新增 8 条，第二次新增 0 条。若本机已完成初始化，两次均新增 0 条。
导入使用 `demo_key` 唯一约束和 PostgreSQL 冲突处理，只补充缺少的演示记录，
不覆盖已有记录、更新时间或其他正式数据。更改 JSON 后重新导入不会覆盖旧内容。
重复导入可能消耗数据库序列号，因此 ID 不连续是正常现象，不应固定假设某条记录的 ID。

演示事项包括校园卡补办、在读证明、教室借用、成绩单申请、图书借阅证补办、宿舍报修、
学生证补办、学生活动场地申请。

### 办事指南表

| 数据库 / API 字段 | 含义 |
| --- | --- |
| `id` | 自动生成的整数 ID |
| `title` | 事项名称 |
| `category` | 分类 |
| `department` | 办理部门 |
| `steps` | 有顺序的办理流程，存储为 JSONB 字符串数组 |
| `materials` | 所需材料，存储为 JSONB 字符串数组 |
| `source` | 来源说明；演示记录明确标注为虚构 |
| `updated_at` | 带时区的更新时间，初始化时由 PostgreSQL 生成 |
| `is_demo` | 是否为演示数据 |
| `demo_key` | 内部导入去重标识，唯一且可空，不在查询响应中返回 |

第三阶段新增三张表：`users` 保存 UUID、唯一用户名、密码哈希和创建时间；
`conversations` 保存 UUID、所属用户、标题及创建/更新时间；`messages` 保存递增整数 ID、
所属会话、角色、内容和创建时间。外键保证消息只能属于存在的会话，删除会话时在同一事务
内级联删除关联消息。用户名按去除首尾空格、转小写的形式保存，大小写变体不能重复注册。

## 4. 启动和使用接口

```bash
source .venv/bin/activate
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

当前终端保持运行。浏览器打开 <http://127.0.0.1:8000/docs> 可以直接填写参数并试用。
如果 8000 端口被占用，将 `--port` 改为 8001，并修改后面的访问地址。

`/health` 仍返回 `{"status":"ok"}`，只代表应用能响应，不代表数据库一定可用。

### 列表、筛选及分页

另开终端：

```bash
curl -sS 'http://127.0.0.1:8000/guides?page=1&page_size=3'

curl -sS --get 'http://127.0.0.1:8000/guides' \
  --data-urlencode 'keyword=校园卡' \
  --data-urlencode 'category=校园生活' \
  --data-urlencode 'page=1' \
  --data-urlencode 'page_size=10'
```

| 参数 | 规则 |
| --- | --- |
| `keyword` | 可选，最多 100 字符，搜索事项名称、办理部门、来源说明；忽略首尾空格 |
| `category` | 可选，最多 100 字符，按分类精确匹配；忽略首尾空格 |
| `page` | 从 1 开始，默认 1 |
| `page_size` | 1～100，默认 10 |

关键词与分类同时填写时取交集。关键词按字面包含匹配，`%`、`_` 不作为通配符。
空字符串视为不筛选。结果按 ID 升序稳定分页；超过最后一页返回空 `items`，状态码仍为 200。
响应使用 Pydantic 定义。无匹配记录时的响应示例：

```json
{
  "items": [],
  "total": 0,
  "page": 1,
  "page_size": 10
}
```

有匹配记录时，`items` 包含完整指南，`total` 为筛选后、分页前的总数。

### 详情、404 和参数错误

从列表的 `items` 中找一个实际 ID，将下面的 `1` 替换为该 ID：

```bash
curl -i http://127.0.0.1:8000/guides/1
```

详情包含事项名称、流程、材料、来源、更新时间和演示标记。
查询一个不存在的正整数 ID，应返回 HTTP 404 和 `{"detail":"办事指南不存在"}`。
ID 的范围是 1～2147483647（与 PostgreSQL 整数主键范围一致）。非整数、超出范围的 ID、
页码小于 1、每页大于 100 等参数错误返回 HTTP 422。

## 5. 注册、登录、创建会话和保存消息

### 先用 Swagger 文档操作

启动应用后打开 <http://127.0.0.1:8000/docs>：

1. 展开 `POST /auth/register`，点击 **Try it out**，填写用户名和密码并执行。例如：
   `{"username":"student_demo","password":"Campus-demo-2026"}`。首次返回 201，重复用户名返回 409。
2. 点击页面右上角 **Authorize**，输入刚注册的 `username`、`password`。
   `client_id`、`client_secret` 留空，点击授权按钮。Swagger 会调用 `/auth/token`，
   后续受保护请求自动携带访问令牌。
3. 执行 `GET /users/me`，确认返回自己的 ID、用户名和创建时间，不包含密码或哈希。
4. 执行 `POST /conversations`，请求体填 `{"title":"校园卡咨询"}`，记录返回的会话 UUID。
   填 `{}` 时使用默认标题“新会话”。
5. 执行 `POST /conversations/{conversation_id}/messages`，将 UUID 填入路径参数，
   请求体只填 `{"content":"我想了解校园卡补办"}`。返回 201，角色固定为 `user`。
6. 再保存一条消息，用 `GET /conversations/{conversation_id}/messages` 分页读取。
7. 用 `GET /conversations` 查看自己的会话列表，用 `GET /conversations/{conversation_id}` 查看详情。
8. 最后执行 `DELETE /conversations/{conversation_id}`，成功返回 204 和空响应体；
   再查询该会话或历史应返回 404。

用户名使用 3～32 位英文字母、数字或下划线；密码为 8～128 字符，密码中的空格不做裁剪。
示例账号仅用于本机操作演示。如果用户名已存在，直接登录或使用另一个用户名即可。

### 用终端走完整流程

保持应用运行，另开终端进入项目并激活虚拟环境。以下命令使用与上面相同的演示账号：

```bash
cd /home/sun/projects/campus-assistant
source .venv/bin/activate

# 1. 注册；只返回公开的用户信息。若之前已注册则返回 409，可继续登录。
curl -sS -X POST 'http://127.0.0.1:8000/auth/register' \
  -H 'Content-Type: application/json' \
  -d '{"username":"student_demo","password":"Campus-demo-2026"}'

# 2. 登录使用表单，不是 JSON；将令牌放入当前终端变量，不打印它。
TOKEN=$(curl -fsS 'http://127.0.0.1:8000/auth/token' \
  --data-urlencode 'username=student_demo' \
  --data-urlencode 'password=Campus-demo-2026' \
  | python -c 'import json, sys; print(json.load(sys.stdin)["access_token"])')

# 3. 确认当前用户。
curl -sS 'http://127.0.0.1:8000/users/me' -H "Authorization: Bearer $TOKEN"

# 4. 创建会话，将返回的 UUID 存入变量。
CONVERSATION_ID=$(curl -fsS 'http://127.0.0.1:8000/conversations' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"title":"校园卡咨询"}' \
  | python -c 'import json, sys; print(json.load(sys.stdin)["id"])')

# 5. 保存两条用户消息；不传 user_id、role 等字段。
curl -sS "http://127.0.0.1:8000/conversations/$CONVERSATION_ID/messages" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"content":"我想了解校园卡补办"}'
curl -sS "http://127.0.0.1:8000/conversations/$CONVERSATION_ID/messages" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"content":"请保存这条历史消息"}'

# 6. 查询会话列表、详情和第一页消息。
curl -sS 'http://127.0.0.1:8000/conversations?page=1&page_size=10' \
  -H "Authorization: Bearer $TOKEN"
curl -sS "http://127.0.0.1:8000/conversations/$CONVERSATION_ID" \
  -H "Authorization: Bearer $TOKEN"
curl -sS "http://127.0.0.1:8000/conversations/$CONVERSATION_ID/messages?page=1&page_size=1" \
  -H "Authorization: Bearer $TOKEN"

# 7. 删除该会话及其消息，应为 204；随后查询应为 404。
curl -i -X DELETE "http://127.0.0.1:8000/conversations/$CONVERSATION_ID" \
  -H "Authorization: Bearer $TOKEN"
curl -i "http://127.0.0.1:8000/conversations/$CONVERSATION_ID/messages" \
  -H "Authorization: Bearer $TOKEN"
unset TOKEN CONVERSATION_ID
```

令牌过期或在另一个终端操作时，需要重新登录并设置该终端的 `TOKEN`。
注册的用户和保存的消息都会真正写入本机开发数据库；下面的自动化验收则使用独立临时库。

### 接口规则与数据归属

| 接口 | 登录要求 | 成功状态 |
| --- | --- | --- |
| `POST /auth/register` | 不需要 | 201 |
| `POST /auth/token` | 不需要，使用用户名/密码表单 | 200 |
| `GET /users/me` | 需要 Bearer 令牌 | 200 |
| `POST /conversations` | 需要 | 201 |
| `GET /conversations` | 需要，只返回自己的会话 | 200 |
| `GET /conversations/{id}` | 需要，只能访问自己的会话 | 200 |
| `DELETE /conversations/{id}` | 需要，只能删除自己的会话及消息 | 204 |
| `POST /conversations/{id}/messages` | 需要，只能写入自己的会话 | 201 |
| `GET /conversations/{id}/messages` | 需要，只能读取自己的会话 | 200 |

- 密码由 `pwdlib` 的 Argon2 实现加盐哈希，数据库不存明文。响应不返回密码或哈希，
  校验错误也不会回显请求中的密码、密钥；日志不记录请求正文、令牌或签名密钥。
- 未登录、令牌无效或过期返回 401，并携带 `WWW-Authenticate: Bearer`。
  错误密码和不存在的用户名使用相同错误响应。
- 其他用户的会话和不存在的会话统一返回 404、`{"detail":"会话不存在"}`。
  合法 UUID 才参与查询，UUID 格式错误返回 422。
- 会话标题最多 100 字符，去掉首尾空格后不能为空。归属由服务端确定，客户端不能传 `user_id`。
- 消息只能提交 `content`，为 1～4000 字符，不能全是空白，保留非空内容的原有空格。
  额外传 `role`、`user_id`、`conversation_id` 等字段返回 422，不能冒充助手或其他用户。
- 会话和消息分页都支持 `page`（1～1000000，默认 1）、`page_size`（1～100，默认 20），
  返回 `items`、`total`、`page`、`page_size`，超过最后一页返回空数组。
- 会话按更新时间降序、UUID 降序排序。消息按递增 ID 升序排序，即使时间相同也顺序稳定。
  每次保存消息都会在同一事务内更新会话时间；读取历史不会改变会话时间。
- 写消息与删除会话都先锁定当前用户所属的会话。数据库外键级联删除和事务回滚共同保证
  删除、写入失败或并发执行时不会留下孤立消息；数据库操作失败返回不包含 SQL 的 503。

## 6. 指南缓存与故障回退

详情缓存键形如 `campus-assistant:guides:v1:<指南ID>`，有效期固定 **300 秒**：

1. 命中有效缓存时直接返回，不查询 PostgreSQL。
2. 未命中、过期或缓存内容损坏时，查询 PostgreSQL，并重新写入 300 秒缓存。
3. Redis 连接或读写失败时，记录简短警告，继续返回 PostgreSQL 查询结果。
4. 不存在的指南返回 404，不创建缓存。列表查询直接使用 PostgreSQL。

Redis 不可用也允许应用启动。回退依赖 PostgreSQL 正常运行，无法代替 PostgreSQL 故障处理。
当前未提供修改指南的接口；若直接修改数据库，已缓存的详情最多可能延迟 5 分钟更新。

## 7. 实际验证与自动化测试

本机开发库先按第 3 节完成迁移。在项目根目录执行：

```bash
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip check
python -m alembic check
python -m pytest -q tests
python -m scripts.verify_auth_http
```

`alembic check` 检查数据库表结构与 ORM 模型是否一致，不修改数据。
所有测试现已改为自动创建 `campus_assistant_test_<随机编号>` 临时数据库：
收集测试前切换连接，自动执行完整迁移并导入 8 条测试指南；结束后只删除本次创建的库。
不会在开发数据库注册测试用户、保存测试消息或执行重复导入。测试使用单独随机 JWT 密钥，
不复用或更改开发密钥，也不清空现有数据库。

测试账号需要 PostgreSQL 的 `CREATEDB` 权限，官方镜像第一阶段初始化的账号当前具备此权限。
若以后更换为受限账号，需管理员提供创建测试库的权限；不要改为清空开发库运行测试。
测试连接真实 PostgreSQL 和 Redis，不使用 SQLite 或内存 Redis 替代。
测试覆盖：

- 当前迁移版本、字段和唯一约束。
- 连续重复导入后数量、原记录 ID 和更新时间保持不变。
- 健康检查、关键词、分类、组合筛选、分页、详情、404 和参数校验。
- 首次查询写入缓存，TTL 为 300 秒，再次查询命中且没有数据库读取。
- 仅将一个明确的测试键 TTL 缩短到 1 秒，等待 Redis 真实过期，确认随后确实查询
  PostgreSQL 并重新填充 300 秒缓存；不需要等待完整 5 分钟。
- 损坏的 JSON 或无效编码缓存能回退。
- 让测试客户端连接一个实际拒绝连接的本机临时端口，确认 Redis 读写均失败时接口
  仍从真实 PostgreSQL 返回 200；不停止共用 Redis 容器。
- 重复/并发注册、Argon2 哈希、错误密码、Swagger 登录配置、无效签名和过期令牌。
- 所有受保护接口未登录访问、用户之间的列表/详情/写入/删除隔离。
- 会话和消息参数限制、身份/角色冒充、稳定分页顺序、消息更新会话时间。
- 删除级联、写入或删除提交故障后的真实事务回滚、并发写入与删除不留孤立记录。

测试使用 `campus-assistant:test:guides:<随机编号>:<ID>`，只删除记录下来的明确测试键，
不会清空 Redis、扫描删除业务缓存、删除正式指南或删除数据卷。
重复导入测试只在临时库执行。通常退出时自动清理；如果测试进程被强制终止，可能留下
带上述随机前缀的测试数据库，需要确认名称后单独清理，不能删除开发数据库。

`scripts.verify_auth_http` 另外启动真实 Uvicorn `--reload` 进程，使用本机空闲端口发送 HTTP
请求，完成注册、登录、保存历史、用户隔离和过期令牌验证；随后停止并重新启动应用，
确认原令牌仍能使用、历史仍在 PostgreSQL 中，再验证删除。脚本结束后停止临时应用、
删除专用测试库，不注册开发账号，也不修改 Redis。

手动验收仍可使用第 4 节的 `curl` 命令，另检查容器：

```bash
docker compose ps
docker compose exec -T postgres sh -c 'pg_isready -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
docker compose exec -T redis redis-cli ping
```

期望两个容器均为 `healthy`，PostgreSQL 返回 `accepting connections`，Redis 返回 `PONG`。

## 8. 停止、保留数据与排错

在运行 Uvicorn 的终端按 `Ctrl+C` 停止应用，再按需执行：

```bash
docker compose down
deactivate
```

`down` 保留现有命名卷：

- `campus-assistant_postgres_data`：PostgreSQL 数据。
- `campus-assistant_redis_data`：启用 AOF 持久化的 Redis 数据。

数据库内容不存放在项目 `data/` 目录，该目录现在存放演示 JSON。
**不要添加 `down -v`，它会删除数据卷。** 也不需要回滚迁移来停止项目。
回滚第二份迁移会删除用户、会话、消息，回滚第一份迁移会删除指南表；停止项目不需要回滚。

再次使用时启动容器和应用即可，`upgrade head` 也可以重复执行。

遇到连接错误，先检查 Docker Desktop 和容器，再核对 `.env` 的地址、宿主机端口和原密码。
出现 `relation "guides" does not exist` 表示还没有执行迁移。
容器启动异常可以查看：

```bash
docker compose logs --tail=100 postgres redis
```

## 9. 本次实际验收记录

| 检查 | 实际结果 |
| --- | --- |
| 虚拟环境激活与依赖 | 激活、退出及 `pip check` 通过；保留原 `.venv` |
| PostgreSQL / Redis | 两个容器均为 `healthy`，端口仍仅绑定本机 |
| 数据库迁移 | 到达 `0001_create_guides (head)`，重复执行通过 |
| 模型一致性 | `alembic check` 无待生成的升级操作 |
| 导入 | 首次新增 8 条，第二次新增 0 条；测试中继续重复导入也无重复记录 |
| 自动化测试 | **25 passed**，包含筛选、分页、详情、404、缓存命中和回退 |
| 缓存有效期 | 真实 Redis 键测得 TTL 为 300 秒 |
| 过期回退 | 明确测试键真实过期后，确认发生 PostgreSQL 读取并重新填充缓存 |
| Redis 故障回退 | 真实连接拒绝时，确认读写缓存失败而详情仍由 PostgreSQL 返回 200 |
| 真实 HTTP | 激活虚拟环境并以 Uvicorn `--reload` 启动后，健康检查、分页、组合筛选、详情和 404 均通过 |

交付时两个容器仍在运行，临时 HTTP 验收进程已经停止，测试键已清理。
本阶段没有需要补做的初始化步骤。想手动查看接口，按第 4 节启动应用即可。
正式办事前仍需要提供并核对本校官方指南；当前数据只能用于演示。

## 参考文档

- [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
- [SQLAlchemy 会话管理](https://docs.sqlalchemy.org/en/20/orm/session_basics.html)
- [Alembic 迁移入门](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
- [Redis Python 客户端](https://redis.io/docs/latest/develop/clients/redis-py/)
