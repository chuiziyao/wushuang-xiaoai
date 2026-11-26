# 架构说明

## 设计原则

1. **合并产品层，不合并协议层**：调度/业务/UI 自己实现；小米云与各音乐平台的
   登录加密、协议细节交给上游组件（MiService 内置 vendor、ncm-api sidecar），
   上游修协议我们跟着换零件，不自己背破解维护成本。
2. **单一调度器**：每台音箱一个轮询循环，一句话只走一条分支，
   杜绝 mi-gpt + xiaomusic 双容器抢话问题。
3. **本地优先**：远端歌单同步下载到 NAS 再播放，规避播放链接有效期问题。
4. **凭证不出 NAS**：全部加密存 SQLite（Fernet），服务只暴露局域网。

## 模块地图

```
wushuang/
├─ app.py               # FastAPI 工厂 + lifespan 组装所有引擎
├─ desktop.py           # ★ 桌面版启动器：后台服务线程 + pywebview 原生窗口/浏览器兜底
├─ config.py            # 环境变量引导 + 运行时设置默认值（WUSHUANG_DESKTOP 桌面模式）
├─ storage.py           # SQLite（aiosqlite）：设置/账号/服务商/设备/歌单/曲目/日志/会话
├─ crypto.py            # Fernet 密钥箱（data/secret.key）
├─ dispatcher.py        # ★ 统一调度器：route() 纯函数路由 + 每设备轮询循环
├─ vendor/miservice/    # 内置 MiService（MIT，静态UA补丁），小米云协议
├─ xiaomi/
│  ├─ account.py        # 账号管理：token 内存缓存+异步加密落盘、自动重登
│  ├─ devices.py        # 设备发现（mina device_list → DB）
│  └─ speaker.py        # 单音箱操作：轮询/TTS/播放URL/控制/状态
├─ llm/
│  ├─ providers.py      # 双轨预设：doubao-web/qwen-web/ark/dashscope/deepseek/custom
│  ├─ client.py         # OpenAI 兼容流式客户端（SSE，兼容非流式响应）
│  └─ engine.py         # 对话编排：记忆→多服务商降级→分句→TTS节奏→日志
├─ music/
│  ├─ platforms.py      # 平台注册表 + 统一客户端接口约定（扫码/歌单/地址归一化）
│  ├─ library.py        # 曲库扫描（mutagen 标签；桌面版含视频）、歌单文件夹映射
│  ├─ videometa.py      # 纯 Python 视频时长（MP4 mvhd / MKV EBML），供自动切歌
│  ├─ server.py         # /media/<id> 媒体流（自实现 Range/206，路径越界防护；在线音源 307）
│  ├─ engine.py         # 播放队列/循环模式/自动切歌巡检/语音控制
│  ├─ sync.py           # 多平台歌单同步下载（跳过已有、写标签、进度、日志）
│  ├─ scheduler.py      # cron 定时调度（croniter，30s 巡检，按 WUSHUANG_TZ）
│  ├─ netease/          # 网易云：ncm-api 客户端 + 平台适配
│  ├─ kugou/            # 酷狗：KuGouMusicApi 客户端
│  ├─ qq/               # QQ音乐：QQMusicApi Web 客户端（GPLv3 sidecar 隔离）
│  └─ qishui/           # 汽水：qishui-api 客户端（VIP 加密曲暂不支持）
├─ utils/text.py        # TTS 分句（SentenceBuffer）、文件名清洗
└─ web/
   ├─ auth.py           # 管理员密码（盐化SHA256）+ 服务端会话
   ├─ pages.py          # Jinja2 页面
   ├─ api.py            # JSON 管理接口（全部需会话；/media 除外）
   ├─ templates/        # 仪表盘/账号/设备/音乐/对话/设置
   └─ static/           # app.css / app.js（无外部 CDN 依赖，离线可用）
```

## 数据流

### 对话链路
```
音箱麦克风 → 小米云 NLU → mina ubus nlp_result_get（1s 轮询）
→ dispatcher.route(question)
   ├─ control/music → MusicEngine
   ├─ llm → LLMEngine.ask
   │    ├─ 取消该设备上一个回答任务（打断）
   │    ├─ system prompt（全局/设备人设）+ 最近 N 轮历史（chat_logs）
   │    ├─ 按优先级遍历服务商：stream_chat → SentenceBuffer.feed
   │    │    → speaker.tts(句子) → sleep(字数/语速)   [小爱原声]
   │    └─ 失败降级下一服务商；全败播报致歉；写 chat_logs
   └─ pass → 不干预（小爱原生应答）
```

### 音乐链路
```
"播放歌单X" → MusicEngine → DB 歌单/曲目 → track_play_url():
    本地曲目 → speaker.play_by_url(http://主机:8080/media/<id>)
               → 音箱 HTTP GET（Range）→ 主机流式读文件
    在线音源 → speaker.play_by_url(原始 https URL) → 音箱直接从音源拉流
巡检任务每 5s 查播放状态，空闲则按模式切下一首；
本地视频时长由 videometa 解析（mutagen 不认的容器），在线直播不做超时切歌。
```

### 远端歌单同步链路（多平台通用）
```
cron/手动 → PlaylistSync.sync_playlist（按歌单 source 选平台客户端）
→ 平台 sidecar: 歌单曲目（分页/游标）
→ 逐曲：DB 已有且文件在 → 跳过；否则取播放地址（VIP cookie，音质可配）
→ httpx 下载 .part → 原子改名 → mutagen 写标签 → upsert_track(source, remote_id)
→ 更新歌单状态 + sync_logs；Web 轮询 /progress 展示进度
```

## 路由规则（dispatcher.route）

优先级：**控制 > 音乐 > LLM > 放行**。
- 控制：暂停/继续/停止/上下首/音量/循环模式（正则全匹配）。
- 音乐：先剥礼貌前缀（请/帮我/我想…），再匹配 播放歌单X / 播放歌曲X /
  我想听X / X的歌（artist）。"帮我播放歌曲X" 走音乐而非 LLM。
- LLM：唤醒词（无双/豆包/千问）或触发词前缀（请/帮我/为什么/…，每设备可配）。
- 其余放行，保护闹钟/米家控制等原生能力。

去重与回放保护：首轮只建时间基线不处理历史；按 request_id 去重；
时间戳单调递增判断，规避 NAS 与小米云时钟偏差。

## 存储（SQLite，data/wushuang.db）

settings / xiaomi_account / music_accounts（每平台一行）/ llm_providers / devices /
playlists / tracks（remote_id 按平台隔离）/ chat_logs / sync_logs / sessions。
旧版 netease_account / netease_id 列在启动时自动迁移。
敏感列一律 *_enc（Fernet）；密钥 data/secret.key（0600）。

## 部署形态（同一代码库）

- **Docker**：compose 五容器：wushuang（主服务）+ 4 音乐协议 sidecar（仅内部网络）。
- **群晖 SPK**：套壳 Container Manager，安装向导落 compose 与 .env，DSM 图标直达。
- **桌面版（v0.8.0+）**：`WUSHUANG_DESKTOP=1`，`wushuang/desktop.py` 启动：
  - 后台线程跑同一 FastAPI 服务（0.0.0.0，音箱仍走局域网 HTTP 拉流）；
  - 前台 pywebview 原生窗口（Win=WebView2 / mac=WKWebView / Linux=WebKitGTK），
    无原生窗口环境自动退回系统浏览器（`--browser` 可强制）；
  - **不接入音乐平台 sidecar**（clients={}，调度器不启动）：曲库=本地文件夹
    （音频+视频）+ 在线音源（tracks.source='online'，path 存 URL，引擎直推原始地址，
    直播无时长不做超时切歌）；
  - 数据目录 `%APPDATA%/WushuangXiaoai`（mac/Linux 对应 XDG 路径），
    `desktop.json` 存媒体目录/端口/拉流地址覆盖，设置页「保存并重启」热更新；
  - 单实例锁（127.0.0.1:47653），重复启动打开已有实例页面；
  - PyInstaller **onedir 目录版**打包（`desktop.spec` + `scripts/build_desktop.py`，便携 zip；
    不用 onefile 以规避系统 TEMP 解压被拦截），可选 Inno Setup 安装版（`scripts/desktop-installer.iss`），
    CI（`.github/workflows/desktop.yml`）构建 Win/Linux/macOS(arm64+x86_64)。
- **鸿蒙遥控器**：`harmony/` ArkTS WebView 壳（HarmonyOS NEXT，DevEco 构建 HAP）；
  鸿蒙 4.x 及以下用 `tools/remote-apk` 构建的 APK。遥控器只是浏览器壳，服务升级无需重装。

## 已知限制

- 小爱原生 NLU 抢答无法根除（未刷机）；TTS 播报节奏按字数估算（无完成回调）。
- 自动切歌依赖 ubus 播放状态，机型差异需实测反馈。
- 视频推送为尽力而为：带屏小爱（触屏音箱系列）可显示画面，无屏机型能否出声取决于固件；
  avi/flv 等容器时长无法轻量解析时按估算时长切歌。
- free-api 类组件时效性强，官方网页改版即可能失效；协议层上游同理。
- S12 等老机型无蓝牙；本项目能力不受影响（走 WiFi 云端接口）。
