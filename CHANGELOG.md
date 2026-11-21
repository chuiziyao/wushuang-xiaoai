# 更新日志

## v0.2.0（2026-11-21）

**四平台音乐支持**：网易云 / 酷狗 / QQ音乐 / 汽水音乐并列，扫码登录 + 歌单定时同步下载 + 语音点播。

- 🎵 新增酷狗音乐（酷狗 APP 扫码，[KuGouMusicApi](https://github.com/MakcRe/KuGouMusicApi) 协议）
- 🎵 新增 QQ音乐（QQ / 微信扫码，[QQMusicApi](https://github.com/L-1124/QQMusicApi) 协议；上游 GPLv3，独立容器隔离）
- 🎵 新增汽水音乐（**抖音 APP** 扫码，[qishui-api](https://github.com/guowenye/qishui-api) 协议；VIP 加密曲暂不支持下载，免费曲正常）
- 🏗️ 多平台架构重构：统一平台接口与通用同步引擎；旧版网易云数据（账号/歌单/曲目）**启动时自动迁移**，无需手动操作
- 🕐 修复页面时间与 cron 定时同步差 8 小时的问题（存储 UTC，显示/调度按 `WUSHUANG_TZ`，默认 `Asia/Shanghai`）
- 🗣️ 歌单名语音归一化：数字中文/阿拉伯写法互通（"666/六六六"、"四万首/4万首"均可命中）
- 🖥️ 后台：账号页四平台面板、音乐页四来源选择、仪表盘多平台登录汇总、设置页各平台音质选项
- 📦 群晖 SPK 套件同步四平台；Docker 形态 5 容器（主服务 + 4 协议 sidecar，均仅内网可见）
- 🧪 88 个单元/集成测试 + 全应用冒烟测试

**升级方式**：
- SPK 用户：套件中心手动安装 `wushuang_xiaoai-0.2.0-0001.spk`（覆盖升级，数据保留）
- Docker 用户：覆盖源码 → `bash scripts/fetch-upstream.sh` → `sudo docker compose up -d --build`

**已知限制**：汽水 VIP 加密音频（spade_a）暂不支持；各平台 Web 协议存在风控风险，建议小号。

## v20261120（0.1.0，2026-11-20）

首个公开版本：大模型对话（豆包网页版内置免签名客户端 + 官方 API 多服务商降级）、
网易云歌单定时同步、本地曲库语音点播、多音箱独立调度、米家扫码登录、统一 Web 后台、
群晖 SPK 套件与 Docker 双形态部署。
