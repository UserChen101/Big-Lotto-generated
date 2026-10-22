/* ============================================================
   Viewer — 基于 pdf.js 的连续滚动阅读器
   ------------------------------------------------------------
   关键设计：
   1. 所有页面先建占位（高度按页面尺寸算），保证滚动条长度正确
   2. IntersectionObserver 触发按需渲染，LRU 限制内存里最多 10 页 canvas
   3. 文字层由 pdf.js 的 TextLayer 生成，注释层是独立 DOM，互不干扰
   4. 坐标一律走 viewport.convertToPdfPoint，天然兼容页面旋转
   ============================================================ */

import * as pdfjsLib from '../vendor/pdfjs/pdf.mjs';

const ASSET = (p) => new URL(`../vendor/pdfjs/${p}`, import.meta.url).href;

pdfjsLib.GlobalWorkerOptions.workerSrc = ASSET('pdf.worker.mjs');

const MAX_LIVE_CANVAS = 10;

export class Viewer {
  constructor(scroller, inner, handlers = {}) {
    this.scroller = scroller;
    this.inner = inner;
    this.handlers = handlers;

    this.pdf = null;
    this.pages = [];
    this.scale = 1;
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.current = 0;
    this.annotations = [];
    this.live = new Map();          // index -> 最近使用时间戳
    this.rendering = new Set();
    this.observer = null;
    this._timer = null;
    this.destroyed = false;
  }

  // ─────────────────────────────────────────────────── 加载

  async load(url, { annotations = [], restorePage = 0 } = {}) {
    this.destroy(false);
    this.destroyed = false;
    this.annotations = annotations;

    const doc = await pdfjsLib.getDocument({
      url,
      cMapUrl: ASSET('cmaps/'),
      cMapPacked: true,
      standardFontDataUrl: ASSET('standard_fonts/'),
      disableAutoFetch: false,
      rangeChunkSize: 1 << 18,
    }).promise;

    if (this.destroyed) { doc.destroy(); return null; }
    this.pdf = doc;

    // 一次性拿到所有页的原始尺寸（不渲染，开销很小）
    const first = await doc.getPage(1);
    const vp1 = first.getViewport({ scale: 1 });
    this.scale = this._fitWidthScale(vp1.width) || 1;

    this.pages = [];
    for (let i = 1; i <= doc.numPages; i++) {
      let w = vp1.width, h = vp1.height;
      try {
        const pg = await doc.getPage(i);
        const v = pg.getViewport({ scale: 1 });
        w = v.width; h = v.height;
      } catch { /* 用首页尺寸兜底 */ }
      this.pages.push({
        index: i - 1, w, h, boxEl: null, canvasEl: null,
        textEl: null, annotEl: null, editEl: null, rendered: false, vp: null,
      });
    }

    this._buildSkeleton();
    this._bindScroll();
    this.setScale(this.scale, { silent: true });

    const target = Math.max(0, Math.min(restorePage, this.pages.length - 1));
    requestAnimationFrame(() => {
      this.gotoPage(target, { instant: true });
      this._renderAnnots();
    });
    return doc;
  }

  destroy(reset = true) {
    this.destroyed = true;
    if (this.observer) { this.observer.disconnect(); this.observer = null; }
    if (this._onScroll) { this.scroller.removeEventListener('scroll', this._onScroll); this._onScroll = null; }
    if (this.pdf) { try { this.pdf.destroy(); } catch { /* ignore */ } this.pdf = null; }
    if (reset) {
      this.inner.innerHTML = '';
      this.pages = [];
      this.live.clear();
      this.rendering.clear();
      this.annotations = [];
    }
  }

  _fitWidthScale(pageWidth) {
    const avail = this.scroller.clientWidth - 56;
    if (!avail || !pageWidth) return 1;
    return Math.min(2.4, Math.max(0.25, avail / pageWidth));
  }

  // ─────────────────────────────────────────────────── 骨架

  _buildSkeleton() {
    const frag = document.createDocumentFragment();
    for (const p of this.pages) {
      const box = document.createElement('div');
      box.className = 'page-box';
      box.dataset.page = String(p.index);
      box.style.width = `${p.w * this.scale}px`;
      box.style.height = `${p.h * this.scale}px`;

      const layer = document.createElement('div');
      layer.className = 'annot-layer';
      box.appendChild(layer);

      const editLayer = document.createElement('div');
      editLayer.className = 'edit-layer';
      box.appendChild(editLayer);

      const tag = document.createElement('span');
      tag.className = 'page-num-tag';
      box.appendChild(tag);

      p.boxEl = box;
      p.annotEl = layer;
      p.editEl = editLayer;
      p.tagEl = tag;
      frag.appendChild(box);
    }
    this.inner.replaceChildren(frag);
  }

  _bindScroll() {
    if (this.observer) this.observer.disconnect();
    this.observer = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          const idx = Number(e.target.dataset.page);
          if (e.isIntersecting) this._renderPage(idx);
        }
      },
      { root: this.scroller, rootMargin: '700px 0px', threshold: 0 }
    );
    for (const p of this.pages) this.observer.observe(p.boxEl);

    this._onScroll = () => {
      if (this._timer) return;
      this._timer = setTimeout(() => {
        this._timer = null;
        this._syncCurrentPage();
      }, 90);
    };
    this.scroller.addEventListener('scroll', this._onScroll, { passive: true });
  }

  _syncCurrentPage() {
    const mid = this.scroller.scrollTop + this.scroller.clientHeight * 0.34;
    let idx = 0;
    for (const p of this.pages) {
      const top = p.boxEl.offsetTop;
      if (top <= mid) idx = p.index;
      else break;
    }
    if (idx !== this.current) {
      this.current = idx;
      this.pages.forEach((p) => p.boxEl.classList.toggle('current', p.index === idx));
      this.handlers.onPageChange?.(idx);
    }
  }

  // ─────────────────────────────────────────────────── 渲染

  async _renderPage(idx) {
    if (!this.pdf || this.destroyed) return;
    const p = this.pages[idx];
    if (!p || p.rendered || this.rendering.has(idx)) return;
    this.rendering.add(idx);
    this.live.set(idx, performance.now());

    try {
      const page = await this.pdf.getPage(idx + 1);
      if (this.destroyed || this.pages[idx] !== p) return;

      const vp = page.getViewport({ scale: this.scale });
      const rvp = page.getViewport({ scale: this.scale * this.dpr });
      p.vp = vp;

      const canvas = document.createElement('canvas');
      canvas.className = 'page-canvas';
      canvas.width = Math.floor(rvp.width);
      canvas.height = Math.floor(rvp.height);
      canvas.style.width = `${vp.width}px`;
      canvas.style.height = `${vp.height}px`;

      const ctx = canvas.getContext('2d', { alpha: false });
      ctx.fillStyle = '#fff';
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      await page.render({ canvasContext: ctx, viewport: rvp }).promise;
      if (this.destroyed) return;

      // 尺寸若有偏差（首页尺寸兜底不准），以真实 viewport 为准
      if (Math.abs(p.w * this.scale - vp.width) > 0.5 || Math.abs(p.h * this.scale - vp.height) > 0.5) {
        p.w = vp.width / this.scale;
        p.h = vp.height / this.scale;
        p.boxEl.style.width = `${vp.width}px`;
        p.boxEl.style.height = `${vp.height}px`;
      }

      p.boxEl.replaceChildren(p.annotEl);
      p.boxEl.insertBefore(canvas, p.annotEl);
      p.canvasEl = canvas;
      p.rendered = true;

      // 文字层
      const textContent = await page.getTextContent();
      if (this.destroyed) return;
      const textDiv = document.createElement('div');
      textDiv.className = 'textLayer';
      textDiv.style.setProperty('--scale-factor', String(this.scale));
      const tl = new pdfjsLib.TextLayer({
        textContentSource: textContent,
        container: textDiv,
        viewport: vp,
      });
      await tl.render();
      if (this.destroyed) return;
      p.boxEl.insertBefore(textDiv, p.annotEl);
      p.textEl = textDiv;

      // 编辑层保持最上层（replaceChildren 会把它移除，这里补回来）
      if (p.editEl) p.boxEl.appendChild(p.editEl);

      this.tagEl?.(p);
      this._evictIfNeeded(idx);
      this._renderAnnotsOnPage(p);
      this.handlers.onPageRendered?.(idx);
    } catch (err) {
      console.warn(`[viewer] 第 ${idx + 1} 页渲染失败`, err);
    } finally {
      this.rendering.delete(idx);
    }
  }

  _evictIfNeeded(keepIdx) {
    if (this.live.size <= MAX_LIVE_CANVAS) return;
    const sorted = [...this.live.entries()].sort((a, b) => a[1] - b[1]);
    for (const [idx] of sorted) {
      if (this.live.size <= MAX_LIVE_CANVAS) break;
      if (idx === keepIdx) continue;
      const p = this.pages[idx];
      if (!p || !p.rendered) { this.live.delete(idx); continue; }
      if (p.canvasEl) { p.canvasEl.width = 0; p.canvasEl.height = 0; p.canvasEl.remove(); p.canvasEl = null; }
      if (p.textEl) { p.textEl.remove(); p.textEl = null; }
      p.rendered = false;
      this.live.delete(idx);
    }
  }

  // ─────────────────────────────────────────────────── 缩放 / 跳转

  setScale(next, { silent = false } = {}) {
    const scale = Math.min(6, Math.max(0.15, next));
    if (Math.abs(scale - this.scale) < 0.001 && !silent) return;
    const anchorPage = this.current;
    this.scale = scale;

    for (const p of this.pages) {
      p.boxEl.style.width = `${p.w * scale}px`;
      p.boxEl.style.height = `${p.h * scale}px`;
      p.boxEl.style.overflow = 'hidden';
      if (p.canvasEl) {
        p.canvasEl.style.width = `${p.w * scale}px`;
        p.canvasEl.style.height = `${p.h * scale}px`;
      }
      if (p.textEl) p.textEl.style.setProperty('--scale-factor', String(scale));
      // 强制重渲染以获得清晰像素
      if (p.rendered) {
        if (p.canvasEl) { p.canvasEl.remove(); p.canvasEl = null; }
        if (p.textEl) { p.textEl.remove(); p.textEl = null; }
        p.rendered = false;
      }
      // 编辑块的位置依赖缩放，缩放后作废，等重渲染时重建
      if (p.editEl && p.editEl.childElementCount) p.editEl.replaceChildren();
    }
    this.live.clear();

    this.handlers.onScale?.(scale);
    requestAnimationFrame(() => {
      this.gotoPage(anchorPage, { instant: true, alignTop: true });
      this._renderVisible();
    });
  }

  gotoPage(idx, { instant = false, alignTop = false } = {}) {
    const p = this.pages[Math.max(0, Math.min(idx, this.pages.length - 1))];
    if (!p) return;
    const top = alignTop ? p.boxEl.offsetTop - 14 : p.boxEl.offsetTop - 18;
    this.scroller.scrollTo({ top, behavior: instant ? 'auto' : 'smooth' });
    this.current = p.index;
    this.pages.forEach((q) => q.boxEl.classList.toggle('current', q.index === p.index));
    this.handlers.onPageChange?.(p.index);
    setTimeout(() => this._renderVisible(), instant ? 0 : 260);
  }

  _renderVisible() {
    const top = this.scroller.scrollTop - 800;
    const bottom = this.scroller.scrollTop + this.scroller.clientHeight + 800;
    for (const p of this.pages) {
      const pTop = p.boxEl.offsetTop;
      if (pTop + p.boxEl.offsetHeight >= top && pTop <= bottom) this._renderPage(p.index);
    }
  }

  fitWidth() {
    const w = this.pages[0]?.w || 595;
    this.setScale(this._fitWidthScale(w));
  }

  fitPage() {
    const p = this.pages[this.current];
    if (!p) return;
    const availH = this.scroller.clientHeight - 46;
    const availW = this.scroller.clientWidth - 56;
    this.setScale(Math.min(availW / p.w, availH / p.h));
  }

  // ─────────────────────────────────────────────────── 选区 → PDF 坐标

  /**
   * 把浏览器选区转换为 PDF 坐标系下的矩形数组。
   * 返回 [{page, rects, text}]
   */
  selectionToRects() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) return null;
    const range = sel.getRangeAt(0);
    if (!this.inner.contains(range.commonAncestorContainer)) return null;

    const byPage = new Map();
    for (const p of this.pages) {
      if (!p.textEl || !p.boxEl.contains(range.commonAncestorContainer)) {
        // 跨页选区时仍需处理，用 rect 相交判断
      }
    }

    const boxRectCache = new Map();
    const getBoxRect = (el) => {
      if (!boxRectCache.has(el)) boxRectCache.set(el, el.getBoundingClientRect());
      return boxRectCache.get(el);
    };

    const clientRects = [...range.getClientRects()].filter((r) => r.width > 0.5 && r.height > 0.5);
    if (!clientRects.length) return null;

    for (const cr of clientRects) {
      // 找到这个 rect 落在哪一页
      let host = null;
      for (const p of this.pages) {
        if (!p.vp) continue;
        const br = getBoxRect(p.boxEl);
        if (br.bottom >= cr.top + 1 && br.top <= cr.bottom - 1) { host = p; break; }
      }
      if (!host) continue;
      const br = getBoxRect(host.boxEl);
      const x0 = cr.left - br.left;
      const y0 = cr.top - br.top;
      const x1 = cr.right - br.left;
      const y1 = cr.bottom - br.top;
      const [px0, py0] = host.vp.convertToPdfPoint(x0, y0);
      const [px1, py1] = host.vp.convertToPdfPoint(x1, y1);
      const rect = [
        Math.min(px0, px1), Math.min(py0, py1),
        Math.max(px0, px1), Math.max(py0, py1),
      ];
      if (!byPage.has(host.index)) byPage.set(host.index, { page: host.index, rects: [], text: sel.toString() });
      byPage.get(host.index).rects.push(rect);
    }

    // 合并同一行内断开的矩形，减少注释碎片
    for (const entry of byPage.values()) entry.rects = mergeRects(entry.rects);
    return [...byPage.values()];
  }

  // ─────────────────────────────────────────────────── 注释层

  setAnnotations(list) {
    this.annotations = list || [];
    this._renderAnnots();
  }

  _renderAnnots() {
    for (const p of this.pages) this._renderAnnotsOnPage(p);
  }

  _renderAnnotsOnPage(p) {
    if (!p.annotEl) return;
    p.annotEl.replaceChildren();
    if (!p.vp) {
      // 页面还没渲染过，先用估算 viewport
      const w = p.w * this.scale, h = p.h * this.scale;
      p.annotEl.style.width = `${w}px`;
      p.annotEl.style.height = `${h}px`;
    }
    const vp = p.vp || { convertToViewportRectangle: (r) => [r[0] * this.scale, r[1] * this.scale, r[2] * this.scale, r[3] * this.scale] };

    for (const a of this.annotations) {
      if (a.page !== p.index) continue;
      const color = `rgb(${a.color.map((c) => Math.round(c * 255)).join(',')})`;
      if (a.kind === 'note') {
        const r = a.rects[0] || [40, 40, 60, 60];
        const [x, y] = vp.convertToViewportPoint
          ? vp.convertToViewportPoint(r[0], r[1])
          : [r[0] * this.scale, r[1] * this.scale];
        const pin = document.createElement('div');
        pin.className = 'annot-mark note-pin';
        pin.style.background = color;
        pin.style.left = `${x}px`;
        pin.style.top = `${y - 20}px`;
        pin.title = a.content || '批注';
        pin.dataset.aid = a.id;
        p.annotEl.appendChild(pin);
      } else {
        for (const r of a.rects) {
          const [x0, y0, x1, y1] = vp.convertToViewportRectangle(r);
          const el = document.createElement('div');
          el.className = 'annot-mark';
          el.dataset.aid = a.id;
          el.style.left = `${Math.min(x0, x1)}px`;
          el.style.top = `${Math.min(y0, y1)}px`;
          el.style.width = `${Math.abs(x1 - x0)}px`;
          el.style.height = `${Math.abs(y1 - y0)}px`;
          if (a.kind === 'highlight') {
            el.style.background = color;
            el.style.opacity = String(a.opacity ?? 0.42);
            el.style.mixBlendMode = 'multiply';
          } else if (a.kind === 'underline') {
            el.style.borderBottom = `2px solid ${color}`;
            el.style.height = `${Math.max(2, Math.abs(y1 - y0))}px`;
          } else if (a.kind === 'strikeout') {
            const line = document.createElement('div');
            line.style.cssText = `position:absolute;left:0;right:0;top:55%;height:1.8px;background:${color}`;
            el.appendChild(line);
          }
          if (a.content) el.title = a.content;
          p.annotEl.appendChild(el);
        }
      }
    }
  }

  getPageViewport(index) {
    return this.pages[index]?.vp || null;
  }

  // 屏幕坐标 → {page, rect}
  pointToPdf(clientX, clientY) {
    for (const p of this.pages) {
      const br = p.boxEl.getBoundingClientRect();
      if (clientX >= br.left && clientX <= br.right && clientY >= br.top && clientY <= br.bottom) {
        const vp = p.vp || { convertToPdfPoint: (x, y) => [x / this.scale, y / this.scale] };
        const [x, y] = vp.convertToPdfPoint(clientX - br.left, clientY - br.top);
        return { page: p.index, x, y };
      }
    }
    return null;
  }

  // ─────────────────────────────────────────────────── 搜索

  async *searchAll(keyword) {
    if (!this.pdf || !keyword) return;
    const needle = keyword.toLowerCase();
    for (let i = 1; i <= this.pdf.numPages; i++) {
      let content;
      try { content = await (await this.pdf.getPage(i)).getTextContent(); }
      catch { continue; }
      const hits = [];
      for (const item of content.items) {
        if (!item.str) continue;
        const hay = item.str.toLowerCase();
        let at = hay.indexOf(needle);
        while (at !== -1) {
          hits.push({ page: i - 1, text: item.str, offset: at });
          at = hay.indexOf(needle, at + needle.length);
        }
      }
      if (hits.length) yield { page: i - 1, hits };
    }
  }

  // 高亮某页上所有匹配（简单版：整行着色）
  async paintSearchHits(pages, keyword) {
    this.clearSearchHits();
    if (!keyword) return;
    const needle = keyword.toLowerCase();
    for (const p of this.pages) {
      if (!pages.includes(p.index) || !p.vp) continue;
      let content;
      try { content = await (await this.pdf.getPage(p.index + 1)).getTextContent(); }
      catch { continue; }
      for (const item of content.items) {
        if (!item.str || !item.str.toLowerCase().includes(needle)) continue;
        const tx = pdfjsLib.Util.transform(p.vp.transform, item.transform);
        const fontH = Math.hypot(tx[2], tx[3]) || 10;
        const width = item.width * this.scale;
        const el = document.createElement('div');
        el.className = 'search-hit';
        el.style.left = `${tx[4]}px`;
        el.style.top = `${tx[5] - fontH}px`;
        el.style.width = `${Math.max(6, width)}px`;
        el.style.height = `${fontH * 1.22}px`;
        p.annotEl.parentElement.insertBefore(el, p.annotEl);
      }
    }
  }

  clearSearchHits() {
    this.inner.querySelectorAll('.search-hit').forEach((n) => n.remove());
  }

  tagEl(p) {
    if (!p.tagEl) return;
    p.tagEl.textContent = `${p.index + 1} / ${this.pages.length}`;
  }
}

/** 把同一水平线上相邻/重叠的矩形合并，避免高亮断成碎片 */
function mergeRects(rects) {
  if (rects.length < 2) return rects;
  const sorted = [...rects].sort((a, b) => a[1] - b[1] || a[0] - b[0]);
  const out = [];
  for (const r of sorted) {
    const last = out[out.length - 1];
    const sameLine = last && Math.abs(last[1] - r[1]) < 3 && Math.abs(last[3] - r[3]) < 3;
    const adjacent = last && r[0] <= last[2] + 4;
    if (sameLine && adjacent) {
      last[2] = Math.max(last[2], r[2]);
      last[3] = Math.max(last[3], r[3]);
    } else {
      out.push([...r]);
    }
  }
  return out;
}

export { pdfjsLib };
