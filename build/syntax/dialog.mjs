/* ============================================================
   Dialog / Toast — 轻量弹窗与提示
   不引入任何 UI 框架，保持单文件可读、无构建步骤
   ============================================================ */

const mask = () => document.getElementById('modal-mask');
const modalEl = () => document.getElementById('modal');
const bodyEl = () => document.getElementById('modal-body');
const footEl = () => document.getElementById('modal-foot');
const titleEl = () => document.getElementById('modal-title');

let escHandler = null;

export function closeModal(result) {
  const m = mask();
  if (m) m.hidden = true;
  if (escHandler) { document.removeEventListener('keydown', escHandler); escHandler = null; }
  if (typeof closeModal._resolve === 'function') {
    closeModal._resolve(result ?? null);
    closeModal._resolve = null;
  }
}

/**
 * 打开一个弹窗。
 * @returns {Promise<any>} 关闭时 resolve 的结果
 */
export function openModal({ title, body, buttons = [], width = 460, onMount }) {
  titleEl().textContent = title || '';
  bodyEl().innerHTML = body;
  modalEl().style.width = `min(${width}px, calc(100vw - 48px))`;

  footEl().replaceChildren();
  for (const b of buttons) {
    const el = document.createElement('button');
    el.className = b.primary ? 'btn-primary' : 'btn-ghost';
    el.textContent = b.label;
    el.addEventListener('click', async () => {
      if (b.onClick) {
        const keep = await b.onClick({ close: closeModal, body: bodyEl() });
        if (keep === false) return;
      }
      closeModal(b.value ?? null);
    });
    footEl().appendChild(el);
  }
  if (!buttons.length) footEl().style.display = 'none';
  else footEl().style.display = '';

  mask().hidden = false;
  escHandler = (e) => { if (e.key === 'Escape') closeModal(null); };
  document.addEventListener('keydown', escHandler);

  const firstInput = bodyEl().querySelector('input, textarea, select');
  if (firstInput) setTimeout(() => firstInput.focus(), 40);
  if (onMount) onMount(bodyEl(), closeModal);

  return new Promise((resolve) => { closeModal._resolve = resolve; });
}

mask()?.addEventListener('mousedown', (e) => {
  if (e.target === mask()) closeModal(null);
});
document.getElementById('modal-close')?.addEventListener('click', () => closeModal(null));

/* ─────────────────────────────── Toast ─────────────────────────────── */

export function toast(message, kind = 'ok', ms = 2600) {
  const box = document.getElementById('toasts');
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = message;
  box.appendChild(el);
  setTimeout(() => {
    el.style.transition = 'opacity .22s, transform .22s';
    el.style.opacity = '0';
    el.style.transform = 'translateY(6px)';
    setTimeout(() => el.remove(), 240);
  }, ms);
  return el;
}

export function toastErr(err) {
  const msg = typeof err === 'string' ? err : err?.message || '操作失败';
  return toast(msg, 'err', 4200);
}

/* ─────────────────────────── 常用输入对话框 ─────────────────────────── */

export function askText({ title, label, value = '', placeholder = '', hint = '', confirmLabel = '确定', multiline = false }) {
  const field = multiline
    ? `<textarea rows="4" placeholder="${escapeAttr(placeholder)}">${escapeHtml(value)}</textarea>`
    : `<input type="text" value="${escapeAttr(value)}" placeholder="${escapeAttr(placeholder)}">`;

  const p = openModal({
    title,
    body: `<div class="field"><label>${escapeHtml(label)}</label>${field}${hint ? `<p class="hint">${escapeHtml(hint)}</p>` : ''}</div>`,
    buttons: [
      { label: '取消', value: null },
      {
        label: confirmLabel, primary: true,
        onClick: ({ body }) => {
          const el = body.querySelector('input, textarea');
          const v = el ? el.value.trim() : '';
          if (!v) { el?.focus(); return false; }
          return v;
        },
      },
    ],
  });
  return p;
}

export function askPassword({ title = '输入密码', label = '这个 PDF 已加密，请输入打开密码', confirmLabel = '解锁' } = {}) {
  return openModal({
    title,
    body: `<div class="field"><label>${escapeHtml(label)}</label>
             <input type="password" placeholder="密码" autocomplete="off">
             <p class="hint">密码只在本机内存中使用，不会上传或记录。</p></div>`,
    buttons: [
      { label: '取消', value: null },
      {
        label: confirmLabel, primary: true,
        onClick: ({ body }) => {
          const el = body.querySelector('input');
          return el && el.value ? el.value : false;
        },
      },
    ],
  });
}

export function confirmDialog({ title, message, confirmLabel = '确定', danger = false }) {
  return openModal({
    title,
    body: `<p style="font-size:13px;line-height:1.75;color:var(--text-2);margin:0 0 6px">${escapeHtml(message)}</p>`,
    buttons: [
      { label: '取消', value: false },
      { label: confirmLabel, primary: true, value: true },
    ],
  });
}

/**
 * 卡片式单选
 * @param {{value,title,desc}[]} options
 */
export function choiceDialog({ title, message = '', options, confirmLabel = '开始' }) {
  const cards = options
    .map(
      (o, i) => `<button class="choice${i === 0 ? ' active' : ''}" data-value="${escapeAttr(o.value)}">
        <span class="t">${escapeHtml(o.title)}</span>
        <span class="d">${escapeHtml(o.desc || '')}</span>
      </button>`
    )
    .join('');

  return openModal({
    title,
    width: 480,
    body: `${message ? `<p style="font-size:12.5px;color:var(--text-2);line-height:1.7;margin:0 0 14px">${escapeHtml(message)}</p>` : ''}
           <div class="choice-grid" style="grid-template-columns:1fr">${cards}</div>`,
    buttons: [
      { label: '取消', value: null },
      {
        label: confirmLabel, primary: true,
        onClick: ({ body }) => body.querySelector('.choice.active')?.dataset.value ?? options[0].value,
      },
    ],
    onMount: (body) => {
      body.querySelectorAll('.choice').forEach((c) => {
        c.addEventListener('click', () => {
          body.querySelectorAll('.choice').forEach((x) => x.classList.remove('active'));
          c.classList.add('active');
        });
      });
    },
  });
}

/* ─────────────────────────────── 工具 ─────────────────────────────── */

export function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
export function escapeAttr(s) {
  return escapeHtml(s).replace(/`/g, '&#96;');
}

export function fmtBytes(n) {
  if (!n || n < 0) return '—';
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}
