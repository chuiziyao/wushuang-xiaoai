# 网页版大模型 Cookie 获取指引（豆包 / 千问 / 元宝）

免费路线的原理：借用你**自己账号的网页登录态**，让 NAS 上的中转服务
以 OpenAI 兼容接口调用网页版模型。

## ⭐ 推荐：无双Cookie助手（浏览器扩展，一键提取）

本项目自带扩展（`tools/cookie-helper/`），走浏览器官方 cookie 接口，
不用在 DevTools 里翻找，HttpOnly cookie 也能读取：

1. Edge 地址栏打开 `edge://extensions`（Chrome 为 `chrome://extensions`）
2. 开启「开发人员模式」
3. 「加载解压缩的扩展」→ 选择 `tools/cookie-helper` 文件夹
4. 先在浏览器登录目标网站，再点扩展图标 → 「提取」→「复制」
5. 粘贴到 NAS 后台「账号 → 服务商」对应预设的 Key 框

元宝的 `x-uskey` 也能自动捕获：先在元宝网页发一句话，再点「读取 x-uskey」。
扩展代码完全本地运行，不上传任何数据。

## 手动方式（备选）

后台支持**直接粘贴整段 Cookie**，自动提取所需令牌。
以下操作均在电脑浏览器完成（手机 APP 登录态不能用于网页接口）。
风险提示：逆向接口违反平台条款，可能触发风控/封号，建议小号、仅限自用。

---

## 一、豆包（doubao.com）——内置免签名客户端，无需中转容器

1. 浏览器打开 https://www.doubao.com 并登录，随便发一句话确认能用。
2. 用**无双Cookie助手扩展**点「提取 sessionid」（自动抓新名 `sessionid_ss`）→ 复制。
   手动方式：F12 → Application → Cookies → `sessionid_ss`（或旧名 `sessionid`）的值。
3. 后台添加服务商：预设选「豆包网页版（免费·内置免签名）」，粘贴 → 保存 → 测试。
4. **无需中转容器**：服务内置 2026 新协议（chat_ability 免签名）直连豆包。

**失效表现**：测试报"登录态失效或风控" → 重新提取 sessionid_ss；
风控错误码 710022004 等约 30 分钟冷却或换网络。

## 二、千问（tongyi.aliyun.com / qianwen.com）

1. 浏览器登录通义千问网页版并发一句话。
2. F12 → **Application → Cookies** → 找 `tongyi_sso_ticket`，复制 Value；
   或 Network 里复制整段 cookie 粘贴（后台自动提取）。
3. 预设选「千问网页版（免费·逆向）」，保存并测试。
4. 中转容器：`WS_PROFILES=qwen-web`。

**注意**：请认准维护分支 Qwen-Free-API（xiaoY233），原版仓库曾发生
供应链攻击（恶意代码），本项目打包的即为修复后的维护分支。

## 三、腾讯元宝（yuanbao.tencent.com）——内置，无需容器

元宝接口除 Cookie 外还需要一个动态签名头 **x-uskey**，两个都要抓：

1. 浏览器打开 https://yuanbao.tencent.com 登录（微信扫码），发一句话。
2. F12 → **Network** → 刷新或再发一句话 → 找到路径含 `/api/` 的请求
   （如 `conversation/create` 或 `chat/...`）：
   - **Request Headers → cookie**：整段复制 → 粘贴到后台「API Key」框
     （需包含 `hy_user` 与 `hy_token`）。
   - **Request Headers → x-uskey**：复制该值 → 粘贴到后台「x-uskey」框。
3. 预设选「腾讯元宝网页版（免费·内置实验性）」，模型默认 `hunyuan`
   （可选 deepseek-v3 / deepseek-r1 / hunyuan-t1 及 -search 联网变体），
   保存并测试。

**实验性说明**：x-uskey 有时效性，失效后测试会报「创建会话失败」，
重新抓取 cookie + x-uskey 即可。元宝网页改版也可能导致接口失效，
届时可换豆包/千问或官方 API 预设兜底。

## 四、官方 API（最稳，按量计费极便宜）

不想折腾 Cookie 时的兜底：
- 豆包官方：火山方舟（ark.cn-beijing.volces.com）创建 API Key 与推理接入点
- 千问官方：阿里云百炼（bailian.console.aliyun.com）创建 API Key
- 预设选对应官方项，填 Key 与模型名即可，新用户有免费额度。

后台可添加多个服务商并排优先级：**免费网页版优先、官方 API 兜底**，
网页版失效时自动降级，音箱不会变哑巴。
