/* 无双小爱同学 后台通用脚本 */
async function api(path, method = 'GET', body = null) {
  const opt = { method, headers: {} };
  if (body !== null) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  const resp = await fetch(path, opt);
  if (resp.status === 401) { location.href = '/login'; throw new Error('未登录'); }
  let data = null;
  try { data = await resp.json(); } catch (e) { /* 无 JSON 体 */ }
  if (!resp.ok) {
    const msg = (data && (data.detail || data.error)) || ('HTTP ' + resp.status);
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  return data;
}

/* UTC ISO 时间戳 → 浏览器本地时间（与后端 localtime 过滤器同一数据源） */
function fmtTs(iso, withDate = true) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return String(iso);
  const p = n => String(n).padStart(2, '0');
  const t = p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  if (!withDate) return t;
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + t;
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

let _toastTimer = null;
function toast(msg, ok = true) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast show' + (ok ? '' : ' err');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { el.className = 'toast'; }, 3500);
}

async function run(fn, okMsg) {
  try {
    const r = await fn();
    if (okMsg) toast(okMsg);
    return r;
  } catch (e) {
    toast(e.message || String(e), false);
    throw e;
  }
}
