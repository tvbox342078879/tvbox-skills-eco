import fs from 'fs';
import path from 'path';

const OUT = 'C:/Users/Lenovo/WorkBuddy/2026-09-13-20-23-38/src';
const STORE = `${OUT}/articles`;
const LIMIT = Number(process.env.LIMIT || 0);       // 0 = all
const CONC = Number(process.env.CONC || 4);
const ONLY = process.env.ONLY || '';                 // 只抓某个账号

const UAS = [
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 MicroMessenger/8.0.40.2040(0x28002837) WeChat/8.0.40',
  'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.40(0x18002832) NetType/WIFI Language/zh_CN',
  'Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.40.2040(0x28002837)',
];

const index = JSON.parse(fs.readFileSync(`${OUT}/index_url.json`, 'utf8'));
let list = ONLY ? index.filter(x => x.account === ONLY) : index;
if (LIMIT) list = list.slice(0, LIMIT);

for (const acc of new Set(index.map(x => x.account))) fs.mkdirSync(path.join(STORE, acc), { recursive: true });

function sanitize(s) {
  return String(s || '').replace(/[\\/:*?"<>|\r\n\t]/g, '_').replace(/\s+/g, ' ').trim().slice(0, 60);
}
function hash(s) { let h = 5381; for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0; return h.toString(36); }
function keyOf(it) { return `${it.account}_${it.date}_${sanitize(it.title)}_${hash(it.url)}`; }

function decode(s) {
  return s.replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&ldquo;/g, '“').replace(/&rdquo;/g, '”')
    .replace(/&hellip;/g, '…').replace(/&mdash;/g, '—').replace(/&#(\d+);/g, (m, d) => String.fromCodePoint(+d));
}

const END_MARKERS = [
  '<div class="rich_media_area_extra', '<div id="js_tags', '<div class="rich_media_tool',
  '<div class="rich_media_extra', '<div id="js_pc_qr_code', 'rich_media_area_extra',
  '<div class="wx_profile_card', '<script type="text/javascript" nonce', 'id="js_article_bottom_bar',
];

function extractText(html) {
  // 允许 class 含换行、属性顺序任意
  const startM = html.match(/<div[^>]*\bid="js_content"[^>]*>/);
  if (!startM) return null;
  const rest = html.slice(startM.index + startM[0].length);
  let end = rest.length;
  for (const m of END_MARKERS) { const i = rest.indexOf(m); if (i >= 0 && i < end) end = i; }
  let h = rest.slice(0, Math.min(end, 400000));
  // 去掉脚注/广告脚本区
  h = h.replace(/<script[\s\S]*?<\/script>/gi, '').replace(/<style[\s\S]*?<\/style>/gi, '');
  // 图片占位
  h = h.replace(/<img[^>]*?data-src="([^"]*)"[^>]*>/gi, '\n[图片] $1\n');
  h = h.replace(/<img[^>]*?src="([^"]*)"[^>]*>/gi, '\n[图片] $1\n');
  h = h.replace(/<\/(p|section|div|li|h1|h2|h3|h4|blockquote|tr)>/gi, '\n');
  h = h.replace(/<br\s*\/?>/gi, '\n');
  h = h.replace(/<[^>]+>/g, '');
  h = decode(h);
  h = h.replace(/[ \t\u00a0]+/g, ' ').replace(/\n{3,}/g, '\n\n').split('\n').map(l => l.trim()).join('\n');
  return h.trim();
}

function extractMeta(html) {
  const t = (html.match(/<meta property="og:title" content="([^"]*)"/) || [])[1]
    || (html.match(/<h1[^>]*id="activity-name"[^>]*>([\s\S]*?)<\/h1>/) || [])[1] || '';
  const ct = (html.match(/var ct = "(\d+)"/) || [])[1] || (html.match(/var create_time = "(\d+)"/) || [])[1];
  const nickname = (html.match(/var nickname = "([^"]*)"/) || [])[1] || (html.match(/id="js_name"[^>]*>\s*([^<]*)/) || [])[1] || '';
  const author = (html.match(/var author = "([^"]*)"/) || [])[1] || (html.match(/<meta name="author" content="([^"]*)"/) || [])[1] || '';
  const imgs = [...html.matchAll(/data-src="(https?:\/\/mmbiz[^"]*)"/g)].map(x => x[1]);
  let date = '';
  if (ct) { const d = new Date(+ct * 1000); date = new Date(d.getTime() + 8 * 3600e3).toISOString().slice(0, 10); }
  return { title: decode(t).trim(), date, nickname: decode(nickname).trim(), author: decode(author).trim(), images: [...new Set(imgs)].slice(0, 30) };
}

async function fetchOne(it, attempt = 0) {
  const ua = UAS[Math.floor(Math.random() * UAS.length)];
  try {
    const ctl = new AbortController();
    const to = setTimeout(() => ctl.abort(), 30000);
    const r = await fetch(it.url, { headers: { 'User-Agent': ua, 'Accept-Language': 'zh-CN,zh;q=0.9', Referer: 'https://mp.weixin.qq.com/' }, signal: ctl.signal });
    clearTimeout(to);
    const html = await r.text();
    const meta = extractMeta(html);
    const text = extractText(html);
    let status = 'ok';
    if (/该内容已被发布者删除|此内容因违规无法查看|该公众号已迁移|此帐号已自主注销|内容已被删除/.test(html)) status = 'deleted';
    else if (/环境异常|请在微信客户端打开/.test(html)) status = 'need-login';
    else if (!text) status = 'unavailable';
    else if (text.length < 40) status = 'too-short';
    return {
      ...it, status,
      realTitle: meta.title || it.title,
      realDate: meta.date || it.date,
      nickname: meta.nickname, realAuthor: meta.author,
      images: meta.images,
      text: text || '',
      chars: (text || '').length,
      fetchedAt: new Date().toISOString(),
    };
  } catch (e) {
    if (attempt < 2) { await new Promise(r => setTimeout(r, 1500 * (attempt + 1))); return fetchOne(it, attempt + 1); }
    return { ...it, status: 'error', error: e.message, text: '', chars: 0, fetchedAt: new Date().toISOString() };
  }
}

const stats = { ok: 0, deleted: 0, blocked: 0, 'no-content': 0, 'need-login': 0, error: 0, skip: 0 };
let done = 0;
const t0 = Date.now();

async function worker(queue) {
  while (queue.length) {
    const it = queue.shift();
    if (!it) break;
    const f = path.join(STORE, it.account, keyOf(it) + '.json');
    if (fs.existsSync(f) && fs.statSync(f).size > 200) { stats.skip++; done++; continue; }
    const res = await fetchOne(it);
    fs.writeFileSync(f, JSON.stringify(res), 'utf8');
    stats[res.status] = (stats[res.status] || 0) + 1;
    done++;
    if (done % 25 === 0) {
      const el = (Date.now() - t0) / 1000;
      console.log(`[${done}/${list.length}] ${el.toFixed(0)}s ${(done / el).toFixed(2)}/s ok=${stats.ok} del=${stats.deleted} no=${stats['no-content']} err=${stats.error} login=${stats['need-login']}`);
    }
    await new Promise(r => setTimeout(r, 120 + Math.random() * 180));
  }
}

console.log(`START: ${list.length} articles, concurrency ${CONC}`);
const sharedQueue = [...list];
await Promise.all(Array.from({ length: CONC }, () => worker(sharedQueue)));
const el = (Date.now() - t0) / 1000;
console.log(`DONE in ${el.toFixed(0)}s`, JSON.stringify(stats));
