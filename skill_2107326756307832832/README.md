# wechat-article-scraper v1.1.0

微信公众号图文内容爬取与多平台知识库导出工具。

## 功能特性

- **三大爬取模式**：搜狗微信搜索发现 + URL直接抓取 + 批量一体化
- **四种输出格式**：Markdown / Text / HTML / JSON，另有纯文本 Markdown 目录 + index.csv
- **三大知识库导出**：IMA / Dify / Obsidian 一键同步
- **智能去重**：规范链接 + 内容哈希 + 标题相似度，并给出重复原因
- **过滤排序**：日期范围 + 关键词 + 相关性排序
- **离线分析**：抽取式摘要 + 高频关键词 + 批量统计（无需 API Key）
- **增量采集**：爬取历史记账，`filter_new_articles` 只保留未见过的文章
- **图片本地化**：批量下载文章配图并改写 Markdown 链接
- **跳转解析**：搜狗 `link?url=` 是 JS 拼接跳转页，会自动还原真实链接，并复用搜索会话
  （无 Cookie 的请求会被搜狗当作反爬目标）
- **请求限速**：自动限速 + 重试 + 缓存（TTL、失败不缓存、按跳转链接与稳定链接双键）+ 反爬验证页识别
- **MCP Server**：21 个工具供 AI 助手直接调用

## 安装

```bash
pip install -r requirements.txt
```

## 使用

### 命令行

```python
from scrapers.sogou_search import SogouWeChatSearcher
from scrapers.article_fetcher import ArticleFetcher
from core.state_store import StateStore
from core.summarizer import build_digest
from exporters.markdown_exporter import MarkdownExporter

# 搜索（命中搜狗验证页时 blocked 为 True，而不是静默返回空）
searcher = SogouWeChatSearcher()
articles = searcher.search_account_articles("人民日报", keyword="AI")
if searcher.blocked:
    print("被限流，请降速或配置代理")

# 获取全文（搜狗 /link?url= 跳转链接同样支持）
fetcher = ArticleFetcher()
articles = fetcher.enrich_articles(articles)

# 增量：只保留历史里没出现过的文章
state = StateStore()
new_articles, known = state.filter_new(articles)
state.record(new_articles)

# 离线摘要
print(build_digest(new_articles)["digest_markdown"])

# 纯文本导出（ZIP 是二进制，默认落系统临时目录）
exporter = MarkdownExporter()
exporter.export(new_articles, folder="rmrb_ai")
```

### MCP Server

```bash
python mcp_server.py
```

## 配置

编辑 `config.json` 设置：
- `sogou.*`：搜狗搜索参数（限速 / 代理 / `raise_on_block`）
- `fetch.*`：文章获取参数（限速 / 代理 / `cache_max_age_days`）
- `ima.api_key`：IMA知识库API Key
- `dify.api_key`：Dify API Key
- `obsidian.vault_path`：Obsidian vault路径
- `export.default_output_dir`：导出目录，留空则写入系统临时目录

环境变量优先级更高：`WECHAT_SCRAPER_EXPORT_DIR`（导出）、`WECHAT_SCRAPER_DATA_DIR`（缓存/历史）。

## 测试

```bash
python run_tests.py           # 261 项，含真实子进程 stdio 协议测试
pytest tests/ -v              # 直接跑也可，但不会做 .pyc 完整性检查
```

## 清理

上传技能包前执行，去掉 `.pyc` / `__pycache__` / 缓存 / 导出等运行期产物：

```bash
python clean.py
python clean.py --check       # 只报告，不删除
```

`data/scrape_history.json` 是你的爬取历史（不是构建产物），默认**不会**被清理；
只有显式 `python clean.py --all` 才会连同它一起删除。

# 二进制清理

技能目录禁止残留二进制文件（`__pycache__`、`.pyc`、`.db`、`.log` 等），上传会被平台拒绝。
每次改完代码和打包上传前，先跑一遍校验：

```bash
python clean.py --check    # 通过输出「干净：无二进制/运行期产物」，退出码 0
```

`data/scrape_history.json` 是技能核心数据文件，清理脚本默认保留它。

## 许可证

MIT
