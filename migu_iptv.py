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
import re
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

# 分组名映射为 kratos.320.io/iptv.txt 风格
GROUP_MAP = {
    "央视": "央视频道",
    "卫视": "卫视频道",
}

# 咪咕没有的央卫视，从公共源(iptv-org 中国列表)补全，键为规范频道名，值为 tvg-id 匹配前缀
SUPPLEMENT_URL = os.environ.get("IPTV_SUPPLEMENT_URL",
                                "https://iptv-org.github.io/iptv/countries/cn.m3u")
SUPPLEMENT_CHANNELS = {
    "CCTV16奥林匹克": ["CCTV16.cn"],
    "湖南卫视": ["HunanTV.cn"],
    "山东卫视": ["ShandongSatelliteTV.cn"],
    "山西卫视": ["ShanxiTV.cn"],
    "深圳卫视": ["ShenzhenSatelliteTV.cn"],
    "四川卫视": ["SichuanSatelliteTV.cn"],
    "天津卫视": ["TianjinTV.cn"],
    "云南卫视": ["YunnanSatelliteTV.cn"],
    "甘肃卫视": ["GansuTV.cn"],
    "广西卫视": ["GuangxiTV.cn"],
    "贵州卫视": ["GuizhouTV.cn"],
    "西藏卫视": ["XizangTVTibetan.cn"],
    "新疆卫视": ["XinjiangTV1.cn"],
}

# 固定补充频道（公共源里找不到、需手工维护地址的），值为候选地址列表
# 同样每次运行时实测验证，通过才写入。分组取键名对应的组。
EXTRA_CHANNELS = {
    "广东频道": {
        "广东民生": [
            "https://16g4q89264.vicp.fun/udp/239.10.0.123:1025",
            "https://stream1.freetv.fun/yan-dong-min-sheng-35.ctv",
            "https://stream1.freetv.fun/yan-dong-min-sheng-16.ctv",
        ],
    },
}

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
def http_get_partial(url: str, max_bytes: int, timeout: int) -> tuple:
    """最多读取 max_bytes 字节，返回 (数据, 耗时秒)。用于无尽流的测速。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    t0 = time.time()
    buf = bytearray()
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
        while len(buf) < max_bytes:
            if time.time() - t0 > timeout:
                break
            chunk = r.read(min(65536, max_bytes - len(buf)))
            if not chunk:
                break
            buf.extend(chunk)
    return bytes(buf), time.time() - t0


def check_stream(url: str) -> tuple:
    """
    验证直播源：m3u8 下载首个分片测速；TS 裸流直接采样测速。
    返回 (是否流畅, 描述信息)
    """
    try:
        head, elapsed = http_get_partial(url, 2048, HTTP_TIMEOUT)
    except Exception as e:
        return False, f"播放列表获取失败: {e}"

    # TS 裸流（udpxy 组播转 HTTP 等）：采样 1MB 测速
    if head[:1] == b"\x47":
        try:
            data, elapsed = http_get_partial(url, 1024 * 1024, SEG_TIMEOUT)
        except Exception as e:
            if not head:
                return False, f"TS 流读取失败: {e}"
            data, elapsed = head, max(elapsed, 0.01)
        if len(data) < 100 * 1024:
            return False, f"TS 流数据不足({len(data)}B)"
        speed_bps = len(data) * 8 / max(elapsed, 0.01)
        info = f"TS流 实测{speed_bps/1e6:.1f}Mbps"
        # 直播 TS 流按实时码率下发，持续 >=1.5Mbps 即无卡顿
        ok = speed_bps >= 1_500_000
        return ok, info + (" 流畅" if ok else " 速度不足")

    playlist = head.decode("utf-8", "replace")
    if "#EXTM3U" not in playlist:
        return False, "不是有效的 m3u8"
    # m3u8 内容可能未读全（head 只有 2KB），重新完整拉取
    try:
        playlist = http_get(url, {"User-Agent": UA}).decode("utf-8", "replace")
    except Exception as e:
        return False, f"播放列表获取失败: {e}"

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


# ---------------- 频道名规范化与补全 ----------------
def normalize_name(name: str) -> str:
    """CCTV1综合 -> CCTV1，与 kratos.320.io/iptv.txt 命名风格对齐"""
    m = re.match(r"^(CCTV\d+\+?)", name)
    if m:
        return m.group(1)
    return name.strip()


def group_name(cate_name: str) -> str:
    if cate_name in GROUP_MAP:
        return GROUP_MAP[cate_name]
    return cate_name if cate_name.endswith("频道") else cate_name + "频道"


def fetch_supplement_candidates() -> dict:
    """从公共 m3u 源提取补全频道的候选地址，返回 {规范名: [url, ...]}"""
    try:
        text = http_get(SUPPLEMENT_URL, {"User-Agent": UA}, timeout=20).decode("utf-8", "replace")
    except Exception as e:
        print(f"[警告] 补全源获取失败: {e}")
        return {}
    candidates = {}
    for block in text.split("#EXTINF"):
        lines = block.splitlines()
        if len(lines) < 2:
            continue
        url = next((l.strip() for l in lines[1:] if l.strip() and not l.startswith("#")), "")
        if not url.startswith("http"):
            continue
        id_m = re.search(r'tvg-id="([^"]*)"', lines[0])
        tvg_id = id_m.group(1) if id_m else ""
        for canon, prefixes in SUPPLEMENT_CHANNELS.items():
            if any(tvg_id.startswith(p) for p in prefixes):
                candidates.setdefault(canon, [])
                if url not in candidates[canon]:
                    candidates[canon].append(url)
    return candidates


def process_supplement(name: str, urls: list) -> tuple:
    """依次验证候选地址，返回 (url 或 None, 描述)"""
    info = "无候选地址"
    for url in urls[:3]:
        ok, info = check_stream(url)
        if ok:
            return url, info
    return None, info


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

    # 组内按规范名去重，保留通过验证的频道
    groups = {}  # 组名 -> [(规范名, url)]
    for cate in categories:
        gname = group_name(cate["name"])
        for ch in cate["dataList"]:
            url = results.get(ch["pID"])
            if not url:
                continue
            name = normalize_name(ch["name"])
            groups.setdefault(gname, {})
            if name not in groups[gname]:
                groups[gname][name] = url

    # 补全咪咕缺失的央卫视频道（公共源，同样做流畅度验证）
    existing = {n for g in groups.values() for n in g}
    missing = {n: p for n, p in SUPPLEMENT_CHANNELS.items() if n not in existing}
    if missing:
        print(f"\n开始补全 {len(missing)} 个咪咕缺失频道...")
        candidates = fetch_supplement_candidates()
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futs = {n: pool.submit(process_supplement, n, candidates.get(n, [])) for n in missing}
            for n, fut in futs.items():
                url, info = fut.result()
                if url:
                    gname = "央视频道" if n.startswith(("CCTV", "CGTN")) else "卫视频道"
                    groups.setdefault(gname, {})[n] = url
                    print(f"[补全 OK] {n} - {info}")
                else:
                    print(f"[补全 FAIL] {n} - {info}")

    # 固定补充频道（手工维护地址，同样实测验证）
    extra_tasks = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for gname, chans in EXTRA_CHANNELS.items():
            for name, urls in chans.items():
                if name not in existing:
                    extra_tasks[(gname, name)] = pool.submit(process_supplement, name, urls)
        for (gname, name), fut in extra_tasks.items():
            url, info = fut.result()
            if url:
                groups.setdefault(gname, {})[name] = url
                print(f"[补充 OK] {name} - {info}")
            else:
                print(f"[补充 FAIL] {name} - {info}")
    # 央视频道按 CCTV 编号排序，其余按名称排序
    def sort_key(item):
        m = re.match(r"^CCTV(\d+)", item[0])
        return (0, int(m.group(1))) if m else (1, item[0])

    lines = []
    ok_count = 0
    for gname, chans in groups.items():
        if not chans:
            continue
        lines.append(f"{gname},#genre#")
        for name, url in sorted(chans.items(), key=sort_key):
            lines.append(f"{name},{url}")
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
