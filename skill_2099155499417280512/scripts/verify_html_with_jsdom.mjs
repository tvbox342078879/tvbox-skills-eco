import fs from 'fs';
import { createRequire } from 'module';
const require = createRequire('C:/Users/Lenovo/.workbuddy/binaries/node/workspace/');
const { JSDOM } = require('jsdom');

const ROOT = 'C:/Users/Lenovo/WorkBuddy/2026-09-13-20-23-38';
let html = fs.readFileSync(`${ROOT}/文章全集阅读器.html`, 'utf8');
// 抽出内联脚本，去掉外部 script 引用（改为手动注入）
const inline = [...html.matchAll(/<script(?![^>]*src)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const dataFiles = ['_summary', '九边', '六镇', '卢克文'].map(n => fs.readFileSync(`${ROOT}/资料/数据/${n}.js`, 'utf8'));
html = html.replace(/<script[^>]*src="[^"]*"[^>]*><\/script>/g, '');

const errs = [];
const dom = new JSDOM(html, { runScripts: 'outside-only', pretendToBeVisual: true });
const { window } = dom;
window.addEventListener('error', e => errs.push('window error: ' + e.message));

// 注入数据
for (const d of dataFiles) window.eval(d);
console.log('WB_CORPUS 账号:', Object.keys(window.WB_CORPUS || {}).join(', '));
for (const k of Object.keys(window.WB_CORPUS)) console.log('  ', k, window.WB_CORPUS[k].length, '篇');

// 执行页面脚本
try { window.eval(inline.join('\n')); } catch (e) { console.log('!! 脚本执行异常:', e.message); }

const doc = window.document;
const q = s => doc.querySelector(s);
console.log('\n--- 初始渲染 ---');
console.log('tab 数量:', doc.querySelectorAll('.tab').length);
console.log('列表条目数(默认只看有正文):', doc.querySelectorAll('.item').length);
console.log('年份分组:', [...doc.querySelectorAll('.yg')].map(e => e.textContent).join(' '));
console.log('计数栏:', q('#count').textContent);

// 切到“卢克文”并点击第一项
const tabs = [...doc.querySelectorAll('.tab')];
const lw = tabs.find(t => t.dataset.a === '卢克文');
lw.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
console.log('\n--- 切到卢克文 ---');
console.log('计数栏:', q('#count').textContent, '| 条目数:', doc.querySelectorAll('.item').length);
const first = doc.querySelector('.item');
console.log('首条:', first.querySelector('.dt').textContent, first.querySelector('.tt').textContent.slice(0, 40));
first.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
const h1 = q('.art h1');
console.log('\n--- 点击首条后 ---');
console.log('标题:', h1 && h1.textContent);
console.log('正文段落数:', doc.querySelectorAll('.body p').length);
console.log('正文字符数:', (q('.body')?.textContent || '').length);
console.log('导航:', q('.top-link')?.textContent);

// 搜索
const inp = q('#q');
inp.value = '俄罗斯';
inp.dispatchEvent(new window.Event('input', { bubbles: true }));
console.log('\n--- 搜索“俄罗斯” ---');
console.log('计数栏:', q('#count').textContent, '| 条目数:', doc.querySelectorAll('.item').length);
console.log('首条高亮:', doc.querySelector('.mark') ? doc.querySelector('.mark').textContent : '(无)');

// 全部 + 隐藏推广
tabs[0].dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
console.log('\n--- 切回全部 ---');
console.log('计数栏:', q('#count').textContent);

// 点一个“未取到正文”的条目，检查提示块
inp.value = ''; inp.dispatchEvent(new window.Event('input', { bubbles: true }));
const cb = q('#onlyok'); cb.checked = false; cb.dispatchEvent(new window.Event('change', { bubbles: true }));
const items = [...doc.querySelectorAll('.item')];
const lostIdx = items.findIndex(n => n.querySelector('.pill.lost'));
console.log('\n--- 全部条目(含未取到正文) ---', items.length, '条; 未取到正文首个索引:', lostIdx);
items[lostIdx].dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
console.log('提示块:', q('.notice') ? q('.notice').textContent.slice(0, 90) : '(无)');
console.log('标题:', q('.art h1')?.textContent);

console.log('\n运行时错误:', errs.length ? errs.join(' | ') : '无');
