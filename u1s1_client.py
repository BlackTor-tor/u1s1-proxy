#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
u1s1 便携版客户端
==================
Windows 单文件便携版 GUI：
  - API 连接验证（列出模型、发一条测试消息）
  - 内置反向代理（复用 u1s1_proxy.py 的标准库实现），一键启停
  - 打开控制台 / 日志输出

仅依赖 Python 标准库（tkinter / http.server / urllib）。
打包：pyinstaller --onefile --windowed --name u1s1-client u1s1_client.py
"""
from __future__ import annotations

import json
import os
import queue
import ssl
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk, scrolledtext
import urllib.error
import urllib.request
import webbrowser

import u1s1_proxy  # 复用标准库反向代理

DEFAULT_API = "https://api.u1s1.io/v1"
# 不内置任何 Key：Key 是用户凭据，写进 exe 会随分发泄露。
# 首次运行时由用户填写，保存到 exe 同目录的 u1s1-client.json。
DEFAULT_KEY = ""
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18081
DEFAULT_UPSTREAM = "https://u1s1.io"
# 网关按 x-u1s1-version 识别客户端版本；官方客户端每个请求都带（见
# ~/.u1s1/u1s1-cli/package.json 的 version）。不带该头的请求会被网关
# 以 403 拒绝（"sign in with the u1s1 client"），因此这里固定带上。
CLIENT_VERSION = "0.20.1"


# 上游 Cloudflare 会拦截 urllib 默认的 Python-urllib UA（403 / SSL 重置），
# 必须带浏览器 UA 才能正常访问。
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _mask_key(key: str) -> str:
    """Key 掩码展示：sk-abcd…mnop；过短或空则整体打码。"""
    if not key:
        return "（未填写）"
    if len(key) <= 8:
        return key[:2] + "…"
    return key[:7] + "…" + key[-4:]


def _http_json(url: str, headers: dict, payload: dict | None = None,
               timeout: float = 30.0) -> tuple[int, dict]:
    """发一个 JSON 请求，返回 (status, body_dict)。

    上游 Cloudflare 会间歇性重置连接（SSL EOF），带浏览器 UA 并重试可规避。
    """
    headers = dict(headers)
    headers.setdefault("User-Agent", _BROWSER_UA)
    headers.setdefault("Accept", "application/json")
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    ctx = ssl.create_default_context()
    last_err: Exception | None = None
    for attempt in range(3):
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if payload else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return resp.status, json.loads(body)
        except (ssl.SSLEOFError, ssl.SSLError, urllib.error.URLError,
                ConnectionError, TimeoutError) as e:
            last_err = e
            time.sleep(0.6 * (attempt + 1))  # 短暂退避后重试
    raise last_err if last_err else RuntimeError("请求失败")


# ---------- 本地配置（API / Key / 端口 / 上游）持久化 ----------
def _config_path() -> Path:
    """配置文件放 exe（或脚本）同目录，便携式携带；目录不可写时退回用户主目录。"""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent
    p = base / "u1s1-client.json"
    if not os.access(base, os.W_OK):
        p = Path.home() / ".u1s1-client.json"
    return p


def load_settings() -> dict:
    try:
        return json.loads(_config_path().read_text("utf-8"))
    except Exception:
        return {}


def save_settings(settings: dict) -> None:
    try:
        _config_path().write_text(
            json.dumps(settings, ensure_ascii=False, indent=2), "utf-8")
    except OSError as e:
        print(f"保存配置失败: {e}")


class ProxyController:
    """在后台线程里运行 u1s1_proxy 的 ThreadingHTTPServer。"""

    def __init__(self, log: callable):
        self.log = log
        self._server = None
        self._thread = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._server is not None

    def start(self, host: str, port: int, upstream: str) -> str:
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

            server = u1s1_proxy.ThreadingHTTPServer((host, port), u1s1_proxy.U1S1ProxyHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self._server, self._thread = server, thread
            return f"代理已启动: http://{host}:{port} -> {scheme}://{uhost}:{uport}"

    def stop(self) -> str:
        with self._lock:
            if self._server is None:
                return "代理未在运行"
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception as e:  # noqa: BLE001
                self.log(f"停止代理时出错: {e}")
            self._server = None
            self._thread = None
            return "代理已停止"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.log_q: queue.Queue[str] = queue.Queue()
        self.proxy = ProxyController(self.log)
        self.cfg = load_settings()

        root.title("u1s1 便携版客户端")
        root.geometry("720x620")
        root.minsize(640, 540)

        self._build_ui()
        self._poll_log()

    # ---------- UI 构建 ----------
    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}

        # ── API 区 ──────────────────────────────
        api = ttk.LabelFrame(self.root, text="API 连接验证", padding=8)
        api.pack(fill="x", **pad)

        row = ttk.Frame(api)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="API 地址:").pack(side="left")
        self.api_var = tk.StringVar(value=self.cfg.get("api") or DEFAULT_API)
        ttk.Entry(row, textvariable=self.api_var, width=40).pack(side="left", padx=6)

        row = ttk.Frame(api)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="API Key:").pack(side="left")
        self.key_var = tk.StringVar(value=self.cfg.get("key") or "")
        ttk.Entry(row, textvariable=self.key_var, width=40, show="*").pack(side="left", padx=6)
        ttk.Button(row, text="验证", command=self.on_verify).pack(side="left")
        ttk.Label(row, text="（自行填写，不内置）", foreground="#6e7781").pack(side="left", padx=6)

        row = ttk.Frame(api)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="模型:").pack(side="left")
        self.model_combo = ttk.Combobox(row, width=40, state="readonly")
        self.model_combo.pack(side="left", padx=6)
        ttk.Button(row, text="测试对话", command=self.on_chat).pack(side="left")

        row = ttk.Frame(api)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="提问:").pack(side="left")
        self.msg_var = tk.StringVar(value="你好，请回复：正常")
        ttk.Entry(row, textvariable=self.msg_var, width=44).pack(side="left", padx=6)

        # ── 代理区 ──────────────────────────────
        prx = ttk.LabelFrame(self.root, text="反向代理（访问 u1s1 控制台）", padding=8)
        prx.pack(fill="x", **pad)

        row = ttk.Frame(prx)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="监听端口:").pack(side="left")
        self.port_var = tk.StringVar(value=self.cfg.get("port") or str(DEFAULT_PORT))
        ttk.Entry(row, textvariable=self.port_var, width=8).pack(side="left", padx=6)
        ttk.Label(row, text="上游:").pack(side="left")
        self.upstream_var = tk.StringVar(value=self.cfg.get("upstream") or DEFAULT_UPSTREAM)
        ttk.Entry(row, textvariable=self.upstream_var, width=32).pack(side="left", padx=6)
        self.port_var.trace_add("write", lambda *_: self._refresh_thirdparty_info())
        self.key_var.trace_add("write", lambda *_: self._refresh_thirdparty_info())

        row = ttk.Frame(prx)
        row.pack(fill="x", pady=2)
        self.btn_start = ttk.Button(row, text="启动代理", command=self.on_start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(row, text="停止代理", command=self.on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=6)
        ttk.Button(row, text="打开控制台", command=self.on_open_dashboard).pack(side="left")

        self.status_var = tk.StringVar(value="状态: 未启动")
        ttk.Label(prx, textvariable=self.status_var, foreground="#1a7f37").pack(anchor="w", pady=(6, 0))

        self.third_var = tk.StringVar()
        ttk.Label(prx, textvariable=self.third_var, foreground="#0969da").pack(anchor="w", pady=(2, 0))

        # ── 日志区 ──────────────────────────────
        logf = ttk.LabelFrame(self.root, text="日志", padding=8)
        logf.pack(fill="both", expand=True, **pad)
        self.log_text = scrolledtext.ScrolledText(logf, height=12, state="disabled",
                                                  font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True)

        self._refresh_thirdparty_info()
        self.log("u1s1 便携版客户端已启动")
        self.log(f"API: {DEFAULT_API}（Key 自行填写，不内置、保存于本机配置文件）")
        self.log(f"代理: http://{DEFAULT_HOST}:{DEFAULT_PORT} -> {DEFAULT_UPSTREAM}")

    # ---------- 日志 ----------
    def log(self, msg: str) -> None:
        self.log_q.put(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def _poll_log(self) -> None:
        try:
            while True:
                line = self.log_q.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", line + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(120, self._poll_log)

    # ---------- API 操作 ----------
    def _api_base(self) -> str:
        return self.api_var.get().strip().rstrip("/")

    def _api_headers(self) -> dict:
        return {
            "Authorization": "Bearer " + self.key_var.get().strip(),
            "x-u1s1-version": CLIENT_VERSION,
        }

    def on_verify(self) -> None:
        self.log("正在验证 API Key ...")
        threading.Thread(target=self._verify_worker, daemon=True).start()

    def _verify_worker(self) -> None:
        try:
            status, body = _http_json(self._api_base() + "/models", self._api_headers())
            models = body.get("data", [])
            if status != 200 or not models:
                self.log(f"验证失败: HTTP {status} {body}")
                return
            ids = [m.get("id", "") for m in models]
            self.log(f"验证成功: 共 {len(ids)} 个模型")
            for m in models:
                self.log(f"  - {m.get('id')}  {m.get('name', '')}")
            self.root.after(0, lambda: self.model_combo.configure(values=ids))
            if ids:
                self.root.after(0, lambda: self.model_combo.set(ids[0]))
        except urllib.error.HTTPError as e:
            self.log(f"验证失败: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:200]}")
        except Exception as e:  # noqa: BLE001
            self.log(f"验证失败: {e}")

    def on_chat(self) -> None:
        model = self.model_combo.get()
        if not model:
            self.log("请先点击“验证”加载模型列表")
            return
        self.log(f"发送测试消息 -> {model}")
        threading.Thread(target=self._chat_worker, args=(model,), daemon=True).start()

    def _chat_worker(self, model: str) -> None:
        try:
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": self.msg_var.get()}],
                "max_tokens": 64,
            }
            status, body = _http_json(self._api_base() + "/chat/completions",
                                      self._api_headers(), payload)
            if status != 200:
                self.log(f"对话失败: HTTP {status} {body}")
                return
            choice = body["choices"][0]
            msg = choice["message"]
            content = msg.get("content") or ""
            reasoning = msg.get("reasoning_content") or ""
            self.log(f"回复: {content[:200] or '(空)'}")
            if reasoning:
                self.log(f"思考: {reasoning[:200]}")
        except Exception as e:  # noqa: BLE001
            self.log(f"对话失败: {e}")

    # ---------- 代理操作 ----------
    def on_start(self) -> None:
        self._save_settings()
        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            self.log("端口格式错误")
            return
        upstream = self.upstream_var.get().strip() or DEFAULT_UPSTREAM
        self.log(f"正在启动代理: 127.0.0.1:{port} -> {upstream} ...")
        threading.Thread(target=self._start_worker, args=(port, upstream), daemon=True).start()

    def _start_worker(self, port: int, upstream: str) -> None:
        msg = self.proxy.start(DEFAULT_HOST, port, upstream)
        self.log(msg)
        self.root.after(0, self._refresh_proxy_buttons)

    def on_stop(self) -> None:
        self._save_settings()
        threading.Thread(target=self._stop_worker, daemon=True).start()

    def _stop_worker(self) -> None:
        msg = self.proxy.stop()
        self.log(msg)
        self.root.after(0, self._refresh_proxy_buttons)

    def _refresh_proxy_buttons(self) -> None:
        running = self.proxy.running
        self.btn_start.configure(state="disabled" if running else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")
        self.status_var.set("状态: 运行中" if running else "状态: 未启动")

    def _refresh_thirdparty_info(self) -> None:
        """实时显示第三方工具需要填写的 Base URL / API Key。"""
        port = self.port_var.get().strip() or str(DEFAULT_PORT)
        base = f"http://127.0.0.1:{port}/v1"
        key = self.key_var.get().strip()
        shown = (key[:10] + "…") if key else "（未填写）"
        self.third_var.set(f"第三方工具填入 → Base URL: {base}    API Key: {shown}")

    def _save_settings(self) -> None:
        save_settings({
            "api": self.api_var.get().strip(),
            "key": self.key_var.get().strip(),
            "port": self.port_var.get().strip(),
            "upstream": self.upstream_var.get().strip(),
        })

    def _on_close(self) -> None:
        """关窗前保存配置并停掉代理。"""
        self._save_settings()
        self.proxy.stop()
        self.root.destroy()

    def on_open_dashboard(self) -> None:
        port = self.port_var.get().strip() or str(DEFAULT_PORT)
        url = f"http://127.0.0.1:{port}/dashboard#sec-usage"
        self.log(f"打开: {url}")
        webbrowser.open(url)


def main() -> None:
    root = tk.Tk()
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except tk.TclError:
        pass
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app._on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
