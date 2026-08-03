FROM python:3.13-alpine

# tzdata 提供 Asia/Shanghai 时区（定时任务按北京时间执行）
RUN apk add --no-cache tzdata

WORKDIR /app
COPY iptv.py fetch.sh docker-entrypoint.sh /app/
RUN chmod +x /app/fetch.sh /app/docker-entrypoint.sh

ENV TZ=Asia/Shanghai \
    IPTV_OUTPUT=/data/iptv.txt \
    IPTV_REPORT=/data/iptv_report.json

VOLUME /data
EXPOSE 8080

ENTRYPOINT ["/app/docker-entrypoint.sh"]
