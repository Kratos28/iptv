---
name: iptv-self-heal
description: 维护本仓库的电视直播源——排查不能观看/不流畅的频道，实测验证候选地址并修复 iptv.py 中的源配置
type: prompt
whenToUse: 当用户要求排查/修复直播源、有频道不能观看或不流畅、更新补充频道地址，或定时任务告警需要自愈时
---

你是本仓库（电视直播源订阅）的维护 Agent。目标：保证 `iptv.txt` 订阅里的每个频道都能正常观看。

## 项目结构（先读懂再动手）

- `iptv.py`：唯一主脚本，仅用 Python 标准库。流程：抓咪咕频道列表 → playurl 接口取带 ddCalcu 签名的 720p 地址 → 302 解析真实 CDN 地址 → 下载分片实测速度 → 写入前全量复验 → 生成 `iptv.txt` 和 `iptv_report.json`。生成的 `iptv.txt` 头部固定带「🕘️更新时间+时间」分组及同名时间条目（时间固定北京时间，条目 URL 借用已验证地址，保证播放器显示），属正常内容，不要当作异常数据删除。通过数低于 `IPTV_MIN_OK` 但 >0 时判定为限速：与旧订阅合并（实测通过的换新地址；测速未过但解析出新地址的也换新地址——咪咕 CDN 地址约 5 小时过期，沿用旧地址必 403；仅完全没解析出地址的沿用旧地址，频道数不减少）并正常退出；一个都没通过才退出码 1 触发告警。**注意：咪咕 playurl 接口已封锁境外 IP（403 版权问题），GitHub 云端运行取不到咪咕地址，只能靠合并续命；正式部署走 Docker（国内网络）。**
- `yangshipin.py`：央视频源抓取（唯一非标准库依赖：Playwright + Chromium）。央视频官方接口签名由 WASM 计算、一次性有效，无法纯 HTTP 重放，故用无头浏览器打开直播页截取 m3u8（vkey 路径，去签名参数可播，有效期约 4 小时）。央视频 CDN 按抓取地分发（境外会拿到 outlivecloud，国内无法播放），vkey 与 CDN 无关（换 host 仍可播），脚本统一改写 host 到国内腾讯 CDN（hlslive-tx-cdn.ysp.cctv.cn，http 兼容老设备）。输出 `yangshipin.json`（gitignored），`iptv.py` 读取后验证（能播即可免测速）：既补咪咕/公共源都缺的央视、卫视频道，也给已有频道追加同名央视频源（央视频优先、咪咕兜底，同名多源供 APTV 等播放器自动切换；合并路径按 ysp.cctv.cn host 识别央视频行单独换新）。频道清单来自 `capi.yangshipin.cn/api/oms/pc/page/PG00000004`（protobuf，正则解析名字+9 位 pid）。
- `Dockerfile` / `docker-entrypoint.sh` / `fetch.sh`：自托管部署（python:3.13-slim + cron + Chromium）。启动即拉取一轮，之后每天 6 次（每 4 小时，北京时间），8080 端口提供 `iptv.txt`。
- `iptv_report.json`：本轮验证报告，`failed` 数组记录未通过频道的 name/group/reason（gitignored，每次运行重新生成）。
- 聚合源（iptv.py 内 `AGGREGATE_URLS`，默认 Guovin/iptv-api 的每日聚合输出 result.m3u，可用 `IPTV_AGGREGATE_URLS` 覆盖或置空关闭）：跳过已有频道，其余按同一套流畅度标准实测后写入（央/卫/地方/港澳并入对应分组，体育/电影/动画等保留原分组名；每频道最多试 2 个候选）。聚合源全是第三方中继，稳定性一般，失效属常态，不用逐个修。
- `EXTRA_CHANNELS`（iptv.py 内）：手工维护的补充频道候选地址列表，失效地址主要在这里修。目前含地方频道（广东民生）、港澳（凤凰中文台/凤凰资讯台/凤凰香港台、翡翠台/翡翠台4K）。
- `SUPPLEMENT_CHANNELS`（iptv.py 内）：从 iptv-org 公共源补全的央卫视频道，按 tvg-id 前缀匹配。
- `.github/workflows/update.yml`：定时任务（每天 6 次，间隔 4 小时——咪咕地址约 5 小时、央视频约 4 小时过期；北京时间 00:07 / 04:13 / 08:19 / 12:25 / 16:31 / 20:37）+ 云端 AI 自愈（Kimi CLI），非用户要求不要改。云端每轮先跑 yangshipin.py 再跑 iptv.py（`IPTV_MIN_OK=1000` 使云端始终走合并路径）：央视频接口不封境外，云端可刷新央视频源和聚合源频道；咪咕接口封境外，咪咕分组在云端只能沿用旧地址（靠 Docker 国内部署解决）。

## 排查流程

1. 本机跑一次：`IPTV_SSL_VERIFY=0 python3 iptv.py`（本机有 TLS 拦截代理，必须加 `IPTV_SSL_VERIFY=0`），或直接读已有的 `iptv_report.json`。
2. 判断失败类型：
   - reason 含「未开播」→ 正常停播，不处理。
   - 大面积失败/速度不足 → 网络限速，非源故障，不改代码。
   - 个别频道打不开、403/404/超时 → 源地址失效，进入修复流程。
   - 频道列表获取失败、playurl 报错、ddCalcu 失效 → 源站接口/算法变更，参考 https://github.com/develop202/migu_video 最新源码修复签名逻辑。
3. 修复失效地址：
   - 候选来源：`https://iptv-org.github.io/iptv/countries/cn.m3u`（按 tvg-id 或频道名匹配）、`https://live.fanmingming.cn/tv/m3u/ipv6.m3u`。
   - 每个候选地址必须用 `python3 iptv.py --check "URL"` 实测，输出 `OK` 才可采用（与主流程同一套流畅度标准）。
   - 失效旧地址从列表移除，验证通过的新地址追加进 `EXTRA_CHANNELS` 对应分组；也可给反复失败的咪咕频道加公共源备用地址（主源成功时备用源不会被使用）。
4. 验证修复：重新完整运行 `python3 iptv.py`，确认目标频道恢复且「复验完成」一行无异常剔除。

## 约束

- 只改 `iptv.py`（通常是 `EXTRA_CHANNELS` / `SUPPLEMENT_CHANNELS` 配置）和重新生成 `iptv.txt`；不改工作流文件，除非用户明确要求。
- 保持仅标准库依赖，不要引入第三方包；环境变量（README 已精简，约定以此处为准）：`IPTV_OUTPUT`（订阅输出路径，默认 iptv.txt）/ `IPTV_REPORT`（报告路径，默认 iptv_report.json）/ `IPTV_WORKERS`（并发数，默认 8）/ `IPTV_MIN_OK`（通过数阈值，默认 50，低于则与旧订阅合并而非覆盖）/ `IPTV_SSL_VERIFY`（本机 TLS 拦截代理下设 0）。
- 提交信息用中文，遵循现有风格：`fix: ...` / `chore: ...`；未获用户同意不执行 git commit/push。
