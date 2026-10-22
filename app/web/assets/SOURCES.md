# 上海理工大学主题素材来源

校徽及校名为校方原始素材，本项目是个人学习项目，非学校官方服务。

## 校徽与校名

官方来源页：[校长办公室「视觉形象」](https://xb.usst.edu.cn/2019/0826/c8624a176918/page.htm)。
另核对 [学校官网「上理标识」](https://www.usst.edu.cn/_t2/slbs/list.psp)。

| 本地文件 | 官方下载与压缩包内文件 | SHA-256 |
| --- | --- | --- |
| `usst-emblem.jpg` | [校徽（正式版）.zip](https://xb.usst.edu.cn/_upload/article/files/64/1c/a9dd8ac749f897b1919b79018933/f254e352-1220-4649-9a01-08a826388535.zip) / `1校徽（正式版）/单独校徽（正式版）.jpg` | `6496259ed2867b87fda9e5f67799c98ddb63d689f4f2a6ef64416227403f0193` |
| `usst-lockup.jpg` | [校徽与中文校名横式组合（正式版）.zip](https://xb.usst.edu.cn/_upload/article/files/64/1c/a9dd8ac749f897b1919b79018933/3310c15b-605f-40ce-b6ee-8a11fa189c36.zip) / `2校徽与中文校名横式组合（正式版）/校徽与中文校名横式组合（正式版）.jpg` | `539d8abb4d33dbf6bc5a5c1ebbcffaf6cfd5419c19335ca7edfb3318bfe2cb90` |

JPG 从官方压缩包逐字节提取，仅重命名，未重绘、改色、拉伸、叠加装饰或重新编码。
原图包含较多纸张留白，页面用等比例图片及容器隐藏外围空白，保留校徽/校名图形的完整性。
校徽用于登录品牌区、侧栏和欢迎页；普通助手消息使用独立产品图标，不将复杂校徽缩作头像。

## 标准色

同一官方页面的 [标准色与辅助色（正式版）.rar](https://xb.usst.edu.cn/_upload/article/files/64/1c/a9dd8ac749f897b1919b79018933/a018f004-a190-477a-9852-108acc7770c0.rar)。
已提取原始说明图到 `docs/assets/usst-standard-color.jpg`、`docs/assets/usst-complementary-colors.jpg`。

标准色说明图明确给出「威尼斯红（VENETIAN RED）」的屏幕 RGB：**184 / 27 / 48**，
对应 CSS `#B81B30`。页面 `--brand` 使用该值，其他浅红、白、灰为本项目界面衍生色，
不冒称校方指定的完整网页配色。印刷等其他用途应以原始 VI 文件为准。

## 产品图标

`assistant.svg` 为本项目用 SVG 路径绘制的独立聊天气泡图标，用于产品标识、favicon 和助手消息头像，
不使用学校校徽图形，不表示校方认证。没有引入外部字体、图标 CDN 或前端框架。
