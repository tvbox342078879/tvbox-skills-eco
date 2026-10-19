"""Export articles to IMA knowledge base via OpenAPI."""

import json
import time
from typing import Any, Dict, List, Optional

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.utils import get_logger, RateLimiter, resolve_state_dir


logger = get_logger("ima-exporter")


class ImaExporter:
    """Export articles to Tencent IMA knowledge base."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://ima.qq.com/api",
        timeout: int = 30,
    ):
        if not HAS_REQUESTS:
            raise ImportError("需要安装 requests: pip install requests")

        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.limiter = RateLimiter(1.0, 3.0)

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def export_articles(
        self,
        articles: List[Dict[str, Any]],
        kb_id: Optional[str] = None,
        collection_name: str = "微信公众号文章",
        batch_size: int = 10,
    ) -> Dict[str, Any]:
        """Export articles to IMA knowledge base.

        Args:
            articles: List of article dicts.
            kb_id: IMA knowledge base ID. If None, creates a new one.
            collection_name: Name for the knowledge collection.
            batch_size: Number of articles per batch upload.

        Returns:
            Result dict with success count, error count, and details.
        """
        if not self.api_key:
            return self._generate_local_export(articles, "ima")

        results = {"success": 0, "errors": [], "kb_id": kb_id}

        # Create KB if needed
        if not kb_id:
            kb_id = self._create_knowledge_base(collection_name)
            if not kb_id:
                return {"success": 0, "errors": ["创建知识库失败"], "kb_id": None}
            results["kb_id"] = kb_id

        # Batch upload
        for i in range(0, len(articles), batch_size):
            batch = articles[i:i + batch_size]
            for article in batch:
                self.limiter.wait()
                success = self._upload_article(kb_id, article)
                if success:
                    results["success"] += 1
                else:
                    results["errors"].append(article.get("title", "unknown"))

        logger.info(f"IMA 导出完成: 成功 {results['success']}, 失败 {len(results['errors'])}")
        return results

    def _create_knowledge_base(self, name: str) -> Optional[str]:
        """Create a new IMA knowledge base."""
        try:
            resp = requests.post(
                f"{self.base_url}/knowledge/create",
                headers=self._headers(),
                json={"name": name, "description": f"微信公众号文章自动导入 - {name}"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", {}).get("kb_id")
        except Exception as e:
            logger.error(f"创建知识库失败: {e}")
            return None

    def _upload_article(self, kb_id: str, article: Dict[str, Any]) -> bool:
        """Upload a single article to IMA."""
        title = article.get("title", "未知标题")
        content = article.get("content_text") or article.get("content_markdown", "")
        if not content and article.get("snippet"):
            content = article["snippet"]

        try:
            payload = {
                "kb_id": kb_id,
                "title": title,
                "content": content,
                "metadata": {
                    "source": "wechat",
                    "account": article.get("account", ""),
                    "author": article.get("author", ""),
                    "pub_date": article.get("pub_date", ""),
                    "link": article.get("link", ""),
                    "word_count": article.get("word_count", 0),
                },
            }
            resp = requests.post(
                f"{self.base_url}/knowledge/document",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.warning(f"上传失败 [{title}]: {e}")
            return False

    def _generate_local_export(
        self, articles: List[Dict[str, Any]], target: str
    ) -> Dict[str, Any]:
        """Generate a local export when no API key is configured."""
        export_dir = str(resolve_state_dir() / f"{target}_export")
        os.makedirs(export_dir, exist_ok=True)

        for i, article in enumerate(articles):
            title = article.get("title", f"article_{i}")
            safe_title = "".join(c if c.isalnum() or c in "._- " else "_" for c in title)[:80]
            content = article.get("content_text") or article.get("content_markdown", "")

            md_content = f"# {title}\n\n"
            if article.get("account"):
                md_content += f"来源: {article['account']}\n"
            if article.get("pub_date"):
                md_content += f"日期: {article['pub_date']}\n"
            md_content += f"\n{content}\n"

            path = os.path.join(export_dir, f"{i+1:03d}_{safe_title}.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(md_content)

        logger.info(f"已导出 {len(articles)} 篇文章到本地目录: {export_dir}")
        return {
            "success": len(articles),
            "errors": [],
            "local_path": export_dir,
            "note": f"未配置 {target.upper()} API Key, 已导出到本地目录",
        }