// ==UserScript==
// @name         微信文章提取（保留排版）
// @namespace    https://github.com/mengdblg/wechat-article-copier
// @version      1.0.0
// @description  提取公众号正文、图片和常见排版，复制 HTML+纯文本，支持 Alt+C 和 HTML 下载。
// @match        *://mp.weixin.qq.com/*
// @run-at       document-idle
// @noframes
// @icon         https://mp.weixin.qq.com/favicon.ico
// @grant        GM_addStyle
// ==/UserScript==
// Generated from scripts/extractor.js + clipboard.js + userscript-ui.js.
// Derived from the author's WeChat Article Copier 1.3.0.
/* Shared browser extraction engine. No network calls or clipboard writes on load. */
(() => {
  'use strict';
  const selector = '#js_content, .rich_media_content, [id^="js_content"]';
  const ignored = '.qr_code_pc, .js_pc_qr_code, .reward_area, .rich_media_tool, .weui-dialog, .wase-toolbar, .wase-toast';
  const forbidden = new Set(['SCRIPT', 'IFRAME', 'OBJECT', 'EMBED', 'STYLE', 'LINK', 'META', 'BASE', 'FORM', 'INPUT', 'BUTTON', 'TEXTAREA', 'SELECT', 'NOSCRIPT', 'TEMPLATE', 'FOREIGNOBJECT', 'ANIMATE', 'ANIMATETRANSFORM', 'SET']);
  const allowed = new Set('div section article main header footer p span a br hr h1 h2 h3 h4 h5 h6 strong b em i u s del ins small sub sup mark blockquote pre code ul ol li dl dt dd figure figcaption img picture source video audio table thead tbody tfoot tr td th colgroup col caption svg g path rect circle ellipse line polyline polygon text tspan defs lineargradient radialgradient stop clippath mask use title desc'.split(' '));
  const styles = ('font-family font-size font-weight font-style font-variant line-height letter-spacing color text-align text-indent text-decoration white-space word-break overflow-wrap background-color background-image background-position background-size background-repeat border-top border-right border-bottom border-left border-radius margin-top margin-right margin-bottom margin-left padding-top padding-right padding-bottom padding-left display vertical-align float clear box-sizing text-overflow list-style-type list-style-position border-collapse border-spacing table-layout flex-direction flex-wrap justify-content align-items align-content gap flex-grow flex-shrink flex-basis grid-template-columns grid-template-rows box-shadow opacity fill stroke stroke-width').split(' ');
  const attributes = new Set('alt title colspan rowspan scope start reversed type width height viewbox preserveaspectratio d x y x1 y1 x2 y2 cx cy r rx ry points fill fill-opacity fill-rule stroke stroke-width stroke-linecap stroke-linejoin stroke-dasharray stroke-opacity transform offset stop-color stop-opacity gradientunits gradienttransform clippathunits maskunits id xmlns role aria-label'.split(' '));
  const blocks = new Set('DIV SECTION ARTICLE P H1 H2 H3 H4 H5 H6 BLOCKQUOTE PRE UL OL LI FIGURE FIGCAPTION TABLE TR DL DT DD'.split(' '));
  function safeURL(value, base, image = false) {
    if (!value) return '';
    const trimmed = value.trim();
    if (image && /^data:image\/(?:png|jpeg|gif|webp|avif);base64,[a-z\d+/=\s]+$/i.test(trimmed)) return trimmed;
    if (trimmed.startsWith('#')) return trimmed;
    try {
      const u = new URL(trimmed, base);
      return ['https:', 'http:'].includes(u.protocol) && !u.username && !u.password ? u.href : '';
    } catch { return ''; }
  }
  function safeCSS(value, base) {
    if (/expression\s*\(|javascript\s*:|@import|[<>]|\\/i.test(value)) return '';
    let invalid = false;
    const result = value.replace(/url\(\s*(['"]?)(.*?)\1\s*\)/gi, (_, quote, raw) => {
      const url = safeURL(raw, base, true);
      if (!url) invalid = true;
      return `url("${url.replace(/"/g, '%22')}")`;
    });
    return invalid ? '' : result;
  }
  function plainText(root) {
    function visit(node) {
      if (node.nodeType === Node.TEXT_NODE) return node.textContent;
      if (node.nodeType !== Node.ELEMENT_NODE) return '';
      if (node.tagName === 'BR') return '\n';
      if (node.tagName === 'IMG') return node.alt ? `[图片：${node.alt}]` : '';
      const value = Array.from(node.childNodes, visit).join('');
      if (['TD', 'TH'].includes(node.tagName)) return value + '\t';
      return blocks.has(node.tagName) ? '\n' + value + '\n' : value;
    }
    return visit(root).replace(/\r/g, '').replace(/[ \t]+\n/g, '\n').replace(/\n[ \t]+/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  }
  const escapeHTML = value => String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[ch]));
  function makeDocument(title, html) {
    return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src https: http: data:; media-src https: http:; style-src 'unsafe-inline'; font-src https: data:; base-uri 'none'; form-action 'none'"><title>${escapeHTML(title)}</title><style>body{margin:0;padding:32px 20px;background:#fff;color:#222;font:16px/1.75 system-ui,'Microsoft YaHei',sans-serif}main{max-width:677px;margin:auto;overflow-wrap:anywhere}img,video,svg{max-width:100%}pre{overflow:auto}table{max-width:100%}a{color:#167756}</style></head><body><main>${html}</main></body></html>`;
  }
  function extract(options = {}) {
    const content = document.querySelector(selector);
    if (!content) throw new Error('未找到公众号正文。页面可能需要验证、已删除，或尚未加载完成。');
    const contentStyle = getComputedStyle(content);
    if (contentStyle.display === 'none' || contentStyle.visibility === 'hidden' || contentStyle.opacity === '0') throw new Error('正文仍处于隐藏状态，请等待文章显示后重试。');
    const title = document.querySelector('#activity-name, #js_title')?.textContent.trim() || document.title || '未命名文章';
    const author = document.querySelector('#js_name')?.textContent.trim() || '';
    const time = document.querySelector('#publish_time')?.textContent.trim() || '';
    const sourceURL = safeURL(options.sourceURL || location.href, location.href);
    const base = sourceURL || document.baseURI;
    const warnings = new Set();
    let imageCount = 0;
    function clone(source) {
      if (source.nodeType === Node.TEXT_NODE) return document.createTextNode(source.textContent);
      if (source.nodeType !== Node.ELEMENT_NODE) return null;
      const tag = source.tagName.toUpperCase();
      if (source.matches(ignored)) return null;
      if (forbidden.has(tag)) {
        if (['IFRAME', 'OBJECT', 'EMBED'].includes(tag)) warnings.add('内嵌内容已省略；视频、音频或小程序卡片不能保证保留。');
        return null;
      }
      const cs = getComputedStyle(source);
      if (source !== content && (cs.display === 'none' || cs.visibility === 'hidden')) return null;
      if (!allowed.has(tag.toLowerCase())) {
        warnings.add('部分微信专用组件已转为普通内容，交互效果不保留。');
        const fragment = document.createDocumentFragment();
        for (const child of source.childNodes) { const copy = clone(child); if (copy) fragment.append(copy); }
        return fragment;
      }
      const out = source.cloneNode(false);
      for (const attr of Array.from(out.attributes)) out.removeAttribute(attr.name);
      for (const attr of source.attributes) {
        if (attributes.has(attr.name.toLowerCase())) out.setAttribute(attr.name, attr.value);
      }
      for (const property of styles) {
        const value = safeCSS(cs.getPropertyValue(property), base);
        if (value) out.style.setProperty(property, value);
      }
      // Preserve authored dimensions without freezing every paragraph to viewport pixels.
      for (const property of ['width', 'height', 'min-width', 'max-width', 'min-height', 'max-height']) {
        const value = safeCSS(source.style.getPropertyValue(property), base);
        if (value) out.style.setProperty(property, value);
      }
      // A stylesheet-defined table width would otherwise collapse to its text width.
      if (tag === 'TABLE' && !out.style.width) {
        const parentWidth = source.parentElement?.clientWidth || 0;
        const tableWidth = source.getBoundingClientRect().width;
        if (parentWidth && tableWidth) out.style.width = `${Math.min(100, tableWidth / parentWidth * 100)}%`;
      }
      if (cs.display === 'list-item') out.style.listStyleType = cs.listStyleType;
      if (tag === 'A') {
        const href = safeURL(source.getAttribute('href'), base);
        if (href) out.setAttribute('href', href);
        out.setAttribute('rel', 'noopener noreferrer');
      }
      if (tag === 'USE') {
        const href = source.getAttribute('href') || source.getAttribute('xlink:href');
        if (href?.startsWith('#')) out.setAttribute('href', href);
      }
      if (tag === 'IMG') {
        imageCount++;
        const candidates = ['data-src', 'data-original', 'data-backsrc', 'src'].map(name => source.getAttribute(name));
        const src = candidates.map(value => safeURL(value, base, true)).find(Boolean);
        if (src) out.src = src; else warnings.add('有图片未找到可用地址。');
        if (source.getAttribute('data-type')) out.setAttribute('data-type', source.getAttribute('data-type'));
        out.loading = 'eager'; out.decoding = 'sync'; out.referrerPolicy = 'no-referrer';
        out.style.maxWidth = '100%'; out.style.height = 'auto';
      } else if (['VIDEO', 'AUDIO', 'SOURCE'].includes(tag)) {
        const src = safeURL(source.getAttribute('src') || source.getAttribute('data-src'), base);
        if (src) out.setAttribute('src', src);
        if (tag !== 'SOURCE') {
          out.setAttribute('controls', '');
          const poster = safeURL(source.getAttribute('poster'), base, true);
          if (poster) out.setAttribute('poster', poster);
          warnings.add('音视频仅保留可用资源链接；目标编辑器可能不支持。');
        }
      } else if (['DIV', 'SECTION', 'SPAN'].includes(tag) && cs.backgroundImage === 'none') {
        const lazy = safeURL(source.getAttribute('data-src'), base, true);
        if (lazy) out.style.backgroundImage = `url("${lazy.replace(/"/g, '%22')}")`;
      }
      for (const pseudo of ['::before', '::after']) {
        const generated = getComputedStyle(source, pseudo).content;
        if (generated && !['none', 'normal', '""'].includes(generated)) warnings.add('部分伪元素装饰不能完整保留。');
      }
      for (const child of source.childNodes) { const copy = clone(child); if (copy) out.append(copy); }
      return out;
    }
    const body = clone(content);
    if (!body || !(body.textContent.trim() || body.querySelector('img,svg,video,audio'))) throw new Error('正文为空，未生成提取结果。');
    body.removeAttribute('id');
    body.style.maxWidth = '100%';
    const meta = [author, time].filter(Boolean).join(' · ');
    const sourceLine = sourceURL ? `<p style="font-size:12px;color:#777;margin:20px 0 0">来源：<a href="${escapeHTML(sourceURL)}" rel="noopener noreferrer">原文链接</a></p>` : '';
    const bodyHtml = body.outerHTML;
    const fragmentHtml = `<section style="font-family:system-ui,'Microsoft YaHei',sans-serif;color:#222;line-height:1.75"><h1 style="font-size:24px;line-height:1.4;margin:0 0 12px">${escapeHTML(title)}</h1>${meta ? `<p style="color:#777;font-size:13px;margin:0 0 24px">${escapeHTML(meta)}</p>` : ''}${bodyHtml}${sourceLine}</section>`;
    const bodyText = plainText(body);
    const text = [title, meta, bodyText, sourceURL ? `来源：${sourceURL}` : ''].filter(Boolean).join('\n\n');
    if (imageCount) warnings.add('图片使用原站链接，未下载到本地；请检查目标编辑器中的图片显示和保存结果。');
    return { version: '1.0.0', title, author, time, meta, sourceURL, imageCount, warnings: [...warnings], bodyHtml, fragmentHtml, bodyText, text, html: makeDocument(title, fragmentHtml) };
  }
  globalThis.WechatStyledExtractor = { extract, makeDocument, selector };
})();

/* Browser helper shared by userscript and exported preview. */
(() => {
  async function copy(html, text) {
    const active = document.activeElement;
    const selection = window.getSelection();
    const ranges = selection ? Array.from({ length: selection.rangeCount }, (_, i) => selection.getRangeAt(i).cloneRange()) : [];
    if (navigator.clipboard?.write && globalThis.ClipboardItem) {
      try {
        await navigator.clipboard.write([new ClipboardItem({ 'text/html': new Blob([html], { type: 'text/html' }), 'text/plain': new Blob([text], { type: 'text/plain' }) })]);
        return 'rich';
      } catch { /* A real user click and legacy copy may still work. */ }
    }
    const field = document.createElement('textarea');
    field.value = text;
    field.style.cssText = 'position:fixed;left:-10000px;top:0';
    document.body.append(field); field.select();
    let handled = false;
    function onCopy(event) {
      if (!event.clipboardData) return;
      try {
        event.clipboardData.setData('text/html', html);
        event.clipboardData.setData('text/plain', text);
        event.preventDefault(); handled = true;
      } catch { handled = false; }
    }
    try {
      document.addEventListener('copy', onCopy);
      let success = false;
      try { success = document.execCommand('copy'); } catch { /* Report actual failure below. */ }
      document.removeEventListener('copy', onCopy);
      if (success) return handled ? 'rich' : 'plain';
      try { if (document.execCommand('copy')) return 'plain'; } catch { /* No clipboard access. */ }
      throw new Error('浏览器未允许复制，请在本机浏览器中重新打开页面并允许剪贴板访问后点击复制。');
    } finally {
      document.removeEventListener('copy', onCopy);
      field.remove();
      active?.focus?.({ preventScroll: true });
      if (selection) {
        selection.removeAllRanges();
        for (const range of ranges) { try { selection.addRange(range); } catch {} }
      }
    }
  }
  globalThis.WechatStyledClipboard = { copy };
})();

(() => {
  'use strict';
  if (window.top !== window.self || globalThis.__wechatStyledUI) return;
  globalThis.__wechatStyledUI = true;
  const css = '.wase-toolbar{position:fixed;right:18px;bottom:18px;z-index:2147483646;display:flex;gap:8px}.wase-toolbar button{border:0;border-radius:10px;padding:12px 16px;background:#16845d;color:white;font:14px system-ui;cursor:pointer;box-shadow:0 6px 18px #0002}.wase-toolbar button:hover{background:#116f4e}.wase-toast{position:fixed;bottom:80px;left:50%;transform:translateX(-50%);z-index:2147483647;max-width:85vw;padding:12px 18px;border-radius:10px;background:#183e32;color:white;font:14px/1.6 system-ui}';
  try { if (typeof GM_addStyle === 'function') GM_addStyle(css); } catch {}
  const style = document.createElement('style'); style.textContent = css; document.documentElement.append(style);
  function toast(text) { document.querySelector('.wase-toast')?.remove(); const el = document.createElement('div'); el.className = 'wase-toast'; el.setAttribute('role', 'status'); el.textContent = text; document.body.append(el); setTimeout(() => el.remove(), 5000); }
  async function copy() {
    try { const result = WechatStyledExtractor.extract(); const mode = await WechatStyledClipboard.copy(result.fragmentHtml, result.text); toast(mode === 'rich' ? '已复制正文与排版，请检查粘贴后的图片。' : '仅复制了纯文本，格式未复制；请下载 HTML。'); }
    catch (error) { toast('复制失败：' + error.message); }
  }
  function download() {
    try { const result = WechatStyledExtractor.extract(); const url = URL.createObjectURL(new Blob([result.html], { type: 'text/html;charset=utf-8' })); const a = document.createElement('a'); a.href = url; a.download = (result.title.replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').slice(0,80) || '微信文章') + '.html'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 30000); }
    catch (error) { toast('导出失败：' + error.message); }
  }
  function mount() {
    if (document.querySelector('.wase-toolbar')) return;
    const toolbar = document.createElement('div'); toolbar.className = 'wase-toolbar';
    for (const [label, action] of [['一键复制（含样式）', copy], ['下载 HTML', download]]) { const button = document.createElement('button'); button.textContent = label; button.type = 'button'; button.addEventListener('click', action); toolbar.append(button); }
    document.body.append(toolbar);
  }
  function ready() {
    if (document.querySelector(WechatStyledExtractor.selector)) { mount(); return; }
    const observer = new MutationObserver(() => { if (document.querySelector(WechatStyledExtractor.selector)) { observer.disconnect(); mount(); } });
    observer.observe(document.documentElement, { childList: true, subtree: true });
    setTimeout(() => { observer.disconnect(); mount(); }, 5000);
  }
  document.addEventListener('keydown', event => { if (event.altKey && event.key.toLowerCase() === 'c') { event.preventDefault(); copy(); } });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready, { once: true }); else ready();
})();
