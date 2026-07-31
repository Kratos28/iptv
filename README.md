# 电视直播源订阅

自动抓取 的电视直播源（央视、卫视、地方台等），逐个实测验证播放流畅度，生成 txt 格式订阅文件。GitHub Actions 每天定时更新 3 次（北京时间 08:03 / 13:17 / 19:41）。

## 订阅地址

```
https://raw.githubusercontent.com/Kratos28/migu-iptv/main/iptv.txt
```

格式与常见 IPTV 订阅一致，可直接导入 TVBox、DIYP、百川影音等播放器：

```
央视频道,#genre#
CCTV1,http://xxx.m3u8?...
```

分组包括：央视频道、卫视频道、地方频道、体育频道、影视频道、新闻频道、教育频道、综艺频道、少儿频道、纪实频道、熊猫频道。

## 本地运行

仅依赖 Python 3 标准库，无需安装任何第三方包：

```bash
python3 migu_iptv.py
```

生成的 `iptv.txt` 只包含通过验证的频道。验证方式：拉取 m3u8 播放列表 → 下载首个分片实测速度，实测速度不低于码率才判定为"流畅"。

环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `IPTV_OUTPUT` | `iptv.txt` | 输出文件路径 |
| `IPTV_WORKERS` | `8` | 并发验证线程数 |
| `IPTV_MIN_OK` | `1` | 通过频道数低于该值时不更新订阅文件（防低质量网络覆盖旧数据） |
| `IPTV_SSL_VERIFY` | `1` | 本机有 TLS 拦截代理时设为 `0` 关闭证书校验 |

## 实现原理

1. `program-sc.miguvideo.com/live/v2/tv-data/...` 获取频道分类与频道 ID（pID）
2. `play.miguvideo.com/playurl/v1/play/playurl` 以安卓端签名（sign/salt）换取 720p 播放地址
3. 对播放地址计算 `ddCalcu` 签名，跟随 302 得到真实 CDN m3u8 地址
4. 实测分片下载速度，过滤卡顿源

算法参考开源项目 [develop202/migu_video](https://github.com/develop202/migu_video)。

## 注意

- 直播地址带时效参数，过期后需等下一次定时更新（最坏约 8 小时）。
- 本项目仅供学习研究，直播内容版权归咪咕视频所有。
