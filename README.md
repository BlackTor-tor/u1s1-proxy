# u1s1-proxy

把 [u1s1.io](https://u1s1.io) 整个站点（控制台 `/dashboard`、`/api/*`、`/auth/*`、静态资源）反向代理到本地，用于在受限网络环境下访问 u1s1 控制台。

零第三方依赖，仅用 Python 标准库（Python 3.8+），单文件实现。

## 为什么单独建目录，而不是并入 atomcode-proxy

`atomcode-proxy` 是「OpenAI/Anthropic 协议 → AtomCode 本地 daemon」的协议翻译适配代理，自带状态页/设置页、托盘、daemon 生命周期管理、PyInstaller 打包，职责高度聚焦。u1s1 反代是另一个站点的通用 HTTP 反向代理，两者目的、配置、生命周期完全不同，混在一起会污染原项目的打包与配置边界，因此单独放在本目录。

## 快速开始

### 方式一：便携版 exe（推荐，Windows）

直接双击 `dist/u1s1-client.exe` 即可，无需安装 Python。这是一个带界面的单文件便携版客户端：

- **API 连接验证**：填入 API 地址与 Key，点「验证」列出可用模型，可发一条测试对话。
- **反向代理**：一键启动/停止 u1s1 控制台代理（默认监听 `127.0.0.1:18081`，Base URL 默认 `https://u1s1.io`；可选填 API Key，启动后转发请求自动带上游鉴权，第三方工具填 `http://127.0.0.1:18081/v1` + 任意 Key 即可），点「打开控制台」直达 `http://127.0.0.1:18081/dashboard#sec-usage`。
- 底部日志区实时显示运行状态。

> 重新打包：`pyinstaller --onefile --windowed --name u1s1-client u1s1_client.py`

### 方式二：源码运行

```bash
python u1s1_proxy.py
```

默认监听 `127.0.0.1:18081`（避开本机 Cursor 占用的 18080），然后浏览器打开：

```
http://127.0.0.1:18081/dashboard#sec-usage
```

Windows 下也可以直接双击 `start.bat`。

## 参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--host` | `127.0.0.1` | 监听地址（`0.0.0.0` 可局域网访问，注意安全） |
| `--port` | `18080` | 监听端口 |
| `--upstream` | `https://u1s1.io` | 上游地址 |

## 实现要点

- **同源 SPA**：u1s1 前端所有接口（`/api/*`、`/auth/*`）与页面同源，因此只需整体转发并重写 `Host` 头，无需改写路径。
- **登录态走 Cookie**：前端 `fetch` 使用 `credentials: "same-origin"`，无 `Authorization` 头。反代会去掉响应 `Set-Cookie` 里的 `Domain` 与 `Secure` 属性，使 Cookie 绑定到本地地址（否则浏览器不会为 `localhost` 存储 Cookie，登录会失败）。
- **重定向改写**：`Location` 头中的上游域名会被改写为本地地址。
- **流式透传**：逐跳头（`Connection`/`Transfer-Encoding` 等）不转发，响应体以 chunked 流式回传，`gzip` 等 `Content-Encoding` 原样透传。
- **无 WebSocket**：调研确认控制台不使用 WebSocket，无需 WS 代理。
- **第三方组件**：页面会直接加载 `https://capcat.ai/widget/cap.js`（客服小部件），该请求不经代理、直连 capcat.ai，不影响使用；如需要可自行屏蔽。

## 注意事项

- 本地监听是明文 HTTP，请勿在不可信网络上以 `--host 0.0.0.0` 暴露，也不要与他人共享你的登录态。
- 反代仅用于个人访问 u1s1 控制台，请遵守 u1s1 的服务条款。
