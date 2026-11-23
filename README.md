# 无双小爱同学

让家里的小米小爱音箱**对话变聪明、听歌不花会员钱**的统一增强服务，专为群晖 NAS 设计。

**当前版本 v0.4.1**：四平台音乐 + Web/APP 播放控制 + 原生指令 + 「小爱原始/无双小爱」双状态切换（语音"变身"）。更新说明见 [CHANGELOG.md](CHANGELOG.md) 与 [Releases](../../releases)。

> ## ⚠️ 重要声明（务必阅读）
> **本项目仅供个人学习与研究测试，请于下载后 24 小时内自行删除。**
> 大模型对话、各音乐平台等功能通过**模拟网页登录态调用非官方接口**实现，
> **违反相关平台服务条款，可能触发风控、限制登录甚至封号**。
> **强烈建议使用小号**（非主力账号），**一切后果由使用者自行承担**。
> 请勿用于商用或对外提供服务。逆向接口不稳定，平台改版后可能随时失效。
> 本项目不分发、不收集、不上传任何账号凭证；所有登录信息仅保存在你自己的 NAS 本地。
> 请支持官方正版 API 与音乐会员服务。继续使用的风险由你自愿承担。

站在开源社区的肩膀上：合并 [mi-gpt](https://github.com/idootop/mi-gpt) 的大模型对话能力与
[xiaomusic](https://github.com/hanxi/xiaomusic) 的自由听歌能力为**一个服务、一个网页后台**，
协议层复用 [MiService](https://github.com/yihong0618/MiService)（小米云）、
[ncm-api-enhanced](https://github.com/Catamint/ncm-api-enhanced)（网易云）、
[KuGouMusicApi](https://github.com/MakcRe/KuGouMusicApi)（酷狗）、
[QQMusicApi](https://github.com/L-1124/QQMusicApi)（QQ音乐）、
[qishui-api](https://github.com/guowenye/qishui-api)（汽水音乐），不重复造轮子。

> 适用设备：小米/红米小爱音箱（已验证：LX06 小爱音箱Pro、S12 小米AI音箱一代等）。
> 华为/天猫精灵音箱不在本项目范围内（封闭生态，无云端接口）。

## 功能

- 🧠 **大模型对话**：**豆包网页版内置免签名客户端**（2026 新协议，无需中转容器），
  另支持**官方 API**（火山方舟/阿里云百炼/DeepSeek/任意 OpenAI 兼容端点），按优先级自动降级。
  说"小爱同学，请帮我……"或自定义触发词即可触发，**小爱原声播报**。
  （千问/元宝因 2026 协议变更暂不可用，界面已置灰。）
- 🎵 **自由听歌**：语音点播 NAS 本地曲库；**网易云/酷狗/QQ/汽水四平台歌单定时同步**
  （VIP 账号可取更高音质；汽水 VIP 加密曲暂不支持），下载到本地后播放，
  不受链接有效期影响；播放控制/循环模式/自动切歌。
- 🏠 **多音箱**：每台设备独立启用、独立触发词与人设；单一调度器，
  音乐/对话/原生指令三分支互斥。闹钟、米家控制等原生能力不受影响。
- 📱 **米家扫码登录小米账号**：免密码免短信验证，登录态持久保护。
- 🧩 **无双Cookie助手扩展**（`tools/cookie-helper/`）：一键提取豆包登录凭证，免翻 DevTools。
- 🖥️ **统一 Web 后台**：账号、设备、歌单与同步、对话日志、运行设置，全部网页化管理。
  凭证加密存于 NAS 本地 SQLite。
- 🐳 **两种部署形态**：群晖 **SPK 套件**（套件中心图形界面安装，无需 SSH）；
  或 `docker compose up -d --build`（通用 Docker）。5 个容器（主服务 + 4 个音乐协议 sidecar，
  均仅内网可见），另可选网页版大模型 profile。

## 下载安装

前往 **[Releases](../../releases)** 页面下载分发包 `无双小爱同学-分发包-*.zip`，内含：
群晖 SPK 套件、Docker 版源码、浏览器插件、**安装使用说明**（图形界面教程，无需 SSH）、风险提示。

## 快速开始（群晖套件，推荐，无需 SSH）

1. 套件中心 → 设置 → 信任「任何发行者」→ 手动安装分发包里的 `wushuang_xiaoai-*.spk`。
2. 向导设置**管理密码** / **NAS 局域网地址**（`http://NAS_IP:8080`）/ **音乐目录**。
3. 完成安装，首次自动构建镜像（约 3–10 分钟）。DSM 桌面图标直达后台。

详见分发包内 `安装使用说明.md` 与 [套件说明](docs/spk-guide-zh.md)。

## 快速开始（Docker，通用）

```bash
# 1. 拉取上游组件源码（ncm-api 必需）
bash scripts/fetch-upstream.sh

# 2. 配置
cp .env.example .env
# 编辑 .env：WUSHUANG_PUBLIC_URL 填 NAS 局域网地址（音箱要能访问到！）
#           WUSHUANG_ADMIN_PASSWORD 设一个强密码，MUSIC_DIR 指向你的曲库

# 3. 启动
docker compose up -d --build
```

打开 `http://NAS_IP:8080` → 登录 → 「账号」页配置小米扫码、豆包凭证、音乐平台扫码
（网易云/酷狗/QQ/汽水按需）→「设备」页启用音箱 → 「音乐」页添加歌单并同步。
详见 [部署文档](docs/deployment-zh.md)。

## 快速开始（本地开发）

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m wushuang          # 默认 http://0.0.0.0:8080
pytest                      # 单元测试
```

构建 SPK：`python scripts/build_spk.py`（需先 `bash scripts/fetch-upstream.sh`）。

## 语音指令速查

| 类别 | 示例 |
|---|---|
| 点歌 | `播放歌曲 晴天` · `我想听 周杰伦的歌` · `播放歌单 我喜欢的音乐` |
| 控制 | `暂停播放` `继续播放` `下一首` `上一首` `音量大一点` `音量调到30` `单曲循环` `随机播放` |
| 大模型 | `小爱同学，请帮我解释量子力学` |
| 原生 | 其余指令（闹钟/天气/米家控制）仍由小爱原生处理 |

## 架构

```
音箱麦克风 → 小米云 → [对话记录轮询：LX06 用 conversation HTTP API]
                          ↓ 统一调度器（每台音箱一个循环）
        ┌─────────────────┼──────────────────────┐
   音乐点播/控制        LLM 触发词              其他 → 放行原生
        ↓                  ↓
   音乐引擎            LLM 引擎（多服务商降级）
   本地曲库/多平台同步   流式分句 → 小爱原声 TTS
        ↓
   NAS HTTP 媒体流 ← 音箱拉流播放（/media/<id>，支持 Range）
```

模块与数据流详见 [架构文档](docs/architecture.md)。

## 风险与合规（务必阅读）

- **仅供个人研究测试，下载后 24 小时内请自行删除。** 网页版大模型逆向、网易云非官方接口
  均可能违反平台服务条款，存在账号风控/封禁风险，**建议使用小号，后果自负**；
  音乐内容版权归原作者所有，请勿传播下载文件。
- 小米云接口非官方公开 API，上游变动可能导致功能暂时失效。
- 服务只应暴露于局域网；管理密码务必修改；`/media` 媒体流不鉴权（音箱无法登录），
  **不要将端口映射到公网**。
- 未刷机设备的固有限制：小爱自身 NLU 可能先"抢答"，本项目通过触发词缓解，无法根除。

## 上游致谢与许可证

本项目 MIT 许可证（见 [LICENSE](LICENSE)）。内置/引用的上游组件版权归原作者，详见 [NOTICE](NOTICE)：
MiService（MIT）、xiaomusic（MIT，设计参考）、mi-gpt（MIT，设计参考）、
NeteaseCloudMusicApi / ncm-api-enhanced（MIT，协议 sidecar）。
