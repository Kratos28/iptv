#!/bin/sh
set -e

mkdir -p /data

# 启动时立即拉取一次（后台执行，不阻塞订阅服务）
/app/fetch.sh >> /data/iptv.log 2>&1 &

# 拉取日志同步到容器标准输出，docker logs 可直接查看进度
tail -F /data/iptv.log &

# 定时任务：每天 12 次，每 2 小时一次（容器时区 Asia/Shanghai，即北京时间
# 00:07 / 02:07 / ... / 22:07）。咪咕地址寿命实测正好 3 小时（过期返回 HTTP 605），2 小时间隔留足余量
printf '7 */2 * * * /app/fetch.sh >> /data/iptv.log 2>&1\n' > /etc/crontabs/root
crond -l 2

# 对外提供订阅文件访问：http://<主机>:8111/iptv.txt
# 用 serve.py 而非 python -m http.server，后者不带 charset 会导致浏览器中文乱码
exec python3 /app/serve.py
