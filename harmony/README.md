# 无双遥控器 · 鸿蒙版（HarmonyOS NEXT）

ArkTS WebView 壳工程：连接运行在电脑/NAS 上的「无双小爱同学」服务，
手机/平板即是遥控器（播放控制、曲库、在线音源、对话日志、大模型配置）。

## 两种鸿蒙，两种装法

| 系统 | 装法 |
|---|---|
| **HarmonyOS NEXT / 5.x**（纯血鸿蒙，不兼容 APK） | 用 DevEco Studio 打开本工程构建 HAP 安装（见下） |
| **HarmonyOS 4.x 及以下**（兼容 APK） | 直接安装分发包里的 `无双遥控器-v*.apk`，无需本工程 |

## 构建 HAP（HarmonyOS NEXT）

1. 安装 [DevEco Studio 5.0+](https://developer.huawei.com/consumer/cn/deveco-studio/)（含 HarmonyOS SDK，API 12+）。
2. File → Open 选择本 `harmony/` 目录，等待 hvigor 同步完成。
3. 登录华为开发者账号，File → Project Structure → Signing Configs 勾选
   **Automatically generate signature**（自动调试签名）。
4. 手机开启开发者模式 + USB 调试并连接，Run 'entry' 即安装到手机。
   或 Build → Build Hap(s) 生成 `entry/build/default/outputs/default/entry-default-unsigned.hap`，
   签名后用 `hdc install` 安装。
5. 打开 App，填入无双小爱同学后台地址（如 `http://192.168.1.10:8080`），
   输入管理密码登录。地址会持久保存，下次直连。

> 说明：本仓库 CI 不含华为签名工具链，HAP 需在 DevEco Studio 内一键构建；
> 工程代码与桌面版/Web 后台完全解耦，服务升级无需重装 App。

## 权限

仅 `ohos.permission.INTERNET`（访问局域网内的无双服务）。
允许 http 明文（局域网服务无 TLS），WebView 已开启 DOM Storage 保持登录态。