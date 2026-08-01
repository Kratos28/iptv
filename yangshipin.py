#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""央视频直播地址抓取（Playwright 无头浏览器）

央视频官方取流接口（player-api.yangshipin.cn）的签名由 WASM 计算，
一次性有效，无法用纯 HTTP 重放。这里用无头 Chromium 打开电视直播页，
从网络请求中截取真实 m3u8 地址。实测这类地址（hlslive-tx-cdn.ysp.cctv.cn
的 vkey 路径）不带签名参数也可播放，有效期数小时以上。

输出 yangshipin.json：{频道名: m3u8 地址}，供 iptv.py 用作央视/卫视备用源。
仅本脚本需要 playwright（pip install playwright && playwright install chromium），
主脚本 iptv.py 仍只用标准库。

环境变量：
  YSP_OUTPUT   输出路径（默认 yangshipin.json）
  YSP_WORKERS  并发页面数（默认 4）
  IPTV_SSL_VERIFY=0  关闭 TLS 校验（本机 TLS 拦截代理环境）
"""

import asyncio
import concurrent.futures
import json
import os
import re
import ssl
import urllib.request

CHANNEL_LIST_URL = "https://capi.yangshipin.cn/api/oms/pc/page/PG00000004?357109521"
TV_HOME = "https://www.yangshipin.cn/tv/home?pid={pid}"
OUTPUT = os.environ.get("YSP_OUTPUT", "yangshipin.json")
CONCURRENCY = int(os.environ.get("YSP_WORKERS", "4"))
PAGE_TIMEOUT = 25  # 单频道等待 m3u8 请求的超时（秒）
HTTP_TIMEOUT = 10

SSL_CTX = ssl.create_default_context()
if os.environ.get("IPTV_SSL_VERIFY", "1") == "0":
    SSL_CTX.check_hostname = False
    SSL_CTX.verify_mode = ssl.CERT_NONE


def fetch_channel_map() -> dict:
    """从央视频 oms 接口（protobuf 编码）解析 {频道名: pid}，只要央视和卫视"""
    raw = urllib.request.urlopen(CHANNEL_LIST_URL, timeout=15, context=SSL_CTX).read()
    channels = {}
    # protobuf 字符串字段：0x12 <len> <name> ... 0x22 0x09 <9位pid>
    for m in re.finditer(rb'\x12(.)([\x20-\x7e\xc0-\xff][^\x00-\x1f]*?)"\t(\d{9})', raw, re.S):
        if len(m.group(2)) != m.group(1)[0]:
            continue
        try:
            name = m.group(2).decode("utf-8")
        except UnicodeDecodeError:
            continue
        if name.startswith(("CCTV", "CGTN")) or name.endswith("卫视"):
            channels[name] = m.group(3).decode()
    return channels


async def grab_one(browser, name: str, pid: str, sem) -> tuple:
    """打开直播页，截取播放器请求的 m3u8 地址（去掉签名参数）"""
    async with sem:
        page = await browser.new_page()
        try:
            async with page.expect_request(
                    lambda r: "_web.m3u8" in r.url,
                    timeout=PAGE_TIMEOUT * 1000) as req_info:
                await page.goto(TV_HOME.format(pid=pid),
                                wait_until="domcontentloaded", timeout=30000)
            req = await req_info.value
            return name, req.url.split("?")[0], ""
        except Exception as e:
            return name, None, type(e).__name__
        finally:
            await page.close()


def verify(name: str, url: str) -> tuple:
    """确认地址能取到 m3u8 播放列表"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT, context=SSL_CTX) as r:
            head = r.read(2048)
        return name, url, b"#EXTM3U" in head
    except Exception:
        return name, url, False


async def async_main(channels: dict) -> dict:
    from playwright.async_api import async_playwright
    results = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        sem = asyncio.Semaphore(CONCURRENCY)
        tasks = [grab_one(browser, n, pid, sem) for n, pid in channels.items()]
        for coro in asyncio.as_completed(tasks):
            name, url, err = await coro
            if url:
                results[name] = url
                print(f"[OK] {name}")
            else:
                print(f"[FAIL] {name} - {err}")
        await browser.close()
    return results


def main():
    channels = fetch_channel_map()
    print(f"央视频频道清单 {len(channels)} 个，开始抓取...")
    results = asyncio.run(async_main(channels))

    # 抓取后逐个验证可取流（并发）
    ok = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        for name, url, good in pool.map(lambda kv: verify(*kv), results.items()):
            if good:
                ok[name] = url
            else:
                print(f"[验证 FAIL] {name}")
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(ok, f, ensure_ascii=False, indent=2)
    print(f"完成: {len(ok)}/{len(channels)} 个频道，已写入 {OUTPUT}")


if __name__ == "__main__":
    main()
