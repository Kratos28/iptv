#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
咪咕视频电视直播源抓取脚本

功能：
1. 抓取咪咕视频 App 的电视直播频道列表
2. 为每个频道解析出 720p m3u8 直播地址（免登录，ddCalcu 签名）
3. 实际下载分片测速，验证直播源能否流畅观看
4. 生成 txt 格式订阅文件（分组,#genre# / 频道名,URL）

仅使用 Python 标准库，无需安装依赖。
本机若处于 TLS 拦截代理环境，可用 IPTV_SSL_VERIFY=0 关闭证书校验。
"""

import concurrent.futures
import datetime
import hashlib
import json
import os
import random
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------- 配置 ----------------
OUTPUT_FILE = os.environ.get("IPTV_OUTPUT", "iptv.txt")
MAX_WORKERS = int(os.environ.get("IPTV_WORKERS", "8"))
MIN_OK = int(os.environ.get("IPTV_MIN_OK", "1"))  # 通过数低于此值则不更新订阅文件
HTTP_TIMEOUT = 10
SEG_TIMEOUT = 15          # 分片下载超时（秒）
RETRY = 2                 # 每个频道获取播放地址的重试次数
MIN_SPEED_RATIO = 1.0     # 实测速度 >= 码率 * 此系数 判定为“流畅”
UA = "okhttp/4.9.0"

CATE_LIST_URL = "https://program-sc.miguvideo.com/live/v2/tv-data/1ff892f2b5ab4a79be6e25b69d2f5d05"
TV_DATA_URL = "https://program-sc.miguvideo.com/live/v2/tv-data/"
PLAYURL_API = "https://play.miguvideo.com/playurl/v1/play/playurl"

APP_VERSION = "2600034600"
APP_VERSION_ID = APP_VERSION + "-99000-201600010010028"
# 这两个频道带 appCode 头会导致无法回放（沿用上游项目的特殊处理）
NO_APPCODE_PIDS = {"641886683", "641886773"}

SSL_CTX = ssl.create_default_context()
if os.environ.get("IPTV_SSL_VERIFY", "1") == "0":
    SSL_CTX.check_hostname = False
    SSL_CTX.verify_mode = ssl.CERT_NONE


def md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def http_get(url: str, headers: dict = None, timeout: int = HTTP_TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
        return r.read()


def get_json(url: str, headers: dict = None) -> dict:
    return json.loads(http_get(url, headers).decode("utf-8"))


# ---------------- 频道列表 ----------------
def fetch_categories() -> list:
    """返回 [{name, dataList:[{name,pID}]}]，央视排第一，去除“热门”"""
    resp = get_json(CATE_LIST_URL)
    live_list = [c for c in resp["body"]["liveList"] if c["name"] != "热门"]
    live_list.sort(key=lambda c: 0 if c["name"] == "央视" else 1)
    for cate in live_list:
        try:
            resp = get_json(TV_DATA_URL + cate["vomsID"])
            cate["dataList"] = resp["body"]["dataList"] if resp and resp.get("body") else []
        except Exception as e:
            print(f"[警告] 分类 {cate['name']} 获取失败: {e}")
            cate["dataList"] = []
    # 跨分类去重（按 pID）
    seen = set()
    for cate in live_list:
        uniq = []
        for ch in cate["dataList"]:
            pid = ch.get("pID")
            if pid and pid not in seen and ch.get("name"):
                seen.add(pid)
                uniq.append(ch)
        cate["dataList"] = uniq
    return [c for c in live_list if c["dataList"]]


# ---------------- 播放地址 ----------------
def _ddcalcu_720p(pu_data: str, program_id: str) -> str:
    """旧版 720p ddCalcu 签名算法"""
    keys = "cdabyzwxkl"
    date_str = datetime.datetime.now().strftime("%Y%m%d")
    out = []
    for i in range(len(pu_data) // 2):
        out.append(pu_data[len(pu_data) - i - 1])
        out.append(pu_data[i])
        if i == 1:
            out.append("v")
        elif i == 2:
            out.append(keys[int(date_str[2])])
        elif i == 3:
            out.append(keys[int(program_id[6])])
        elif i == 4:
            out.append("a")
    return "".join(out)


def get_play_url(pid: str, rate_type: int = 3) -> str:
    """通过安卓端 playurl 接口获取带 ddCalcu 签名的播放地址"""
    ts = str(round(time.time() * 1000))
    salt = str(random.randint(0, 999999)).zfill(6) + "25"
    sign = md5(md5(ts + pid + APP_VERSION[:8]) + "2cac4f2c6c3346a5b34e085725ef7e33migu" + salt[:4])
    headers = {
        "AppVersion": APP_VERSION,
        "TerminalId": "android",
        "X-UP-CLIENT-CHANNEL-ID": APP_VERSION_ID,
        "ClientId": md5(ts),
        "User-Agent": UA,
    }
    if pid not in NO_APPCODE_PIDS:
        headers["appCode"] = "miguvideo_default_android"
    api = (f"{PLAYURL_API}?sign={sign}&rateType={rate_type}&contId={pid}"
           f"&timestamp={ts}&salt={salt}&flvEnable=true&super4k=true")
    resp = get_json(api, headers)
    body = resp.get("body") or {}
    url = (body.get("urlInfo") or {}).get("url")
    real_pid = (body.get("content") or {}).get("contId")
    if not url or not real_pid or "&puData=" not in url:
        return ""
    pu_data = url.split("&puData=", 1)[1]
    return f"{url}&ddCalcu={_ddcalcu_720p(pu_data, real_pid)}&sv=10004&ct=android"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=SSL_CTX))


def resolve_final_url(url: str) -> str:
    """跟随 302 直到拿到真实 CDN 地址（bofang 开头为无效调度地址，视为失败）"""
    loc = url
    for _ in range(6):
        try:
            _OPENER.open(urllib.request.Request(loc, headers={"User-Agent": UA}), timeout=HTTP_TIMEOUT)
            return loc  # 直接 200
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                loc = e.headers["Location"]
                if not loc.startswith("http://bofang"):
                    return loc
            else:
                return ""
    return ""


# ---------------- 流畅度验证 ----------------
def check_stream(url: str) -> tuple:
    """
    验证直播源：拉取 m3u8 -> 下载首个分片测速。
    返回 (是否流畅, 描述信息)
    """
    try:
        playlist = http_get(url, {"User-Agent": UA}).decode("utf-8", "replace")
    except Exception as e:
        return False, f"播放列表获取失败: {e}"
    if "#EXTM3U" not in playlist:
        return False, "不是有效的 m3u8"

    # 若是多级列表，选码率最高的一条
    bandwidth = 0
    target = url
    if "#EXT-X-STREAM-INF" in playlist:
        best_bw = 0
        lines = playlist.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF"):
                bw = 0
                for part in line.split(","):
                    if part.startswith("BANDWIDTH="):
                        bw = int(part.split("=", 1)[1])
                if i + 1 < len(lines) and bw >= best_bw:
                    best_bw = bw
                    target = urllib.parse.urljoin(url, lines[i + 1].strip())
        bandwidth = best_bw or 2_000_000
        try:
            playlist = http_get(target, {"User-Agent": UA}).decode("utf-8", "replace")
        except Exception as e:
            return False, f"子播放列表获取失败: {e}"
    else:
        bandwidth = 2_000_000  # 无码率信息时按 2Mbps 估算

    segments = [l.strip() for l in playlist.splitlines()
                if l.strip() and not l.startswith("#")]
    if not segments:
        return False, "播放列表无分片（频道可能未开播）"

    seg_url = urllib.parse.urljoin(target, segments[0])
    try:
        t0 = time.time()
        data = http_get(seg_url, {"User-Agent": UA}, timeout=SEG_TIMEOUT)
        elapsed = time.time() - t0
    except Exception as e:
        return False, f"分片下载失败: {e}"
    if len(data) < 10 * 1024:
        return False, f"分片过小({len(data)}B)"

    speed_bps = len(data) * 8 / max(elapsed, 0.01)
    ratio = speed_bps / bandwidth
    info = f"码率{bandwidth/1e6:.1f}Mbps 实测{speed_bps/1e6:.1f}Mbps"
    if ratio >= MIN_SPEED_RATIO:
        return True, info + " 流畅"
    return False, info + " 速度不足"


# ---------------- 主流程 ----------------
def process_channel(name: str, pid: str) -> tuple:
    """返回 (url 或 None, 描述)"""
    for attempt in range(1, RETRY + 2):
        try:
            play_url = get_play_url(pid)
            if not play_url:
                time.sleep(0.2)
                continue
            final_url = resolve_final_url(play_url)
            if not final_url:
                time.sleep(0.2)
                continue
            ok, info = check_stream(final_url)
            if ok:
                return final_url, info
            # 播放列表无分片多为未开播，重试无意义
            if "未开播" in info:
                return None, info
        except Exception as e:
            info = str(e)
        time.sleep(0.2)
    return None, locals().get("info", "获取播放地址失败")


def main():
    started = time.time()
    print("正在获取频道列表...")
    categories = fetch_categories()
    total = sum(len(c["dataList"]) for c in categories)
    print(f"共 {len(categories)} 个分类, {total} 个频道，开始解析并验证...")

    results = {}  # pid -> url or None
    tasks = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for cate in categories:
            for ch in cate["dataList"]:
                tasks.append((ch, pool.submit(process_channel, ch["name"], ch["pID"])))
        done = 0
        for ch, fut in tasks:
            url, info = fut.result()
            results[ch["pID"]] = url
            done += 1
            mark = "OK" if url else "FAIL"
            print(f"[{done}/{total}] [{mark}] {ch['name']} - {info}")

    lines = []
    ok_count = 0
    for cate in categories:
        valid = [ch for ch in cate["dataList"] if results.get(ch["pID"])]
        if not valid:
            continue
        lines.append(f"{cate['name']},#genre#")
        for ch in valid:
            lines.append(f"{ch['name']},{results[ch['pID']]}")
            ok_count += 1
    lines.append("")

    print(f"\n完成: {ok_count}/{total} 个频道通过验证，耗时 {time.time()-started:.0f}s")
    if ok_count < MIN_OK:
        print(f"[错误] 通过数 {ok_count} 低于阈值 IPTV_MIN_OK={MIN_OK}，"
              "可能处于被限速的网络环境，保留原订阅文件不更新")
        sys.exit(1)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"订阅文件已写入 {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
