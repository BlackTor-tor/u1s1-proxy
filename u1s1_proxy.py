#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
u1s1 本地反向代理
=================
把 https://u1s1.io 整个站点（/dashboard 控制台、/api/*、/auth/*、静态资源）
反向代理到本地端口，用于在受限网络环境下访问 u1s1 控制台。

零第三方依赖，仅用 Python 标准库（Python 3.8+）。

用法:
    python u1s1_proxy.py                      # 默认监听 127.0.0.1:18080
    python u1s1_proxy.py --port 8080          # 换端口
    python u1s1_proxy.py --host 0.0.0.0       # 局域网可访问（注意安全）
    python u1s1_proxy.py --upstream https://u1s1.io

访问:
    http://127.0.0.1:18080/dashboard#sec-usage

实现要点:
    - 前端为同源 SPA：/api/*、/auth/* 等接口与页面同源，因此只需整体转发
      并重写 Host 头，无需改写任何路径。
    - 登录态走 Cookie（前端 fetch 使用 credentials: same-origin，无
      Authorization 头）。反代会把响应 Set-Cookie 里的 Domain 与 Secure
      属性去掉，使 Cookie 绑定到本地地址，否则浏览器不会为 localhost 存储。
    - 重定向 Location 头会把上游域名改写为本地地址。
    - 逐跳头（Connection/Transfer-Encoding 等）不会转发，响应体以
      chunked 形式流式回传，gzip 等 Content-Encoding 原样透传。
"""
from __future__ import annotations

import argparse
import http.client
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

DEFAULT_HOST = "127.0.0.1"
# 默认端口避开 18080（本机 Cursor 已占用该端口）
DEFAULT_PORT = 18081
DEFAULT_UPSTREAM = "https://u1s1.io"
# 网关按 x-u1s1-version 识别客户端版本（官方客户端每个请求都带），
# 不带该头的聊天请求会被网关以 403 拒绝，转发时补上。
CLIENT_VERSION = "0.20.1"

# 逐跳头：不转发给上游，也不回传给客户端
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
}
# 请求侧由本代理自行处理、不转发的头
_STRIP_REQ = _HOP_BY_HOP | {"host", "content-length"}
# 响应侧由本代理自行处理、不回传的头
_STRIP_RESP = _HOP_BY_HOP | {"content-length"}

# 这些状态码没有响应体
_NO_BODY_STATUS = {204, 304}


def _rewrite_cookie(cookie: str, local_is_secure: bool) -> str:
    """重写 Set-Cookie：去掉 Domain（绑定到本地域名）与 Secure（本地为 http 时）。

    保留 Path/Expires/Max-Age/SameSite 等其余属性。
    """
    parts = []
    for part in cookie.split(";"):
        name = part.strip().lower()
        if name.startswith("domain="):
            continue
        if not local_is_secure and name == "secure":
            continue
        parts.append(part)
    return ";".join(parts)


class U1S1ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "u1s1-proxy/1.0"

    upstream_scheme = "https"
    upstream_host = "u1s1.io"
    upstream_port = 443
    # 反代鉴权：由客户端工具注入；非空时转发请求自动带上 Authorization
    authorization = ""

    # ---------- 请求体读取（支持 Content-Length 与 chunked） ----------
    def _read_body(self) -> bytes:
        te = (self.headers.get("Transfer-Encoding") or "").lower()
        if te == "chunked":
            data = bytearray()
            while True:
                line = self.rfile.readline()
                if not line:
                    break
                size = int(line.split(b";", 1)[0].strip(), 16)
                if size == 0:
                    # 吃掉结尾的 trailer 头直到空行
                    while True:
                        t = self.rfile.readline()
                        if t in (b"\r\n", b"\n", b""):
                            break
                    break
                data += self.rfile.read(size)
                self.rfile.read(2)  # 吃掉 chunk 结尾的 CRLF
            return bytes(data)
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _local_base(self) -> str:
        """客户端视角的本地基址，用于重写 Location。"""
        host = self.headers.get("Host")
        if not host:
            addr = self.server.server_address
            host = f"{addr[0]}:{addr[1]}"
        return f"http://{host}"

    def _rewrite_location(self, location: str) -> str:
        """把上游域名改写为本地地址。"""
        if not location:
            return location
        if location.startswith("//"):
            location = self.upstream_scheme + ":" + location
        origin = f"{self.upstream_scheme}://{self.upstream_host}"
        if location.startswith(origin):
            return self._local_base() + location[len(origin):]
        return location

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

    # ---------- 统一转发逻辑 ----------
    def _forward(self) -> None:
        try:
            self._do_forward()
        except Exception as e:  # noqa: BLE001 - 记录异常并断开连接
            import traceback
            traceback.print_exc(file=sys.stderr)
            try:
                msg = f"反向代理上游请求失败: {e}".encode("utf-8")
                self.send_response_only(502, "Bad Gateway")
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(msg)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(msg)
            except Exception:
                pass

    def _do_forward(self) -> None:
        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(
            self.upstream_host, self.upstream_port, context=ctx, timeout=60
        )
        try:
            headers = self._build_upstream_headers()

            body = (
                self._read_body()
                if self.command in ("POST", "PUT", "PATCH", "DELETE")
                else None
            )

            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()

            self.send_response_only(resp.status, resp.reason)

            has_body = self.command != "HEAD" and resp.status not in _NO_BODY_STATUS
            local_is_secure = self.upstream_scheme == "https" and False

            for k, v in resp.getheaders():
                lk = k.lower()
                if lk in _STRIP_RESP:
                    continue
                if lk == "location":
                    v = self._rewrite_location(v)
                elif lk == "set-cookie":
                    v = _rewrite_cookie(v, local_is_secure)
                self.send_header(k, v)

            if has_body:
                self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            # 流式回传响应体（chunked 编码）
            if has_body:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(b"%X\r\n" % len(chunk))
                    self.wfile.write(chunk)
                    self.wfile.write(b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
        finally:
            conn.close()

    do_GET = _forward
    do_POST = _forward
    do_PUT = _forward
    do_PATCH = _forward
    do_DELETE = _forward
    do_HEAD = _forward
    do_OPTIONS = _forward

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main() -> None:
    ap = argparse.ArgumentParser(description="u1s1.io 本地反向代理")
    ap.add_argument("--host", default=DEFAULT_HOST, help="监听地址（默认 127.0.0.1）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口（默认 18080）")
    ap.add_argument(
        "--upstream", default=DEFAULT_UPSTREAM,
        help="上游地址（默认 https://u1s1.io）",
    )
    args = ap.parse_args()

    raw = args.upstream if "://" in args.upstream else "https://" + args.upstream
    u = urlsplit(raw)
    scheme = u.scheme or "https"
    host = u.hostname or "u1s1.io"
    port = u.port or (443 if scheme == "https" else 80)

    U1S1ProxyHandler.upstream_scheme = scheme
    U1S1ProxyHandler.upstream_host = host
    U1S1ProxyHandler.upstream_port = port

    httpd = ThreadingHTTPServer((args.host, args.port), U1S1ProxyHandler)
    print(f"u1s1 反向代理已启动: http://{args.host}:{args.port}  ->  {scheme}://{host}:{port}")
    print(f"访问控制台: http://{args.host}:{args.port}/dashboard#sec-usage")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
        httpd.server_close()


if __name__ == "__main__":
    main()
