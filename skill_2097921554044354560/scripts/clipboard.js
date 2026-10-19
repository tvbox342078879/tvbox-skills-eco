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
