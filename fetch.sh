#!/bin/sh
# 完整拉取一轮：先抓央视频备用源（失败不阻塞主流程），再跑主流程
cd /app
python3 /app/yangshipin.py || echo "[警告] 央视频抓取失败，本轮无央视频备用源"
python3 /app/iptv.py
