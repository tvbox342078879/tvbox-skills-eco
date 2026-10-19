#!/usr/bin/env node
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { writeArtifacts } from './export.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
export function validateArticleURL(value) {
  let url;
  try { url = new URL(value); } catch { throw new Error('请提供完整的微信公众号文章链接。'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.hostname !== 'mp.weixin.qq.com' || url.port || url.username || url.password || !/^\/s(?:\/|$)/.test(url.pathname)) {
    throw new Error('仅支持 mp.weixin.qq.com/s 开头的微信公众号文章链接。');
  }
  url.protocol = 'https:';
  return url.href;
}
export async function launchBrowser(chromium, browser = 'auto', headed = false) {
  const choices = browser === 'auto' ? ['msedge', 'chrome', 'chromium'] : [{ edge: 'msedge', chrome: 'chrome', chromium: 'chromium' }[browser]];
  if (choices.includes(undefined)) throw new Error('--browser 只能为 auto、edge、chrome 或 chromium。');
  for (const channel of choices) {
    try { return await chromium.launch({ headless: !headed, ...(channel === 'chromium' ? {} : { channel }) }); }
    catch (error) { if (/EACCES|EPERM/.test(error.message)) throw new Error('浏览器启动被执行环境限制，请在允许启动浏览器的本地环境运行。'); }
  }
  throw new Error('未能启动浏览器。请安装 Edge/Chrome，或在技能 scripts 目录执行：npx playwright install chromium。');
}
export async function extractPage(page, sourceURL = '', timeout = 30000) {
  try { await page.waitForSelector('#js_content, .rich_media_content, [id^="js_content"]', { state: 'visible', timeout }); }
  catch { throw new Error('未找到公众号正文。请检查链接是否已失效、需要登录或出现微信验证；可在本地加 --headed 手动验证，或使用浏览器油猴版本。'); }
  await page.evaluate(async () => { await Promise.race([document.fonts?.ready || Promise.resolve(), new Promise(resolve => setTimeout(resolve, 2000))]); });
  const engine = await readFile(path.join(here, 'extractor.js'), 'utf8');
  await page.evaluate(engine);
  return page.evaluate(options => globalThis.WechatStyledExtractor.extract(options), { sourceURL });
}
async function main() {
  const { values } = parseArgs({ options: {
    url: { type: 'string' }, html: { type: 'string' }, out: { type: 'string' },
    'source-url': { type: 'string' }, browser: { type: 'string', default: 'auto' },
    timeout: { type: 'string', default: '30' }, headed: { type: 'boolean', default: false },
    doctor: { type: 'boolean', default: false }, help: { type: 'boolean', short: 'h' },
  }, strict: true, allowPositionals: false });
  if (values.help) {
    console.log('微信文章提取（保留排版）\nnode run.mjs --url "https://mp.weixin.qq.com/s/文章标识" --out "输出目录"\nnode run.mjs --html "已保存的文章.html" --out "输出目录"\n可选：--browser auto|edge|chrome|chromium --headed --timeout 120 --source-url "原文链接"\n环境检查：node run.mjs --doctor\n仅输出 preview.html；在预览页中点击“一键复制带格式内容”，不会自动写剪贴板。');
    return;
  }
  if (!values.doctor && Number(Boolean(values.url)) + Number(Boolean(values.html)) !== 1) throw new Error('请指定且仅指定一个输入：--url 或 --html。');
  const timeout = Number(values.timeout) * 1000;
  if (!Number.isFinite(timeout) || timeout < 1000 || timeout > 300000) throw new Error('--timeout 必须在 1 到 300 秒之间。');
  const url = values.url ? validateArticleURL(values.url) : '';
  const sourceURL = values['source-url'] ? validateArticleURL(values['source-url']) : url;
  let chromium;
  try { ({ chromium } = await import('playwright')); }
  catch { throw new Error('缺少 Playwright 依赖。请在技能 scripts 目录执行 npm ci --no-audit --no-fund，或查看 references/runtime.md。'); }
  const browser = await launchBrowser(chromium, values.browser, values.headed);
  try {
    if (values.doctor) { console.log(JSON.stringify({ ok: true, node: process.version, browser: browser.version(), message: '运行依赖和浏览器可用。' })); return; }
    const context = await browser.newContext({ viewport: { width: 1080, height: 900 }, locale: 'zh-CN', javaScriptEnabled: !values.html, acceptDownloads: false, serviceWorkers: 'block' });
    const page = await context.newPage();
    page.on('dialog', dialog => dialog.dismiss());
    context.on('page', opened => { if (opened !== page) opened.close().catch(() => {}); });
    if (url) {
      await page.route('**/*', route => {
        const request = route.request();
        if (request.isNavigationRequest() && request.frame() === page.mainFrame()) {
          try { if (new URL(request.url()).hostname !== 'mp.weixin.qq.com') return route.abort(); } catch { return route.abort(); }
        }
        return route.continue();
      });
    }
    const input = url || pathToFileURL(path.resolve(values.html)).href;
    try { await page.goto(input, { waitUntil: 'domcontentloaded', timeout }); }
    catch (error) { throw new Error('文章页面加载失败：' + error.message.split('\n')[0]); }
    const article = await extractPage(page, sourceURL, timeout);
    const files = await writeArtifacts(article, path.resolve(values.out || 'wechat-exports'));
    console.log(JSON.stringify({ ok: true, title: article.title, imageCount: article.imageCount, warnings: article.warnings, files, clipboard: '尚未复制；请在本机浏览器打开 preview.html 并点击复制按钮。' }, null, 2));
  } finally { await browser.close(); }
}
if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch(error => { console.error(JSON.stringify({ ok: false, error: error.message })); process.exitCode = 1; });
}
