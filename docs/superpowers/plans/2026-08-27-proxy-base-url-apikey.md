# 反代区 Base URL / API Key 配置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 u1s1 便携版客户端（`u1s1_client.exe`）的反向代理区新增可编辑的 Base URL / API Key 配置：启动代理后向上游请求注入 `Authorization: Bearer <Key>`，让第三方工具只需填 `http://127.0.0.1:<port>/v1` + 任意 Key 即可使用。

**Architecture:** 复用现有结构，改动集中在两个既有文件 + 新增 `tests/`。`u1s1_proxy.py`（标准库反向代理）新增类属性 `authorization` 并把转发头组装抽取为可单测的 `_build_upstream_headers()`；`u1s1_client.py`（tkinter GUI）代理区新增 API Key 输入框、配置持久化 `proxy_key` 键、`ProxyController.start()` 增加 `api_key` 参数并写入 handler、启动日志掩码 Key、第三方提示行按是否配置 Key 显示。

**Tech Stack:** Python 3.8+ 标准库（tkinter / http.server / urllib）；测试用 pytest（仅开发期，不写入 requirements.txt）；打包用 PyInstaller（复用现有 `u1s1-client.spec`，命令不变）。

**规格文档:** `docs/superpowers/specs/2026-08-27-proxy-base-url-apikey-design.md`（已获用户确认，方案 A）

---

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `u1s1_proxy.py` | 标准库反向代理（转发/头处理） | 新增类属性 `authorization`；抽取 `_build_upstream_headers()`；`_do_forward` 改调用它 |
| `u1s1_client.py` | tkinter GUI + 配置持久化 + 代理生命周期 | 代理区 UI（「Base URL」标签 + 「API Key」输入框）；`_save_settings` 存 `proxy_key`；`ProxyController.start()` 加 `api_key` 参数；`_mask_key()` 掩码助手；`_refresh_thirdparty_info` 更新；`on_start`/`_start_worker` 传 Key |
| `tests/test_proxy_injection.py` | 新增：`_build_upstream_headers` 注入逻辑单测 | 新建 |
| `tests/test_client_config.py` | 新增：`_mask_key` 与控制器接线单测 | 新建 |
| `README.md` | 功能说明 | 「反向代理」功能 bullet 补一句新字段 |

执行前安装开发依赖（在项目根目录）：

```bash
pip install pytest
```

---

### Task 1: u1s1_proxy 头组装抽取与 Authorization 注入

**Files:**
- Modify: `u1s1_proxy.py`（`U1S1ProxyHandler` 类内）
- Test: `tests/test_proxy_injection.py`（新建）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_proxy_injection.py`：

```python
"""u1s1_proxy 转发头组装与反代鉴权注入的单测。"""
import unittest

import u1s1_proxy


def make_handler(headers: dict, authorization: str) -> u1s1_proxy.U1S1ProxyHandler:
    """绕开网络，直接构造一个可测的 handler 实例。"""
    h = u1s1_proxy.U1S1ProxyHandler.__new__(u1s1_proxy.U1S1ProxyHandler)
    h.headers = headers
    h.authorization = authorization
    h.upstream_host = "u1s1.io"
    return h


class BuildUpstreamHeadersTest(unittest.TestCase):
    def test_host_rewritten(self):
        out = make_handler({}, "")._build_upstream_headers()
        self.assertEqual(out["Host"], "u1s1.io")

    def test_version_header_added(self):
        out = make_handler({}, "")._build_upstream_headers()
        self.assertEqual(out["x-u1s1-version"], u1s1_proxy.CLIENT_VERSION)

    def test_no_key_no_injection(self):
        out = make_handler({"Cookie": "a=1"}, "")._build_upstream_headers()
        self.assertNotIn("Authorization", out)

    def test_key_injected(self):
        out = make_handler({}, "Bearer sk-abc")._build_upstream_headers()
        self.assertEqual(out["Authorization"], "Bearer sk-abc")

    def test_key_overrides_client_auth(self):
        out = make_handler({"Authorization": "Bearer fake"},
                           "Bearer sk-real")._build_upstream_headers()
        self.assertEqual(out["Authorization"], "Bearer sk-real")
        self.assertNotIn("authorization", out)

    def test_hop_by_hop_stripped(self):
        out = make_handler({"Connection": "keep-alive", "Cookie": "a=1"},
                           "")._build_upstream_headers()
        self.assertNotIn("connection", out)
        self.assertNotIn("Connection", out)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_proxy_injection.py -v`（在项目根目录）
Expected: 全部 FAIL——`AttributeError: 'U1S1ProxyHandler' object has no attribute '_build_upstream_headers'`

- [ ] **Step 3: 最小实现**

在 `u1s1_proxy.py` 的 `U1S1ProxyHandler` 类中（`upstream_port = 443` 之后）新增类属性：

```python
    # 反代鉴权：由客户端工具注入；非空时转发请求自动带上 Authorization
    authorization = ""
```

在 `_forward` 方法之前新增：

```python
    def _build_upstream_headers(self) -> dict:
        """组装转发给上游的请求头：剔除逐跳头、重写 Host、补版本头；
        配置了反代 Key 时注入 Authorization 并覆盖客户端自带的任意 Key。"""
        headers = {
            k: v for k, v in self.headers.items()
            if k.lower() not in _STRIP_REQ
        }
        headers["Host"] = self.upstream_host
        headers.setdefault("x-u1s1-version", CLIENT_VERSION)
        if self.authorization:
            for k in [k for k in headers if k.lower() == "authorization"]:
                del headers[k]
            headers["Authorization"] = self.authorization
        return headers
```

把 `_do_forward` 中原有的内联组装（`headers = {...}`、`headers["Host"] = ...`、`headers.setdefault("x-u1s1-version", ...)` 三行）替换为一行调用：

```python
            headers = self._build_upstream_headers()
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_proxy_injection.py -v`
Expected: 6 个测试全部 PASS

- [ ] **Step 5: 提交**

```bash
git add u1s1_proxy.py tests/test_proxy_injection.py
git commit -m "feat: u1s1_proxy 支持反代鉴权头注入" -m "Co-Authored-By: AtomCode (deepseek-v4-flash) <noreply@atomgit.com>"
```

---

### Task 2: `_mask_key` 掩码助手

**Files:**
- Modify: `u1s1_client.py`（`_BROWSER_UA` 之后新增函数）
- Test: `tests/test_client_config.py`（新建）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_client_config.py`：

```python
"""u1s1_client 掩码助手与代理控制器接线的单测。"""
import unittest

import u1s1_client
import u1s1_proxy


class MaskKeyTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(u1s1_client._mask_key(""), "（未填写）")

    def test_short_key(self):
        self.assertEqual(u1s1_client._mask_key("sk-abc"), "sk…")

    def test_normal_key(self):
        self.assertEqual(u1s1_client._mask_key("sk-abcdefghijklmnop"), "sk-abcd…mnop")

    def test_exact_8_chars(self):
        self.assertEqual(u1s1_client._mask_key("12345678"), "12…")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_client_config.py -v`（在项目根目录）
Expected: 全部 FAIL——`AttributeError: module 'u1s1_client' has no attribute '_mask_key'`

- [ ] **Step 3: 最小实现**

在 `u1s1_client.py` 的 `_BROWSER_UA` 定义之后新增：

```python
def _mask_key(key: str) -> str:
    """Key 掩码展示：sk-abcd…mnop；过短或空则整体打码。"""
    if not key:
        return "（未填写）"
    if len(key) <= 8:
        return key[:2] + "…"
    return key[:7] + "…" + key[-4:]
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_client_config.py -v`
Expected: 4 个测试全部 PASS

- [ ] **Step 5: 提交**

```bash
git add u1s1_client.py tests/test_client_config.py
git commit -m "feat: 增加 Key 掩码展示助手" -m "Co-Authored-By: AtomCode (deepseek-v4-flash) <noreply@atomgit.com>"
```

---

### Task 3: ProxyController 接线反代 Key

**Files:**
- Modify: `u1s1_client.py:121-140`（`ProxyController.start`）
- Test: `tests/test_client_config.py`（追加测试类）

- [ ] **Step 1: 写失败测试**

在 `tests/test_client_config.py` 末尾追加：

```python
class ControllerAuthTest(unittest.TestCase):
    def test_start_injects_authorization(self):
        ctl = u1s1_client.ProxyController(log=lambda msg: None)
        try:
            msg = ctl.start("127.0.0.1", 0, "https://u1s1.io", "sk-abcdef123456")
            self.assertIn("代理已启动", msg)
            self.assertIn("sk-abcd…3456", msg)
            self.assertEqual(u1s1_proxy.U1S1ProxyHandler.authorization,
                             "Bearer sk-abcdef123456")
        finally:
            ctl.stop()
            u1s1_proxy.U1S1ProxyHandler.authorization = ""

    def test_start_without_key_keeps_blank(self):
        ctl = u1s1_client.ProxyController(log=lambda msg: None)
        try:
            ctl.start("127.0.0.1", 0, "https://u1s1.io", "")
            self.assertEqual(u1s1_proxy.U1S1ProxyHandler.authorization, "")
        finally:
            ctl.stop()
            u1s1_proxy.U1S1ProxyHandler.authorization = ""


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_client_config.py -v`
Expected: `test_start_injects_authorization` FAIL——`TypeError: start() takes 4 positional arguments but 5 were given`

- [ ] **Step 3: 最小实现**

把 `u1s1_client.py` 的 `ProxyController.start` 改为：

```python
    def start(self, host: str, port: int, upstream: str, api_key: str = "") -> str:
        with self._lock:
            if self._server is not None:
                return "代理已在运行"
            raw = upstream if "://" in upstream else "https://" + upstream
            from urllib.parse import urlsplit
            u = urlsplit(raw)
            scheme = u.scheme or "https"
            uhost = u.hostname or "u1s1.io"
            uport = u.port or (443 if scheme == "https" else 80)

            u1s1_proxy.U1S1ProxyHandler.upstream_scheme = scheme
            u1s1_proxy.U1S1ProxyHandler.upstream_host = uhost
            u1s1_proxy.U1S1ProxyHandler.upstream_port = uport
            u1s1_proxy.U1S1ProxyHandler.authorization = f"Bearer {api_key}" if api_key else ""

            server = u1s1_proxy.ThreadingHTTPServer((host, port), u1s1_proxy.U1S1ProxyHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self._server, self._thread = server, thread
            suffix = f"（Key: {_mask_key(api_key)}）" if api_key else ""
            return f"代理已启动: http://{host}:{port} -> {scheme}://{uhost}:{uport}{suffix}"
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_client_config.py -v`
Expected: `MaskKeyTest` 4 个 + `ControllerAuthTest` 2 个全部 PASS

- [ ] **Step 5: 提交**

```bash
git add u1s1_client.py tests/test_client_config.py
git commit -m "feat: ProxyController 启动时注入反代鉴权 Key" -m "Co-Authored-By: AtomCode (deepseek-v4-flash) <noreply@atomgit.com>"
```

---

### Task 4: GUI 代理区新增 Base URL / API Key 字段

**Files:**
- Modify: `u1s1_client.py:209-218`（`_build_ui` 代理区）、`:365-371`（`_save_settings`）、`:326-340`（`on_start`/`_start_worker`）、`:357-363`（`_refresh_thirdparty_info`）

UI 无法单测，本 Task 以「语法/导入检查 + 运行验证」收尾（Step 3）。

- [ ] **Step 1: 修改 `_build_ui` 代理区**

把 `_build_ui` 中代理区的第一行（监听端口 + 上游）整体替换为：

```python
        row = ttk.Frame(prx)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="监听端口:").pack(side="left")
        self.port_var = tk.StringVar(value=self.cfg.get("port") or str(DEFAULT_PORT))
        ttk.Entry(row, textvariable=self.port_var, width=8).pack(side="left", padx=6)
        ttk.Label(row, text="Base URL:").pack(side="left")
        self.upstream_var = tk.StringVar(value=self.cfg.get("upstream") or DEFAULT_UPSTREAM)
        ttk.Entry(row, textvariable=self.upstream_var, width=32).pack(side="left", padx=6)

        row = ttk.Frame(prx)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="API Key:").pack(side="left")
        self.proxy_key_var = tk.StringVar(value=self.cfg.get("proxy_key") or "")
        ttk.Entry(row, textvariable=self.proxy_key_var, width=32, show="*").pack(side="left", padx=6)
        ttk.Label(row, text="（可选，转发时自动带上游鉴权）", foreground="#6e7781").pack(side="left", padx=6)

        self.port_var.trace_add("write", lambda *_: self._refresh_thirdparty_info())
        self.proxy_key_var.trace_add("write", lambda *_: self._refresh_thirdparty_info())
```

（原第二行里的 `self.key_var.trace_add(...)` 移除——第三方提示改为由代理区 Key 决定。）

- [ ] **Step 2: 修改保存、启动与提示逻辑**

`_save_settings` 的 dict 增加 `proxy_key`：

```python
    def _save_settings(self) -> None:
        save_settings({
            "api": self.api_var.get().strip(),
            "key": self.key_var.get().strip(),
            "port": self.port_var.get().strip(),
            "upstream": self.upstream_var.get().strip(),
            "proxy_key": self.proxy_key_var.get().strip(),
        })
```

`on_start` / `_start_worker` 传递 Key：

```python
    def on_start(self) -> None:
        self._save_settings()
        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            self.log("端口格式错误")
            return
        upstream = self.upstream_var.get().strip() or DEFAULT_UPSTREAM
        proxy_key = self.proxy_key_var.get().strip()
        self.log(f"正在启动代理: 127.0.0.1:{port} -> {upstream}（Key: {_mask_key(proxy_key)}）...")
        threading.Thread(target=self._start_worker, args=(port, upstream, proxy_key), daemon=True).start()

    def _start_worker(self, port: int, upstream: str, proxy_key: str) -> None:
        msg = self.proxy.start(DEFAULT_HOST, port, upstream, proxy_key)
        self.log(msg)
        self.root.after(0, self._refresh_proxy_buttons)
```

`_refresh_thirdparty_info` 改为按代理区 Key 显示提示：

```python
    def _refresh_thirdparty_info(self) -> None:
        """实时显示第三方工具需要填写的 Base URL / API Key。"""
        port = self.port_var.get().strip() or str(DEFAULT_PORT)
        base = f"http://127.0.0.1:{port}/v1"
        key = self.proxy_key_var.get().strip()
        hint = "任意（代理自动带真实 Key）" if key else "需在工具中配置"
        self.third_var.set(f"第三方工具填入 → Base URL: {base}    API Key: {hint}")
```

- [ ] **Step 3: 语法与导入检查**

Run: `python -m py_compile u1s1_client.py u1s1_proxy.py && python -c "import u1s1_client"`
Expected: 无输出、退出码 0（无 SyntaxError / ImportError）

- [ ] **Step 4: 手动运行验证（GUI）**

Run: `python u1s1_client.py`（本地窗口，人工确认）
Expected:
- 代理区出现「Base URL」与「API Key（星号掩码）」两字段，默认值 Base URL=`https://u1s1.io`、API Key 空。
- 在 API Key 填入任意值后，底部提示行变为「API Key: 任意（代理自动带真实 Key）」；清空后变回「需在工具中配置」。
- 关闭窗口后重开，API Key 值保留（写入了 `u1s1-client.json` 的 `proxy_key` 键）。

- [ ] **Step 5: 提交**

```bash
git add u1s1_client.py
git commit -m "feat: 反代区支持配置 Base URL 与 API Key" -m "Co-Authored-By: AtomCode (deepseek-v4-flash) <noreply@atomgit.com>"
```

---

### Task 5: 全量验证、重建 exe 与文档

**Files:**
- Modify: `README.md:17-19`（反向代理 bullet）
- 验证产物：`dist/u1s1-client.exe`（重新构建）

- [ ] **Step 1: 全量测试**

Run: `python -m pytest tests/ -v`（在项目根目录）
Expected: `tests/test_proxy_injection.py` 6 个 + `tests/test_client_config.py` 6 个，全部 PASS

- [ ] **Step 2: 重建 exe**

Run: `pyinstaller u1s1-client.spec`
Expected: 构建成功，`dist/u1s1-client.exe` 时间戳更新

- [ ] **Step 3: 更新 README**

把 README「方式一：便携版 exe」下的反向代理 bullet 更新为：

```markdown
- **反向代理**：一键启动/停止 u1s1 控制台代理（默认监听 `127.0.0.1:18081`，Base URL 默认 `https://u1s1.io`；可选填 API Key，启动后转发请求自动带上游鉴权，第三方工具填 `http://127.0.0.1:18081/v1` + 任意 Key 即可），点「打开控制台」直达 `http://127.0.0.1:18081/dashboard#sec-usage`。
```

- [ ] **Step 4: 提交**

```bash
git add README.md
git commit -m "docs: README 补充反代 Base URL / API Key 说明" -m "Co-Authored-By: AtomCode (deepseek-v4-flash) <noreply@atomgit.com>"
```

- [ ] **Step 5: 用户验收指引（交付时告知，不在本任务执行）**

用户拿到新 exe 后：
1. 启动 exe，反代区 Base URL 保持 `https://u1s1.io`，API Key 填自己 u1s1 账号的 Key（`sk-` 开头，与「API 连接验证」区同一个）。
2. 点「启动代理」，日志显示 `代理已启动: http://127.0.0.1:18081 -> https://u1s1.io（Key: sk-abcd…mnop）`。
3. 任意第三方工具（如 Cherry Studio / ChatBox）填 Base URL `http://127.0.0.1:18081/v1` + 任意 Key，即可正常对话。
```
```
