# 上理办事助手 · USST Campus Assistant

面向校园事务咨询的个人学习项目：支持多轮聊天、流式回复、资料检索、可展开引用及个人会话历史。

**Python / FastAPI · PostgreSQL · Redis · DeepSeek · SSE · Docker Compose**

[![Tests](https://github.com/SunHaijian-creator/usst-campus-assistan/actions/workflows/ci.yml/badge.svg)](https://github.com/SunHaijian-creator/usst-campus-assistan/actions/workflows/ci.yml)

> 个人学习项目，非上海理工大学官方服务。内置8条办事指南为虚构演示数据；提供的参考文件未核验当前适用性。项目账号不接入学校统一身份认证，请勿输入学校账号密码。

## 项目预览

![上理办事助手桌面欢迎页](docs/screenshots/usst-welcome-desktop.png)

<details>
<summary>查看登录、聊天、文档引用及手机端截图</summary>

![登录页](docs/screenshots/usst-login-desktop.png)
![桌面聊天](docs/screenshots/usst-chat-desktop.png)
![文档原文与来源](docs/screenshots/usst-documents-desktop.png)

<img src="docs/screenshots/usst-chat-mobile.png" alt="手机端聊天" width="280"> <img src="docs/screenshots/usst-drawer-mobile.png" alt="手机端历史抽屉" width="280">

截图展示mock模式下的产品交互，账号显示名已匿名。
</details>

## 能做什么

- **独立账号与个人历史**：Argon2密码哈希、JWT认证，按用户隔离会话和消息，支持分页与会话删除。
- **异步流式聊天**：DeepSeek适配器与免费mock模式；浏览器使用带Bearer头的POST fetch读取SSE，支持停止、超时和失败状态。
- **校园资料检索与引用**：3份文件按条款、栏目及表格分块；保存版本、哈希和定位，回答只展示实际引用的合法编号，原件入口需要登录。
- **有边界的分析**：一般交流与学习规划可由模型分析，具体金额、资格和期限优先依据资料，区分原文与建议。
- **可恢复的上下文**：PostgreSQL保存完整历史；Redis只缓存最近已完成的问答，过期或不可用时回源，失败/取消的回答不进入正常上下文。
- **可复现部署**：Compose管理应用、单次初始化、PostgreSQL和Redis；增量迁移与幂等导入，非root应用镜像。

## 快速开始

适用：Linux / WSL 2 的 Bash，已安装 **Python 3.12**、Git及支持启动依赖条件的Docker Compose。Windows用户先启动Docker Desktop并启用WSL集成。依赖和基础镜像首次下载需要网络。

```bash
git clone https://github.com/SunHaijian-creator/usst-campus-assistan.git usst-campus-assistant
cd usst-campus-assistant

# 纯标准库初始化，不安装Python依赖；生成随机数据库密码与JWT密钥。
# 已有 .env 会完整保留。
python3 -m scripts.setup_env

docker compose up -d --build --wait --wait-timeout 120
docker compose ps --all
curl --fail http://127.0.0.1:8000/ready
```

打开 **http://127.0.0.1:8000/**，注册本项目账号并登录。接口文档在 **/docs**。

预期：`app`、`postgres`、`redis`健康，`migrate`执行完成退出码0。迁移到`0005_reference_documents`，初始化导入8条演示指南、3份资料和30片段，其中29片段允许检索。重复启动不会重复导入，也不重置已有账号和历史。

默认 **mock** 不产生模型API费用，可演示登录、检索、引用、流式和历史；它是固定模板，不具备真实语义推理能力。`/health`只检查进程响应，`/ready`要求数据库可用，Redis不可用时标记降级。

启动命令等待应用健康检查通过后再返回。若超时，先执行 `docker compose ps --all` 和 `docker compose logs --tail=80 app migrate postgres` 查看原因；不要通过删除数据卷重新初始化。

### 启用真实DeepSeek

只在本机 `.env` 中填写以下字段，保留已有数据库密码和JWT密钥：

```dotenv
MODEL_PROVIDER=deepseek
MODEL_NAME=deepseek-flash
MODEL_BASE_URL=https://api.deepseek.com
MODEL_API_KEY=your_deepseek_api_key_here
MODEL_MAX_TOKENS=1024
MODEL_TIMEOUT_SECONDS=30
```

```bash
docker compose up -d --force-recreate --wait --wait-timeout 120 app
```

密钥仅由后端读取，不能写入前端或提交Git。真实请求按供应商额度计费；适配器关闭自动重试，使用非思考模式。回到mock时设置`MODEL_PROVIDER=mock`并重建app容器。配置说明见[模型接入](docs/deepseek.md)。

### 日常启停

```bash
docker compose stop          # 停止，保留数据卷
docker compose up -d --wait --wait-timeout 120  # 再次启动并等待就绪
docker compose logs --tail=80 app migrate
```

不要删除数据卷。`APP_PORT`、`POSTGRES_PORT`、`REDIS_PORT`可在`.env`调整；所有映射端口默认仅绑定127.0.0.1。容器之间使用服务名`postgres`/`redis`，不是宿主机地址。

## 架构与数据流

```mermaid
flowchart LR
    UI[原生响应式聊天页面] -->|JWT / REST / POST SSE| API[FastAPI]
    API --> AUTH[Argon2 / JWT / 数据归属校验]
    API --> SEARCH[主题路由 / 中文双字词面排序]
    SEARCH --> DOC[文档条款 / 表格 / 引用定位]
    API --> PG[(PostgreSQL)]
    DOC --> PG
    API <-->|已完成问答的短期上下文| REDIS[(Redis)]
    API --> MODEL[异步模型适配层]
    MODEL --> DS[DeepSeek API]
    MODEL --> MOCK[Mock 流式适配器]
    MODEL -->|delta / done / error| API
    INIT[单次 migrate 服务] -->|迁移 / 幂等导入| PG
```

1. 校验登录、会话归属和并发生成状态，写入用户消息及生成记录。
2. 读取已完成历史与相关资料；无关资料不强行作为引用。
3. 将上游流转换为`meta / delta / done / error`，客户端停止时关闭上游并保存取消状态。
4. 正常完成后保存助手消息及合法引用，更新短期上下文。模型等待期间不持有长数据库事务。

**技术边界**：当前是面向3份小文档的主题路由、中文双字词面评分和规则联检，没有向量数据库、embedding、BM25或自主工具调用。可称为检索增强问答应用，不宣称为多工具自主Agent。编号校验保证引用来自候选片段，不等于自动证明每句话都被原文支持。

### 实现取舍

- **先保证资料可核对**：资料量较小，按条款和表格整理片段，保留版本与定位，采用可直接检查匹配过程的词面检索。复杂改写的覆盖有限，扩展到更多文档时需要重新评估检索方式。
- **数据库保存历史，Redis缓存上下文**：缓存只保存最近已完成的问答，不缓存本轮答案；每次仍向数据库校验消息版本，主要减少历史文本的重新组装，不能宣称完全免去数据库读取。
- **短事务协调生成**：同一会话用行锁与部分唯一索引防止重复生成；模型等待期间释放数据库连接，完成后另开事务保存。
- **POST读取SSE**：问题放在请求体，认证放在请求头，浏览器增量解析事件；停止时取消请求，由后端关闭上游流。当前不支持流断点续传。

代码阅读入口：`app/chat.py` → `app/reference_search.py` / `app/context_cache.py` → `app/deepseek.py`。数据约束见 `app/models.py`，浏览器事件解析见 `app/web/sse.js`。

## 资料与回答边界

| 资料 | 使用方式 |
| --- | --- |
| 全日制硕士研究生奖助学金管理办法 | 保留适用对象、例外、跨页条款和表格对应关系，强制提示当前适用性未核验 |
| 研究生中期考核表 | 依据栏目与注释回答，空白字段不能推断最低学分或截止日期 |
| 学位论文开题及信息提交流程 | 仅检索4条正文注释，未核对箭头的流程图片段禁止用于生成步骤 |

原件与提取记录见[data/references/usst](data/references/usst)，导入与定位机制见[资料接入说明](docs/references.md)。文件作为数据处理，不执行其中的宏、链接或指令。提供的文件仅用于个人学习展示，不代表学校当前政策。

## 开发与测试

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m scripts.setup_env
docker compose up -d --wait --wait-timeout 120 postgres redis
python -m alembic upgrade head
python -m scripts.import_demo_guides
python -m scripts.import_usst_references
python -m pytest -q tests
```

测试需要可连接的PostgreSQL与Redis，数据库账号需要`CREATEDB`权限。测试为每次运行创建随机临时库，使用随机JWT密钥和明确的Redis测试键，退出后清理；不清空开发库或Redis。上游调用使用模拟响应，不自动消耗真实额度。

本地热重载：停止Compose中的app后执行`python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload`。已有app占用8000端口时不要同时启动另一个后端。

### 测试覆盖

测试覆盖认证与用户隔离、事务一致性、缓存回退、SSE事件、引用定位及初始化流程。GitHub Actions在独立PostgreSQL和Redis环境中运行测试：[查看测试状态](https://github.com/SunHaijian-creator/usst-campus-assistan/actions)。自动测试使用受控上游响应，不产生模型API费用。

CI当前运行Python接口与集成测试，不包括镜像构建、浏览器检查或真实模型语义评价。浏览器检查需要单独安装Chromium，步骤见[前端实现](docs/frontend.md#浏览器检查)。

## 技术文档

- [回答策略](docs/answer-policy.md)
- [SSE与会话](docs/chat.md)
- [前端实现](docs/frontend.md)
- [资料接入](docs/references.md)
- [模型接入](docs/deepseek.md)
- [已知限制](docs/limitations.md)

## 素材与使用说明

校徽、校名、标准色及学校文件的权利归相应权利方；公开获取不等于可以任意授权再使用。来源记录见[素材说明](app/web/assets/SOURCES.md)与[第三方说明](THIRD_PARTY_NOTICES.md)。部署或复用时请自行核对相应用途。本仓库不表示学校背书。
