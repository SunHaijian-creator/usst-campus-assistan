# DeepSeek接入

## 配置

初始化项目后，在本机 `.env` 中设置：

```dotenv
MODEL_PROVIDER=deepseek
MODEL_BASE_URL=https://api.deepseek.com
MODEL_NAME=deepseek-flash
MODEL_API_KEY=your_deepseek_api_key_here
MODEL_TIMEOUT_SECONDS=30
MODEL_MAX_TOKENS=1024
```

```bash
docker compose up -d --force-recreate app
```

密钥仅在服务端读取。保留数据库密码和JWT密钥，避免覆盖已有配置。

| 配置项 | 作用 |
| --- | --- |
| `MODEL_PROVIDER` | `deepseek`调用供应商接口，`mock`使用本地固定模板 |
| `MODEL_BASE_URL` | 供应商服务地址，适配器拼接聊天接口路径 |
| `MODEL_NAME` | 上游模型ID |
| `MODEL_API_KEY` | 服务端API Key |
| `MODEL_TIMEOUT_SECONDS` | 总生成时限与上游超时配置 |
| `MODEL_MAX_TOKENS` | 上游输出上限 |

页面从 `/chat/config` 获取模式和模型名称。配置就绪标识只表示必要字段存在，不代表供应商账户及网络一定可用。

## 调用流程

适配器使用异步HTTP请求访问Chat Completions接口，启用流式输出和非思考模式。服务端将上游文本转换为应用的SSE事件。

模型输入包含回答约束、独立参考资料、已完成历史及当前问题。上游连接在结束、超时或取消时关闭。自动重试和HTTP重定向均关闭，避免重复生成及意外传递认证信息。

结束原因、输出截断和异常中断参与消息状态判断；异常结束不能保存为完整成功回复。

## 错误处理

供应商鉴权、额度、限流、超时及服务故障分别映射为可读的聊天错误。本项目账号的认证与供应商认证独立，上游鉴权失败不要求用户退出本项目账号。

日志不记录API Key或完整认证请求头。API费用以供应商账户规则为准。

## 本地模式

设置 `MODEL_PROVIDER=mock` 后重建app容器即可使用本地模板。页面明确标识该模式；它可用于接口、流式及交互测试，不提供真实模型分析能力。

测试方法见[README](../README.md)。

## 协议参考

- [DeepSeek API文档](https://api-docs.deepseek.com/)
- [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)
- [错误码](https://api-docs.deepseek.com/quick_start/error_codes/)
