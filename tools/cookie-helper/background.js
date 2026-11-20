// 无双Cookie助手 service worker（v2026.11.20）
// 豆包凭证提取完全在 popup.js 中通过 chrome.cookies API 完成，无需后台监听。
// 千问/元宝因 2026 协议变更暂不可用（界面已置灰），故移除其请求捕获监听。
// 本扩展仅在用户本机浏览器运行，不向任何外部服务器上传数据。
chrome.runtime.onInstalled.addListener(() => {
  console.log("无双Cookie助手已安装：仅供个人研究测试，建议用小号，封号风险自负。");
});
