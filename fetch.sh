#!/bin/sh
# 完整拉取一轮直播源（输出逐行带时间戳，容器时区 Asia/Shanghai 即北京时间）
cd /app
python3 -u /app/iptv.py 2>&1 | while IFS= read -r line; do
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$line"
done
