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

/* 账号/登录状态码 → 中文（expired 统一显示为"登录失效"） */
function zhStatus(s) {
  const M = { ok: '正常', expired: '登录失效', verification_required: '待安全验证',
              logged_out: '未登录', unknown: '未知' };
  return M[s] || s;
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

/* 秒 → 时长显示（3:45 / 1:02:03）；0/未知返回空串 */
function fmtDur(sec) {
  sec = Math.round(Number(sec) || 0);
  if (sec <= 0) return '';
  const p = n => String(n).padStart(2, '0');
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? h + ':' + p(m) + ':' + p(s) : m + ':' + p(s);
}

/* ---------- 轻量模态框（替代原生 prompt/confirm 的观感） ---------- */
function _modalEsc(e) { if (e.key === 'Escape') closeModal(); }
function closeModal() {
  const m = document.getElementById('modal-mask');
  if (m) {
    m.remove();
    document.dispatchEvent(new Event('modal-closed'));
  }
  document.removeEventListener('keydown', _modalEsc);
}
/* openModal(内部HTML) → 遮罩元素；点遮罩/按 Esc 关闭 */
function openModal(html) {
  closeModal();
  const mask = document.createElement('div');
  mask.className = 'modal-mask';
  mask.id = 'modal-mask';
  mask.innerHTML = '<div class="modal" role="dialog" aria-modal="true">' + html + '</div>';
  mask.addEventListener('mousedown', e => { if (e.target === mask) closeModal(); });
  document.body.appendChild(mask);
  document.addEventListener('keydown', _modalEsc);
  const first = mask.querySelector('input,select,textarea');
  if (first) setTimeout(() => first.focus(), 30);
  return mask;
}
/* promptModal(标题, 字段[], 确认文案) → Promise<值对象|null>
   字段：{key,label,value,placeholder,type}；回车=确认，Esc/取消=null */
function promptModal(title, fields, okText = '确定') {
  return new Promise(resolve => {
    let settled = false;
    const done = val => {
      if (settled) return;
      settled = true;
      document.removeEventListener('modal-closed', onClosed);
      closeModal();
      resolve(val);
    };
    const onClosed = () => done(null);  // Esc/遮罩关闭 → 取消
    const rows = fields.map(f =>
      '<label class="modal-field">' + esc(f.label) +
      '<input id="pm-' + esc(f.key) + '" type="' + esc(f.type || 'text') + '" value="' + esc(f.value || '') + '"' +
      (f.placeholder ? ' placeholder="' + esc(f.placeholder) + '"' : '') + '></label>').join('');
    const mask = openModal(
      '<h3>' + esc(title) + '</h3>' + rows +
      '<div class="modal-actions">' +
      '<button id="pm-cancel">取消</button>' +
      '<button id="pm-ok" class="primary">' + esc(okText) + '</button></div>');
    document.addEventListener('modal-closed', onClosed, { once: true });
    mask.querySelector('#pm-cancel').onclick = () => done(null);
    mask.querySelector('#pm-ok').onclick = () => {
      const out = {};
      fields.forEach(f => { out[f.key] = mask.querySelector('#pm-' + f.key).value; });
      done(out);
    };
    mask.addEventListener('keydown', e => {
      if (e.key === 'Enter' && e.target.tagName === 'INPUT') {
        e.preventDefault(); mask.querySelector('#pm-ok').click();
      }
    });
  });
}
/* confirmModal(标题, 说明, 确认文案, 是否危险操作) → Promise<boolean> */
function confirmModal(title, message, okText = '确定', danger = false) {
  return new Promise(resolve => {
    let settled = false;
    const done = v => {
      if (settled) return;
      settled = true;
      document.removeEventListener('modal-closed', onClosed);
      closeModal();
      resolve(v);
    };
    const onClosed = () => done(false);
    const mask = openModal(
      '<h3>' + esc(title) + '</h3>' +
      '<p class="muted" style="margin:6px 0 0">' + esc(message) + '</p>' +
      '<div class="modal-actions"><button id="cm-cancel">取消</button>' +
      '<button id="cm-ok" class="primary"' +
      (danger ? ' style="background:var(--err);border-color:var(--err)"' : '') + '>' +
      esc(okText) + '</button></div>');
    document.addEventListener('modal-closed', onClosed, { once: true });
    mask.querySelector('#cm-cancel').onclick = () => done(false);
    mask.querySelector('#cm-ok').onclick = () => done(true);
  });
}
