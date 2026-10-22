# 上理办事助手：前端主题与验收

产品名称「上理办事助手」，副标题「校园事务，一问明白」。在第五阶段已有后端上改造，
继续使用原生 HTML / CSS / JavaScript，没有增加前端框架、构建流程或外部 CDN。

页面固定保留：**个人学习项目，非学校官方服务；演示指南仅供功能展示。**
登录/注册是本项目独立账号，不是学校统一身份认证；不得在这里填写学校认证密码。
学校主题只改变界面，不会把现有虚构指南变成学校真实政策。

## 文件与素材

| 文件 | 用途 |
| --- | --- |
| `app/web/index.html` | 独立登录/注册、品牌、会话侧栏、欢迎卡片、聊天区和指南详情弹窗 |
| `app/web/chat.css` | 官方威尼斯红主色、排版、桌面/手机布局、焦点、抽屉及减少动画支持 |
| `app/web/chat.js` | 沿用原认证/API/SSE，完善加载/错误、复制、来源详情、自动滚动和手机抽屉 |
| `app/web/sse.js` | 保留原 SSE 分块解析实现，未改动 |
| `app/web/assets/usst-emblem.jpg` | 从校方正式版压缩包提取的校徽原图 |
| `app/web/assets/usst-lockup.jpg` | 从校方正式版压缩包提取的校徽与中文校名横式组合原图 |
| `app/web/assets/assistant.svg` | 本项目独立助手图标，不重绘校徽 |
| `app/web/assets/SOURCES.md` | 官方素材直链、压缩包内文件名、SHA-256 与使用方式 |
| `docs/assets/usst-standard-color.jpg` | 校方标准色说明原图，RGB 184/27/48，即 `#B81B30` |
| `scripts/verify_frontend.py` | 独立数据库、mock 模式下的桌面/手机完整交互检查与截图 |
| `docs/screenshots/` | 实际浏览器生成的登录、欢迎、聊天、来源详情和手机抽屉截图 |

校徽未重绘、拉伸、改色或加装饰，原始 JPG 字节不变；仅隐藏外围纸张留白。
素材来源：[上理标识](https://www.usst.edu.cn/_t2/slbs/list.psp)、
[校长办公室「视觉形象」](https://xb.usst.edu.cn/2019/0826/c8624a176918/page.htm)。
详细原始下载记录见 [SOURCES](../app/web/assets/SOURCES.md)。

## 启动和手动验收

```bash
cd /home/sun/projects/campus-assistant
source .venv/bin/activate
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

数据库/Redis 继续沿用现有 Compose 服务与数据卷，配置、迁移步骤见 README。
浏览器访问 <http://127.0.0.1:8000/>。当前运行模式来自 `/chat/config`，页头显示实际模式和模型。
默认 mock 不调用真实 API；DeepSeek 配置、缺少密钥的处理继续遵循第五阶段说明。
本次设计验收不调用真实 DeepSeek、不产生模型费用，也不改变本机 `.env`。

1. 在登录页切换「注册账号」，创建项目账号，再切回登录。错误密码会就地提示。
2. 登录后新建会话，点击校园卡补办、在读证明、教室借用快捷卡片：只填入问题，
   点击发送或按 Enter 后才调用聊天接口。未选择会话时首次发送会自动创建自己的会话。
3. 查看逐步显示的回复；点击「停止生成」检查取消状态。mock 模式可从模拟场景选择失败/超时。
4. 展开回答下方「参考指南」，查看本次实际匹配的标题、来源及演示标识；
   点击「查看指南详情」通过既有 `GET /guides/{id}` 读取材料、流程、来源和更新时间。
5. 点击「复制回复」，检查复制成功提示。刷新页面，确认账号、会话、历史及真实模型标识恢复。
6. 历史较长时向上滚动，然后等待新回复：保持阅读位置，点击「回到最新消息」才跳到底部。
7. 手机点击左上角菜单打开会话抽屉，创建/切换会话后自动关闭。Escape 可关闭，
   键盘焦点限制在打开的抽屉内，关闭后返回菜单按钮。
8. 发送使用 Enter，Shift + Enter 换行，中文输入法选词不触发发送。

流式区不把每个分段设置为屏幕阅读器的重复播报；生成、错误、复制等状态使用 `aria-live`。
图像有替代文本，按钮保留文字，保留键盘焦点样式和 `prefers-reduced-motion`。
桌面历史独立滚动；输入区固定在布局底部。手机使用 `100dvh` 和安全区域内边距，
适配浏览器可用高度；自动化尺寸检查不等于已在所有实体手机/输入法上测试。

## 自动检查与截图

在项目 `.venv` 中执行（只用 mock）：

```bash
python -m pytest -q tests
python -m scripts.verify_chat_http --browser
python -m scripts.verify_deepseek_http --browser
python -m scripts.verify_frontend
```

本机 Chromium 和补充库位于 `/tmp`，可使用以下环境变量：

```bash
LD_LIBRARY_PATH=/tmp/campus-assistant-browser-libs/root/usr/lib/x86_64-linux-gnu \
PLAYWRIGHT_BROWSERS_PATH=/tmp/campus-assistant-browsers \
FONTCONFIG_FILE=/tmp/campus-assistant-fonts.conf \
python -m scripts.verify_frontend
```

`FONTCONFIG_FILE` 仅供本机 Linux 浏览器截图显示中文，引用本机 Windows 字体路径，
没有将系统字体复制或打包进项目；普通用户浏览器使用本机字体，无需此配置。
这些 `/tmp` 文件在其他机器上需要重新准备；也可按 README 安装 Playwright Chromium。
脚本自动创建并清理随机临时数据库，只删除其明确的 Redis 上下文键，不清空开发数据库或 Redis。

截图是使用 mock 及隔离测试账号生成的真实页面截图，不代表学校官方服务或真实模型验收。
本次实际执行结果将记录在 README。
