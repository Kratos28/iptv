#!/usr/bin/env python3
"""对外提供订阅文件：http://<主机>:8111/iptv.txt

python -m http.server 返回的 text/plain 不带 charset，浏览器会猜错编码，
导致 UTF-8 中文显示成乱码，这里显式补上 charset=utf-8。
"""
import functools
import http.server


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".txt": "text/plain; charset=utf-8",
        ".json": "application/json; charset=utf-8",
    }


http.server.ThreadingHTTPServer(
    ("0.0.0.0", 8111),
    functools.partial(Handler, directory="/data"),
).serve_forever()
