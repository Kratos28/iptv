https://raw.githubusercontent.com/Kratos28/iptv/main/iptv.txt

## Docker 部署（自托管订阅服务）

容器启动后立即拉取一次直播源，之后每天 6 次（每 4 小时，北京时间
00:07 / 04:07 / 08:07 / 12:07 / 16:07 / 20:07）自动更新，
并通过 8080 端口对外提供订阅文件。

每轮先由 `yangshipin.py`（内置无头 Chromium）抓取央视频官方源作为
央视/卫视的优先源（同名双源：央视频在前、咪咕兜底），再运行 `iptv.py`
主流程（咪咕源为主），并聚合 Guovin/iptv-api 的每日聚合输出补充更多频道。

```bash
# 构建镜像（含 Chromium，约 700MB，构建需几分钟）
docker build -t iptv .

# 运行（数据持久化到宿主机 ./data，订阅端口 8080）
docker run -d --name iptv --restart unless-stopped \
  -p 8080:8080 \
  -v $(pwd)/data:/data \
  iptv
```

部署后订阅地址：`http://<服务器IP>:8080/iptv.txt`

常用命令：

```bash
docker logs iptv                        # 查看容器日志
docker exec iptv tail -f /data/iptv.log # 查看拉取/验证日志
docker exec iptv cat /data/iptv_report.json  # 查看最近一轮验证报告
docker restart iptv                     # 立即重新拉取（重启即触发）
```

注意：咪咕 playurl 接口对境外 IP 地域封锁，请部署在中国大陆网络环境的机器上。

## 本地运行（不用 Docker）

```bash
# 主流程仅需 Python 3 标准库
python3 iptv.py

# 央视频备用源（可选）：需安装 Playwright
pip install -r requirements.txt
playwright install chromium
python3 yangshipin.py   # 生成 yangshipin.json，之后 iptv.py 会自动使用
```
