# u1s1 客户端反代区新增 Base URL / API Key 配置 — 设计文档

日期：2026-08-27
状态：已获用户确认（方案 A）

## 背景与目标

u1s1 便携版客户端（`u1s1_client.py`，打包为 `u1s1-client.exe`）内置反向代理，转发到上游 u1s1.io。当前代理区只有「监听端口 / 上游」可编辑配置；API Key 仅用于「API 连接验证」区。

用户希望反代区也能配置 **Base URL** 与 **API Key**：启动代理后，转发到上游的每个请求自动注入 `Authorization: Bearer <Key>`，使第三方工具只需指向本地代理即可使用，真实 Key 只保存在工具本机配置中，不内置进 exe。

目标：

- 代理区新增可编辑的 Base URL（复用现有「上游」字段，改标签名）与 API Key 字段，持久化到 `u1s1-client.json`。
- 启动代理后，上游请求自动注入 `Authorization: Bearer <Key>`（填了才注入；未填则行为与现状一致，透传客户端自带头）。
- 第三方工具填 `http://127.0.0.1:<port>/v1` + 任意 Key 即可使用。
- 不内置任何 Key 到 exe（用户自行填写，保存于本机配置文件）。

## 方案（已确认：方案 A）

| 方案 | 做法 | 结论 |
|---|---|---|
| A（选定） | 代理区加「Base URL + API Key」可编辑字段并保存；转发时注入 Authorization 头 | 真实 Key 只存在工具里，第三方工具 Key 随便填 |
| B | 只加字段做展示/复制，不改转发逻辑 | 弃：第三方工具仍需真实 Key |
| C | 仅「上游」改名 + Key 注入 | 并入 A 实现 |

## 设计

### 1. UI（u1s1_client.py `_build_ui` 反向代理区）

- 现有「上游」标签改为「Base URL」，`self.upstream_var` 保持不变（默认 `https://u1s1.io`）。
- 新增「API Key」输入框：`self.proxy_key_var`，`show="*"`，默认空，标签旁注明「（可选，自动带上游鉴权）」。

### 2. 配置持久化（u1s1_client.py）

- `_save_settings` 增加 `"proxy_key": self.proxy_key_var.get().strip()`；`upstream` 键沿用，老配置兼容（无 `proxy_key` 键时默认空）。
- `load_settings` 无需改动（`cfg.get(...)` 容错）。

### 3. 转发注入（u1s1_proxy.py）

- `U1S1ProxyHandler` 增加类属性 `authorization = ""`（默认不注入）。
- `_do_forward` 组装请求头后：若 `self.authorization` 非空，则 `headers["Authorization"] = self.authorization`（覆盖客户端任意 Key）。
- 与现有 Cookie 登录、`x-u1s1-version` 补头逻辑互不干扰；GET/POST/流式/重定向全部生效（统一走 `_forward`）。

### 4. 启动接线（u1s1_client.py `ProxyController.start`）

- `start(host, port, upstream, api_key)` 新增 `api_key` 参数。
- 设置 `u1s1_proxy.U1S1ProxyHandler.authorization = f"Bearer {api_key}" if api_key else ""`。
- 启动日志打印掩码 Key（如 `sk-abc…xyz`），不打印明文。

### 5. 第三方提示行（`_refresh_thirdparty_info`）

- Key 已填 → 「API Key: 任意（代理自动带真实 Key）」。
- 未填 → 「API Key: 需在工具中配置」。

## 边界与错误处理

- Key 留空：不注入，行为与现状完全一致。
- 端口非法：沿用现有 `ValueError` 校验。
- 配置保存失败：沿用现有 `save_settings` 容错。
- 不改变「API 连接验证」区 `key` 的存储与用法。

## 测试

1. 源码运行 `python u1s1_client.py`，填 Base URL / API Key 后启动代理。
2. 用 `curl http://127.0.0.1:<port>/v1/models -H "Authorization: Bearer fake"` 验证上游收到的是真实 Key（通过上游行为/响应判断）。
3. 未填 Key 时行为与旧版一致。
4. 打包验证：`pyinstaller --onefile --windowed --name u1s1-client u1s1_client.py`。

## 用户需填写的值（功能交付后由用户自填）

- Base URL：`https://u1s1.io`（默认值；如使用中转服务则填中转地址）。
- API Key：用户 u1s1 账号的 Key（`sk-` 开头，与「API 连接验证」区同一个）。
