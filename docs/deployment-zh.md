# 群晖 DS918+ 部署指南（DSM 7.2）

> **推荐改用套件安装**：[SPK 套件说明](spk-guide-zh.md)（套件中心一键安装/启停、
> 桌面图标、向导配置、自动迁移手动部署）。本文保留 Docker 手动部署方式。
> 大模型网页版 Cookie 获取：[Cookie 指引](cookie-guide-zh.md)。

目标：把「无双小爱同学」跑在 NAS 上，让 **小爱音箱 Pro（LX06）** 和
**小米AI音箱一代（S12）** 对话接大模型、听歌免会员。

---

## 0. 准备清单

| 项目 | 说明 |
|---|---|
| NAS | DS918+（x86_64），DSM 7.2.1，已装 Container Manager |
| 网络 | 音箱与 NAS 同一局域网；路由器未开 AP 隔离 |
| 小米账号 | 音箱绑定的账号（**建议小号**，云端接口有风控可能） |
| 网易云账号 | 你的 VIP 账号（扫码登录） |
| 大模型 | 免费路线：豆包/千问网页版账号；或官方 API Key（火山方舟/百炼） |
| SSH | DSM「控制面板 → 终端机和 SNMP」启用 SSH（部署时用） |

## 1. 上传项目

把整个项目文件夹上传到 NAS，例如 `/volume1/docker/wushuang`
（File Station 上传压缩包后解压，或 SSH 里 git clone）。

> `reference/` 上游源码不需要从开发机带过去，下一步脚本会在 NAS 上拉取。

## 2. 拉取上游组件

SSH 登录 NAS 后：

```bash
cd /volume1/docker/wushuang
bash scripts/fetch-upstream.sh
```

拉取三个组件：`ncm-api-enhanced`（网易云协议，必需）、
`doubao-free-api-1` 与 `Qwen-Free-API`（网页版大模型逆向，可选）。
GitHub 克隆失败时脚本自动改用 tarball 下载。

## 3. 配置 .env

```bash
cp .env.example .env
vi .env
```

必改三项：

```ini
# 音箱拉流地址：必须是音箱能访问到的 NAS 局域网 IP，不能是 127.0.0.1
WUSHUANG_PUBLIC_URL=http://192.168.1.10:8080
# 后台初始密码（登录后在后台再改一次）
WUSHUANG_ADMIN_PASSWORD=一个强密码
# 你的曲库目录（网易云同步的歌也放这里）
MUSIC_DIR=/volume1/music
```

## 4. 构建并启动

```bash
sudo docker compose up -d --build
```

- 只用官方 API / 已有中转：上面这条即可。
- 要免费网页版豆包/千问：`.env` 中设 `DOUBAO_REPLICAS=1` / `QWEN_REPLICAS=1`
  后重新 `sudo docker compose up -d`（元宝为内置实现，无需容器）。

查看日志：`sudo docker compose logs -f wushuang`

> 构建慢/失败多为网络问题：.env 里 PIP_INDEX_URL / NPM_REGISTRY 已默认国内镜像；
> 海外环境改回 https://pypi.org/simple 与 https://registry.npmjs.org。

## 5. 后台配置（浏览器打开 http://NAS_IP:8080）

### 5.1 小米账号（账号页）

**推荐：米家扫码登录**——点「📱 米家扫码登录」按钮，用米家 APP（登录音箱
绑定的小米账号）扫码确认即可，无需密码和短信验证。

备选：填账号密码 → 保存并登录。若提示安全验证，点验证链接完成短信验证后
再点「重新登录」（链接有时效，需立即验证；系统会锁定会话上下文重试）。

### 5.2 启用音箱（设备页）
勾选 LX06 与 S12 两台 → 轮询指示灯变 🟢。
可为每台配置不同触发词/人设。

### 5.3 大模型服务商（账号页）

**免费路线（网页版逆向）：**
1. 电脑浏览器登录 [豆包网页版](https://www.doubao.com)（或通义千问网页版），随便发一句话；
2. F12 → Application → Cookies → 复制 `sessionid`（千问为 `tongyi_sso_ticket`）；
3. 后台添加服务商：预设选「豆包网页版（免费·逆向）」，Key 粘贴 sessionid；
4. 点「测试」验证。登录态过期后重复上述步骤更新 Key。

**官方路线（更稳）：** 预设选火山方舟/百炼，填 API Key 与模型名。

可添加多个服务商，优先级数字小的先用，失败自动降级到下一个。

### 5.4 网易云（账号页）
「获取登录二维码」→ 网易云 APP 扫码确认（或粘贴含 MUSIC_U 的 Cookie）。

### 5.5 歌单（音乐页）
1. 类型选「网易云歌单」→ 拉取我的歌单 → 选择「我喜欢的音乐」等；
2. 填 cron（如 `0 4 * * *` 每天凌晨4点同步）→ 添加 → 立即同步；
3. 同步完成后对音箱说：`播放歌单 我喜欢的音乐`。
4. 自有曲库：把文件放进 MUSIC_DIR，点「扫描曲库」，`播放歌曲 XXX` 点播。

## 6. 验收测试

| 对音箱说 | 预期 |
|---|---|
| 小爱同学，请帮我用一句话介绍长城 | 几秒后小爱播报大模型答案 |
| 播放歌曲 晴天 | 播放曲库中的晴天（没有则提示） |
| 播放歌单 我喜欢的音乐 | 播放同步下来的网易云歌单 |
| 下一首 / 暂停播放 / 音量大一点 | 播放控制生效 |
| 定一个明早8点的闹钟 | 小爱原生处理，不受影响 |

## 7. 常见问题

**Q：音箱说"播放失败"/没声音？**
A：九成是 `WUSHUANG_PUBLIC_URL` 不对。音箱必须能访问该地址：
手机连同一 WiFi 浏览器打开 `http://NAS_IP:8080/media/1` 应能下载音频。
检查 NAS 防火墙、Docker 端口映射、路由器 AP 隔离。

**Q：小米登录失败/掉线？**
A：小米风控。等几分钟重试；避免多设备频繁登录同一账号；token 会自动续期，
彻底失效时在后台重新保存账号密码。

**Q：小米安全验证完成后仍提示需要验证？**
A：验证链接有时效（约几十分钟），过期链接验证后会报错——点「重新登录」
生成新链接并**立即**完成验证。验证完成后再次点「重新登录」，系统会用
**同一会话上下文**重试（1.0 版修复：此前每次重试都开新会话导致验证作废）。
正确顺序：重新登录 → 立即打开新链接验证 → 再点重新登录 → 成功。

**Q：大模型回答失败？**
A：账号页点「测试」看具体错误。free-api 常见：sessionid 过期（重新取）、
官方网页改版（等上游更新或切官方 API 预设）。配了多个服务商时会自动降级。

**Q：网易云歌曲同步失败/灰色歌曲？**
A：无版权或权益不足的歌曲取不到地址，会跳过并计入失败数；VIP 歌曲需 VIP 账号登录态。
Cookie 过期时后台状态会变 expired，重新扫码即可。风控严格时降低同步频率。
ncm-api 启动日志偶见 `xeapi public key is missing`：这是启动瞬间公钥尚未注册完成的
正常现象，歌曲地址接口已内置 v1→旧接口自动回退，不影响同步。

**Q：小爱总是先自己抢答？**
A：未刷机设备的固有限制。用"请/帮我"等触发词开头可显著缓解；
后台可自定义触发词。彻底解决需刷机（不在本项目范围）。

**Q：播完不自动下一首？**
A：自动切歌优先查播放状态，查询不可用时按曲目时长兜底。个别机型仍有差异，
可先手动"下一首"，并反馈硬件型号与日志。

**Q：某些歌播放失败/没声音（但文件存在）？**
A：LX06 固件对 MP4/AAC 容器（.m4a）兼容性差（上游 xiaomusic 已证实），
曲库优先用 **mp3 / flac**；网易云同步默认就是 mp3/flac，不受影响。
m4a 转码支持在路线图中。

**Q：docker build 拉取基础镜像失败（tls error / timeout / 镜像加速器报错）？**
A：Container Manager 里配置的 Docker Hub 加速器失效（国内常见）。两种解法：
1. Container Manager → 设置 → 注册表，换一个可用的加速器；
2. 从其他公共仓库手动拉基础镜像并打标签，再重新构建：
```bash
sudo docker pull public.ecr.aws/docker/library/python:3.12-slim
sudo docker tag public.ecr.aws/docker/library/python:3.12-slim python:3.12-slim
sudo docker pull public.ecr.aws/docker/library/node:lts-alpine
sudo docker tag public.ecr.aws/docker/library/node:lts-alpine node:lts-alpine
sudo docker compose up -d --build
```

**Q：DSM 升级后容器异常？**
A：`sudo docker compose up -d --build` 重建即可；数据都在 ./data 与曲库目录，不会丢。

## 8. 升级与卸载

```bash
# 升级（保留数据）
cd /volume1/docker/wushuang
git pull                # 或上传新版本覆盖
sudo docker compose up -d --build

# 卸载
sudo docker compose down
# 数据在 ./data（数据库/密钥）与 MUSIC_DIR，自行决定是否删除
```

## 9. 安全清单

- [ ] 后台密码已改强密码
- [ ] 8080 端口只在内网可达，**未映射公网**（/media 不鉴权）
- [ ] 小米/网易云使用小号或知情同意主号风险
- [ ] data/ 目录（含密钥与凭证）不随意外传
