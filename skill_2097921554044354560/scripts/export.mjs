import { readFile, mkdir, mkdtemp, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
export async function renderPreview(article) {
  const [template, clipboard] = await Promise.all([
    readFile(path.join(here, '../templates/preview.html'), 'utf8'),
    readFile(path.join(here, 'clipboard.js'), 'utf8'),
  ]);
  // JSON inside script must never contain a literal closing script tag.
  const json = JSON.stringify(article).replace(/</g, '\\u003c').replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029');
  return template.replace('__ARTICLE_DATA__', () => json).replace('__CLIPBOARD_SCRIPT__', () => clipboard);
}
export async function writeArtifacts(article, outputRoot) {
  await mkdir(outputRoot, { recursive: true });
  const dir = await mkdtemp(path.join(path.resolve(outputRoot), 'wechat-article-'));
  const preview = path.join(dir, 'preview.html');
  await writeFile(preview, await renderPreview(article), 'utf8');
  return { preview };
}
