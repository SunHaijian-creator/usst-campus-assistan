# 前端实现

页面使用原生HTML、CSS和JavaScript，提供登录、会话历史、流式聊天和资料来源查看。

## 结构

| 文件 | 用途 |
| --- | --- |
| `app/web/index.html` | 登录注册、侧栏、欢迎页、聊天区及详情弹窗 |
| `app/web/chat.css` | 配色、排版、响应式布局及焦点样式 |
| `app/web/chat.js` | 认证请求、消息交互、来源详情及手机抽屉 |
| `app/web/sse.js` | SSE分块解析 |
| `app/web/assets/` | 本地校徽、校名及产品图标 |

## 交互

- 桌面侧栏展示新建会话和个人历史；手机端使用抽屉。
- 输入区支持发送、停止及生成状态显示，回复可以复制。
- 用户接近底部时自动跟随新消息，向上查看历史时保留阅读位置。
- 回答实际使用的引用展示为来源卡片，可展开摘录及访问原件。
- 刷新后从后端恢复历史，页面显示当前实际模型模式。

原件通过带认证请求头的接口获取，再创建浏览器本地blob地址，不把令牌加入下载链接。

## 校园主题

主色使用威尼斯红，配合白色与浅灰背景。校徽及校名保持原始比例，置于品牌区域；普通助手消息使用独立产品图标。

素材来源见[素材说明](../app/web/assets/SOURCES.md)。账号属于本项目，不接入学校统一身份认证。

## 页面预览

- [桌面登录](screenshots/usst-login-desktop.png)
- [桌面聊天](screenshots/usst-chat-desktop.png)
- [文档来源](screenshots/usst-documents-desktop.png)
- [手机聊天](screenshots/usst-chat-mobile.png)
- [手机抽屉](screenshots/usst-drawer-mobile.png)

运行步骤见[README](../README.md)。
