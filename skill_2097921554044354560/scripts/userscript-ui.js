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
