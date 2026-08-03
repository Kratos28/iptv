#!/bin/sh
set -e

mkdir -p /data

# 启动时立即拉取一次（后台执行，不阻塞订阅服务）
/app/fetch.sh >> /data/iptv.log 2>&1 &

# 定时任务：每天 6 次，每 4 小时一次（容器时区 Asia/Shanghai，即北京时间
# 00:07 / 04:07 / 08:07 / 12:07 / 16:07 / 20:07）。咪咕地址约 5 小时过期，间隔须留余量
printf '7 0,4,8,12,16,20 * * * /app/fetch.sh >> /data/iptv.log 2>&1\n' > /etc/crontabs/root
crond -l 2

# 对外提供订阅文件访问：http://<主机>:8080/iptv.txt
exec python3 -m http.server 8080 --directory /data
