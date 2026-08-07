https://raw.githubusercontent.com/Kratos28/iptv/main/iptv.txt

## Docker 部署（自托管订阅服务）

容器启动后立即拉取一次直播源，之后每天 12 次（每 2 小时，北京时间
00:07 / 02:07 / ... / 22:07，主源地址寿命实测约 3 小时，2 小时间隔
留足余量）自动更新，并通过 8111 端口对外提供订阅文件。

每轮运行 `iptv.py` 主流程，并聚合 Guovin/iptv-api 的
每日聚合输出补充更多频道；同时从外部源收集已有频道的更多地址
（320p 及以上且实测流畅即收录），其中 720p 及以上的高清地址
按分辨率从高到低排在频道源首位（同名多行），标清地址列在主地址之后。

```bash
# 构建镜像
docker build -t iptv .

# 运行（数据持久化到宿主机 ./data，订阅端口 8111）
docker run -d --name iptv --restart unless-stopped \
  -p 8111:8111 \
  -v $(pwd)/data:/data \
  iptv
```

部署后订阅地址：`http://<服务器IP>:8111/iptv.txt`

常用命令：

```bash
docker logs iptv                        # 查看容器日志
docker exec iptv tail -f /data/iptv.log # 查看拉取/验证日志
docker exec iptv cat /data/iptv_report.json  # 查看最近一轮验证报告
docker restart iptv                     # 立即重新拉取（重启即触发）
```

注意：主源接口对境外 IP 地域封锁，请部署在中国大陆网络环境的机器上。

## 本地运行（不用 Docker）

```bash
# 仅需 Python 3 标准库
python3 iptv.py
```
