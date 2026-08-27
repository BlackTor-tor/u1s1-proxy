"""u1s1.io 网站反向代理

把 u1s1.io 的 dashboard 等页面通过本机地址访问，供本地浏览器使用。
默认监听 http://127.0.0.1:8899，转发到 https://u1s1.io。

支持：
  - cookie / Set-Cookie 透传（登录态可用）
  - WebSocket 转发（实时用量等）
  - SSE / 流式响应透传
  - HTML 与重定向中的绝对地址改写（u1s1.io -> 本机地址），避免绕过反代
  - 尊重系统代理（trust_env），如需绕墙可配合系统代理使用

运行：
  python proxy.py                 # 监听 127.0.0.1:8899
  python proxy.py --port 9000     # 自定义端口
  python proxy.py --host 0.0.0.0  # 监听所有网卡（局域网可访问）

环境变量（可选）：
  U1S1_PROXY_HOST / U1S1_PROXY_PORT / U1S1_UPSTREAM / U1S1_UPSTREAM_HOST
"""
from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import urlsplit, urlunsplit

import httpx
import uvicorn
import websockets
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

log = logging.getLogger("u1s1_proxy")

# ── 配置（可用环境变量覆盖）────────────────────────────────────────────
PROXY_HOST = os.environ.get("U1S1_PROXY_HOST", "127.0.0.1")
PROXY_PORT = int(os.environ.get("U1S1_PROXY_PORT", "8899"))
UPSTREAM = os.environ.get("U1S1_UPSTREAM", "https://u1s1.io").rstrip("/")
UPSTREAM_HOST = os.environ.get("U1S1_UPSTREAM_HOST", urlsplit(UPSTREAM).netloc)

# 转发到上游时去掉的逐跳头（Host 由我们重写，其余由 httpx 处理）
_HOP_BY_HOP = {
    "host", "content-length", "connection", "keep-alive",
    "proxy-authenticate", "proxy-authorization", "te", "trailer",
    "transfer-encoding", "upgrade",
}

app = FastAPI(title="u1s1-proxy", docs_url=None, redoc_url=None)

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(60.0, connect=15.0),
            trust_env=True,  # 尊重系统代理（如需绕墙可配合系统代理）
        )
    return _client


def _local_base() -> str:
    return f"http://{PROXY_HOST}:{PROXY_PORT}"


def _build_upstream_url(path: str, raw_query: str) -> str:
    base = urlsplit(UPSTREAM)
    return urlunsplit((base.scheme, base.netloc, "/" + path, raw_query, ""))


def _build_ws_url(path: str, raw_query: str) -> str:
    base = urlsplit(UPSTREAM)
    scheme = "wss" if base.scheme == "https" else "ws"
    return urlunsplit((scheme, base.netloc, "/" + path, raw_query, ""))


def _forward_headers(headers) -> dict[str, str]:
    """构造转发给上游的请求头：去掉逐跳头，Host 重写为上游主机。

    Accept-Encoding 固定为 identity：让上游返回未压缩内容，
    避免 httpx 自动解压后仍残留 content-encoding 头导致下游解码错乱，
    也保证 HTML 地址改写能拿到明文。
    """
    out = {
        k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP
    }
    out["Host"] = UPSTREAM_HOST
    out["Accept-Encoding"] = "identity"
    return out


def _rewrite_url(text: str) -> str:
    """把响应体/头里的 u1s1.io 绝对地址改写为本机地址，避免绕过反代。"""
    local = _local_base()
    return (
        text.replace(UPSTREAM, local)
        .replace(f"//{UPSTREAM_HOST}", f"//{PROXY_HOST}:{PROXY_PORT}")
    )


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def http_proxy(request: Request, path: str):
    url = _build_upstream_url(path, request.url.query)
    headers = _forward_headers(request.headers)
    body = await request.body()

    upstream = await get_client().request(
        request.method, url, headers=headers, content=body or None,
    )

    resp_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP
    }
    # httpx 已自动解压，转发时去掉 content-encoding，避免下游重复解码
    resp_headers.pop("content-encoding", None)

    # 重定向：改写 Location，让浏览器继续走反代
    location = upstream.headers.get("location")
    if location:
        resp_headers["location"] = _rewrite_url(location)

    content_type = upstream.headers.get("content-type", "")
    is_html = "text/html" in content_type

    # HTML 整包读取并改写绝对地址；其余流式透传（SSE/大文件）
    if is_html:
        text = upstream.content.decode("utf-8", errors="replace")
        resp_headers.pop("content-length", None)
        return Response(
            content=_rewrite_url(text),
            status_code=upstream.status_code,
            headers=resp_headers,
        )

    resp_headers.pop("content-length", None)
    return StreamingResponse(
        upstream.aiter_bytes(),
        status_code=upstream.status_code,
        headers=resp_headers,
        background=BackgroundTask(upstream.aclose),
    )


@app.websocket("/{path:path}")
async def ws_proxy(websocket: WebSocket, path: str):
    await websocket.accept()
    url = _build_ws_url(path, websocket.query_params)
    headers = _forward_headers(websocket.headers)
    # 连接级握手头交给 websockets 库生成，不转发
    for key in (
        "sec-websocket-key", "sec-websocket-version",
        "sec-websocket-extensions", "sec-websocket-accept",
        "sec-websocket-protocol",
    ):
        headers.pop(key, None)
    subprotocols = websocket.headers.get("sec-websocket-protocol")
    try:
        async with websockets.connect(
            url,
            extra_headers=headers,
            subprotocols=[subprotocols] if subprotocols else None,
            max_size=None,
        ) as upstream:
            async def upstream_to_client():
                async for message in upstream:
                    if isinstance(message, str):
                        await websocket.send_text(message)
                    else:
                        await websocket.send_bytes(message)

            async def client_to_upstream():
                while True:
                    message = await websocket.receive()
                    msg_type = message.get("type")
                    if msg_type == "websocket.disconnect":
                        break
                    if message.get("text") is not None:
                        await upstream.send(message["text"])
                    elif message.get("bytes") is not None:
                        await upstream.send(message["bytes"])

            await asyncio.gather(upstream_to_client(), client_to_upstream())
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # 上游断开/握手失败等，正常结束即可
        log.debug("websocket 转发结束: %s", exc)
        try:
            await websocket.close()
        except Exception:
            pass


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="u1s1.io 反向代理")
    parser.add_argument("--host", default=PROXY_HOST, help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=PROXY_PORT, help="监听端口（默认 8899）")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log.info("u1s1-proxy 启动: http://%s:%s -> %s", args.host, args.port, UPSTREAM)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
