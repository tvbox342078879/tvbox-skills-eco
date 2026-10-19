#!/usr/bin/env node
// Optional localhost preview for browsers that restrict file:// clipboard access.
import http from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { parseArgs } from 'node:util';

const { values } = parseArgs({ options: { dir: { type: 'string' }, port: { type: 'string', default: '0' } } });
if (!values.dir) throw new Error('用法：node serve.mjs --dir "提取结果目录" [--port 端口]');
const root = path.resolve(values.dir);
if (!(await stat(root)).isDirectory()) throw new Error('--dir 必须是提取结果目录。');
await stat(path.join(root, 'preview.html'));
const allowed = new Map([['preview.html', 'text/html'], ['article.html', 'text/html'], ['fragment.html', 'text/html'], ['article.txt', 'text/plain'], ['article.json', 'application/json']]);
const port = Number(values.port);
if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error('端口必须为 0 到 65535。');
const server = http.createServer(async (req, res) => {
  const pathname = new URL(req.url, 'http://127.0.0.1').pathname;
  const file = pathname === '/' ? 'preview.html' : pathname.slice(1);
  if (!['GET', 'HEAD'].includes(req.method) || !allowed.has(file)) { res.writeHead(404); res.end(); return; }
  try {
    const data = await readFile(path.join(root, file));
    res.writeHead(200, { 'Content-Type': allowed.get(file) + '; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer' });
    res.end(req.method === 'HEAD' ? undefined : data);
  } catch { res.writeHead(404); res.end(); }
});
server.on('error', error => { console.error(error.message); process.exitCode = 1; });
server.listen(port, '127.0.0.1', () => {
  console.log(JSON.stringify({ url: `http://127.0.0.1:${server.address().port}/`, expiresInMinutes: 30 }));
});
const timer = setTimeout(() => { server.close(); server.closeAllConnections(); }, 30 * 60 * 1000);
timer.unref();
process.on('SIGINT', () => { server.close(); server.closeAllConnections(); });
