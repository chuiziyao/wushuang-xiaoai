# 无双小爱同学 · 群晖套件（SPK）安装说明

适用：DSM 7.2.1 及以上（DS918+ 实测），需已安装 **Container Manager**（≥24.0.2-1432）。
套件通过 DSM 官方 **Docker Project worker** 原生管理 compose 项目：
套件中心一键安装/启动/停止/卸载，DSM 桌面图标直达后台。

---

## 一、安装

1. **允许第三方套件**：套件中心 → 设置 → 常规 → 信任层级选「**任何发行者**」
   （本套件为自制未签名包，仅局域网自用）。
2. 套件中心 → **手动安装** → 上传 `wushuang_xiaoai-0.1.0-0001.spk`。
3. 安装向导填写：
   - **管理密码**：后台登录密码（≥6 位）
   - **音乐目录**：曲库共享文件夹，默认 `/volume1/Music`
   - **音箱访问地址**：留空自动检测 NAS IP；多网卡/桥接环境建议手填
     `http://192.168.x.x:8080`
4. 安装过程会自动构建容器镜像（首次约 3~8 分钟，取决于网络；
   基础镜像拉取失败时脚本自动回退 AWS ECR 公共仓库）。
5. 勾选「安装后立即启动」。完成后 DSM 桌面出现「**无双小爱同学**」图标，
   点击即打开后台（http://NAS_IP:8080）。
6. **授予音乐目录访问权限（必需）**：DSM 7 套件以低权限内部用户
   `wushuang_xiaoai` 运行，默认无法访问共享文件夹。二选一：
   - 界面：控制面板 → 共享文件夹 → 选中音乐目录（如 Music）→ 编辑 →
     权限 → 勾选「**系统内部用户**」→ 找到 `wushuang_xiaoai` → 设为「**可读写**」
   - SSH：`sudo synoacltool -add /volume1/Music user:wushuang_xiaoai:allow:rwxpd-a-R-c--:fd--`
   未授权时后台「扫描曲库」结果为 0、网易云同步无法写入。

> 命令行安装（SSH，无向导，密码自动生成）：
> ```bash
> sudo synopkg install /tmp/wushuang_xiaoai-0.1.0-0001.spk
> sudo synopkg start wushuang_xiaoai
> # 自动生成的初始密码见：
> sudo cat /var/packages/wushuang_xiaoai/etc/INITIAL_PASSWORD.txt
> ```

## 二、从手动部署迁移

若 NAS 上已有 `/volume1/docker/wushuang` 手动部署：**先停止旧部署**
（SSH 执行 `cd /volume1/docker/wushuang && sudo docker compose down`，
文件保留），再安装套件。安装时**自动迁移**——原 `.env` 配置与 `data/`
（账号、歌单索引、密钥）整体迁入套件持久目录。网易云登录态、管理密码
全部保留，无需重新配置。迁移后请改用套件中心管理启停。

## 三、日常使用

| 操作 | 位置 |
|---|---|
| 启动/停止/重启 | 套件中心 → 无双小爱同学 |
| 打开后台 | DSM 桌面图标，或 http://NAS_IP:8080 |
| 容器项目 | Container Manager → 项目 → wushuang-xiaoai |
| 账号/设备/歌单/模型 | 后台网页（见部署指南第 5 节） |

后台配置流程：小米账号**米家扫码登录**（推荐，免短信）→ 启用音箱 →
网易云扫码 → 添加歌单同步 → 配置大模型服务商（豆包用扩展提取 sessionid_ss，
见 [cookie-guide-zh.md](cookie-guide-zh.md)）。

## 四、容器构成（仅 2 个）

| 容器 | 作用 | 能否关闭 |
|---|---|---|
| `wushuang` | 主服务：语音路由、大模型对话、播放控制、网页后台 | ❌ 核心，必须开 |
| `wushuang-ncm-api` | 网易云协议：歌单同步、扫码登录 | ❌ 用网易云就必须开 |

**豆包/元宝对话已内置免签名客户端，无需任何中转容器。**
千问如需使用，可参考 `reference/Qwen-Free-API` 自行在 compose 增加服务。

> 设计说明：DSM 的 Container Manager 项目模型在安装时生成，动态增删容器
> （profiles/replicas）会导致模型与实际不一致而启动失败，故套件版固定 2 容器。
> 关闭/重启请用套件中心或 Container Manager「项目」，不要单独删容器。

## 五、文件与数据位置（DSM FHS）

| 路径 | 内容 | 升级 | 卸载 |
|---|---|---|---|
| `/var/packages/wushuang_xiaoai/target/` | 程序与 compose 项目 | 替换 | 删除 |
| `/var/packages/wushuang_xiaoai/etc/wushuang.env` | 运行配置 | 保留 | 保留 |
| `/var/packages/wushuang_xiaoai/var/data/` | 数据库/密钥（账号、歌单索引） | 保留 | 保留 |
| 音乐目录（向导指定） | 曲库与同步下载 | 保留 | 保留 |

彻底清除：卸载套件后手动删除 `etc/` 与 `var/` 两个目录。

## 六、升级

套件中心手动安装新版 SPK 即可（`silent_upgrade=yes`，配置与数据自动保留，
镜像自动重建）。开发机构建新版：`python scripts/build_spk.py --version 0.1.1-0002`。

## 七、故障排查

**安装失败/状态损坏**：查看安装日志（套件中心提示或
`/var/packages/wushuang_xiaoai/` 下日志）；常见为 Container Manager 未安装
或 Docker 服务异常。`sudo synopkg log wushuang_xiaoai` 可看脚本输出。

**启动后无法访问 8080**：
```bash
sudo docker ps                       # 容器是否 Up
sudo docker logs wushuang            # 主服务日志
sudo /var/packages/wushuang_xiaoai/target/project/../../etc/wushuang.env  # 配置
```
检查 `WUSHUANG_PUBLIC_URL` 是否为音箱可达的 NAS 局域网地址。

**镜像构建拉取失败**：Container Manager 的注册表加速器失效时，
脚本已自动回退 ECR；仍失败可手动：
```bash
sudo docker pull public.ecr.aws/docker/library/python:3.12-slim
sudo docker tag public.ecr.aws/docker/library/python:3.12-slim python:3.12-slim
sudo docker pull public.ecr.aws/docker/library/node:lts-alpine
sudo docker tag public.ecr.aws/docker/library/node:lts-alpine node:lts-alpine
```
然后套件中心重启。

**安装后首次启动失败（error 272）**：Container Manager 的启动 worker 用
`compose start` 要求容器已存在。若首次安装后启动失败，SSH 执行一次：
```bash
cd /var/packages/wushuang_xiaoai/target/project
sudo docker compose -f docker-compose.admin.yaml up -d
```
然后回套件中心启动即可，之后的停启循环正常。

**停止套件会发声吗？** 不会。启停只影响容器；音箱发声仅由后台「启用设备」
后的语音指令触发。夜间维护可放心操作容器，只要不启用设备/不发测试语音。

## 八、安全提醒

- 8080 端口仅限局域网，勿映射公网（/media 媒体流不鉴权）
- 管理密码用强密码；小米/网易云建议小号
- 网页版逆向与网易云非官方接口仅限个人自用，风险自负
