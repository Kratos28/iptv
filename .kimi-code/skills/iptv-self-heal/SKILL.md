---
name: iptv-self-heal
description: 维护本仓库的电视直播源——排查不能观看/不流畅的频道，实测验证候选地址并修复 iptv.py 中的源配置
type: prompt
whenToUse: 当用户要求排查/修复直播源、有频道不能观看或不流畅、更新补充频道地址，或定时任务告警需要自愈时
---

你是本仓库（电视直播源订阅）的维护 Agent。目标：保证 `iptv.txt` 订阅里的每个频道都能正常观看。

## 项目结构（先读懂再动手）

- `iptv.py`：唯一脚本，仅用 Python 标准库。流程：抓咪咕频道列表 → playurl 接口取带 ddCalcu 签名的 720p 地址 → 302 解析真实 CDN 地址 → 下载分片实测速度 → 写入前全量复验 → 生成 `iptv.txt` 和 `iptv_report.json`。生成的 `iptv.txt` 头部固定带「🕘️更新时间+时间」分组及同名时间条目（时间固定北京时间，条目 URL 借用已验证地址，保证播放器显示），属正常内容，不要当作异常数据删除。通过数低于 `IPTV_MIN_OK` 但 >0 时判定为限速：与旧订阅合并（通过的换新地址、其余沿用旧地址，频道数不减少）并正常退出；一个都没通过才退出码 1 触发告警。
- `iptv_report.json`：本轮验证报告，`failed` 数组记录未通过频道的 name/group/reason（gitignored，每次运行重新生成）。
- `EXTRA_CHANNELS`（iptv.py 内）：手工维护的补充频道候选地址列表，失效地址主要在这里修。目前含地方频道（广东民生）、港澳（凤凰中文台/凤凰资讯台/凤凰香港台、翡翠台/翡翠台4K）。
- `SUPPLEMENT_CHANNELS`（iptv.py 内）：从 iptv-org 公共源补全的央卫视频道，按 tvg-id 前缀匹配。
- `.github/workflows/update.yml`：定时任务（每天 4 次，北京时间 06:13 / 11:23 / 17:37 / 22:53）+ 云端 AI 自愈（Kimi CLI），非用户要求不要改。检查与自愈全部在云端运行，无本地定时任务。

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
- 保持仅标准库依赖，不要引入第三方包；环境变量（README 已精简，约定以此处为准）：`IPTV_OUTPUT`（订阅输出路径，默认 iptv.txt）/ `IPTV_REPORT`（报告路径，默认 iptv_report.json）/ `IPTV_WORKERS`（并发数，默认 8）/ `IPTV_MIN_OK`（通过数阈值，低于则不更新订阅，云端设为 50）/ `IPTV_SSL_VERIFY`（本机 TLS 拦截代理下设 0）。
- 提交信息用中文，遵循现有风格：`fix: ...` / `chore: ...`；未获用户同意不执行 git commit/push。
