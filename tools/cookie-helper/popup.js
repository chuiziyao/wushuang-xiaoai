const SITES = {
  doubao: { domains: ["doubao.com"], names: ["sessionid_ss", "sessionid"] },
  qwen: { domains: ["qianwen.com", "aliyun.com", "tongyi.aliyun.com"],
          names: ["tongyi_sso_ticket", "login_aliyunid_ticket", "login_tongyi_ticket", "qianwen_ticket"] },
  yuanbao: { domains: ["yuanbao.tencent.com", "tencent.com"], names: ["hy_user", "hy_token"] },
};
const cache = {};

async function getAllCookies(domains) {
  const all = [];
  for (const d of domains) {
    const cs = await chrome.cookies.getAll({ domain: d });
    for (const c of cs) all.push(c);
  }
  // 去重（同名同域）
  const seen = new Set();
  return all.filter((c) => {
    const k = c.domain + "|" + c.name;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

async function extract(site) {
  const out = document.getElementById(site + "-val");
  try {
    if (site === "uskey") {
      const d = await chrome.storage.local.get(["yuanbao_uskey", "yuanbao_uskey_ts"]);
      if (d.yuanbao_uskey) {
        cache.uskey = d.yuanbao_uskey;
        const mins = Math.round((Date.now() - (d.yuanbao_uskey_ts || 0)) / 60000);
        out.textContent = d.yuanbao_uskey + `（${mins}分钟前捕获）`;
      } else {
        out.textContent = "未捕获：请先在元宝网页发一句话，再点提取";
      }
      return;
    }
    if (site === "chatbody") {
      const d = await chrome.storage.local.get(["yuanbao_chat_body", "yuanbao_chat_ts"]);
      if (d.yuanbao_chat_body) {
        cache.chatbody = d.yuanbao_chat_body;
        const mins = Math.round((Date.now() - (d.yuanbao_chat_ts || 0)) / 60000);
        out.textContent = d.yuanbao_chat_body + `\n（${mins}分钟前捕获）`;
      } else {
        out.textContent = "未捕获：请先在元宝网页发一句话，再点捕获";
      }
      return;
    }
    const cfg = SITES[site];
    // 元宝优先用从真实请求捕获的完整 Cookie（最可靠，不怕改名）
    if (site === "yuanbao") {
      const d = await chrome.storage.local.get(["yuanbao_cookie", "yuanbao_cookie_ts"]);
      if (d.yuanbao_cookie) {
        cache.yuanbao = d.yuanbao_cookie;
        const mins = Math.round((Date.now() - (d.yuanbao_cookie_ts || 0)) / 60000);
        out.textContent = d.yuanbao_cookie + `\n（${mins}分钟前从请求捕获）`;
        out.classList.add("ok");
        return;
      }
    }
    const cookies = await getAllCookies(cfg.domains);
    const parts = [];
    for (const name of cfg.names) {
      const c = cookies.find((x) => x.name === name);
      if (c) parts.push(site === "yuanbao" ? `${name}=${c.value}` : c.value);
      if (parts.length && site !== "yuanbao") break;
    }
    if (parts.length) {
      cache[site] = site === "yuanbao" ? parts.join("; ") : parts[0];
      out.textContent = cache[site];
      out.classList.add("ok");
    } else {
      out.textContent = `未找到目标cookie（共${cookies.length}条）：请点「列出全部」看名字，或「复制整段」`;
    }
  } catch (e) {
    out.textContent = "出错: " + (e.message || e);
  }
}

async function listAll(site) {
  const out = document.getElementById(site + "-val");
  try {
    const cfg = SITES[site];
    const cookies = await getAllCookies(cfg.domains);
    if (!cookies.length) {
      out.textContent = `0条cookie：请确认已在本浏览器登录 ${cfg.domains.join(" / ")}`;
      return;
    }
    const names = cookies.map((c) => `${c.name}  [${c.domain}]`).join("\n");
    out.textContent = names;
    try {
      await navigator.clipboard.writeText(names);
      out.textContent = "✅ 名字列表已复制到剪贴板，直接粘贴给助手：\n\n" + names;
    } catch (e) {
      out.textContent = names + "\n\n（请手动选中上面文字复制）";
    }
  } catch (e) {
    out.textContent = "出错: " + (e.message || e);
  }
}

async function copyAll(site) {
  const out = document.getElementById(site + "-val");
  try {
    const cfg = SITES[site];
    const cookies = await getAllCookies(cfg.domains);
    if (!cookies.length) {
      out.textContent = "0条cookie，无法复制";
      return;
    }
    const full = cookies.map((c) => `${c.name}=${c.value}`).join("; ");
    cache[site + "_all"] = full;
    await navigator.clipboard.writeText(full);
    out.textContent = `已复制整段（${cookies.length}条）:\n` + cookies.map((c) => c.name).join(", ");
  } catch (e) {
    out.textContent = "出错: " + (e.message || e);
  }
}

document.querySelectorAll("[data-site]").forEach((btn) =>
  btn.addEventListener("click", () => extract(btn.dataset.site))
);
document.querySelectorAll("[data-list]").forEach((btn) =>
  btn.addEventListener("click", () => listAll(btn.dataset.list))
);
document.querySelectorAll("[data-copyall]").forEach((btn) =>
  btn.addEventListener("click", () => copyAll(btn.dataset.copyall))
);
document.querySelectorAll("[data-copy]").forEach((btn) =>
  btn.addEventListener("click", async () => {
    const site = btn.dataset.copy;
    if (!cache[site]) await extract(site);
    if (cache[site]) {
      await navigator.clipboard.writeText(cache[site]);
      btn.textContent = "已复制✓";
      setTimeout(() => (btn.textContent = site === "yuanbao" ? "复制Cookie" : "复制"), 1500);
    }
  })
);
