FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends cron tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/
# Playwright + Chromium：yangshipin.py 抓央视频备用源用（官方接口签名为
# WASM 计算，无法纯 HTTP 重放，只能用无头浏览器）
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY iptv.py yangshipin.py fetch.sh docker-entrypoint.sh /app/
RUN chmod +x /app/fetch.sh /app/docker-entrypoint.sh

ENV TZ=Asia/Shanghai \
    IPTV_OUTPUT=/data/iptv.txt \
    IPTV_REPORT=/data/iptv_report.json \
    IPTV_YANGSHIPIN=/data/yangshipin.json \
    YSP_OUTPUT=/data/yangshipin.json

VOLUME /data
EXPOSE 8080

ENTRYPOINT ["/app/docker-entrypoint.sh"]
