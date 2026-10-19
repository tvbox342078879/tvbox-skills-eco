#!/usr/bin/env node
'use strict';

// pickShareLink 取值顺序的自校验（Issue #102515）。
//
// 为什么值得单独测：这个函数是「用户最终拿到的链接」的唯一出口，选错字段的后果是
// 发布看起来成功、用户拿到的链接却打不开，而脚本这侧完全无异常可查 —— 属于
// 静默故障，只能靠断言守住。
//
// 用 node:assert + node:test 而非 vitest/jest：本目录是**沙箱内运行的零依赖
// CommonJS 脚本**（package.json 无 dependencies），引测试框架会破坏这个前提。
//
// 运行：node --test scripts/lib.test.js  （Node 18+ 内置 test runner）

const assert = require('node:assert');
const { test } = require('node:test');

const lib = require('./lib');

test('shareUrl 优先级最高', () => {
  // 启用 sharecode 时只有 shareUrl 含 ?sharecode=；若先取 friendlyDomains 拼裸域名，
  // sandbox-gateway 会拦成「链接不完整」引导页。
  const link = lib.pickShareLink({
    shareUrl: 'https://myapp.release.example.com/?sharecode=ABC23XYZ89',
    domains: { friendlyDomains: ['myapp.release.example.com'] },
    releaseUrl: 'https://myapp.release.example.com/',
  });
  assert.strictEqual(link, 'https://myapp.release.example.com/?sharecode=ABC23XYZ89');
  assert.ok(link.includes('sharecode='), '必须保留访问凭据参数');
});

test('未启用 sharecode 时回落 friendlyDomains（行为不变）', () => {
  const link = lib.pickShareLink({
    domains: { friendlyDomains: ['myapp.release.example.com'] },
    releaseUrl: 'https://fallback.example.com/',
  });
  assert.strictEqual(link, 'https://myapp.release.example.com');
});

test('无 friendlyDomains 时回落 releaseUrl', () => {
  const link = lib.pickShareLink({ releaseUrl: 'https://fallback.example.com/' });
  assert.strictEqual(link, 'https://fallback.example.com/');
});

test('空 friendlyDomains 数组不误判', () => {
  const link = lib.pickShareLink({
    domains: { friendlyDomains: [] },
    releaseUrl: 'https://fallback.example.com/',
  });
  assert.strictEqual(link, 'https://fallback.example.com/');
});

test('什么都没有时返回 null，由调用方报错', () => {
  // publish.js 依赖 null 来抛「发布成功但未取到分享链接」，
  // 返回空串会让它把空链接当成功回给用户。
  assert.strictEqual(lib.pickShareLink({}), null);
  assert.strictEqual(lib.pickShareLink(null), null);
  assert.strictEqual(lib.pickShareLink(undefined), null);
});
