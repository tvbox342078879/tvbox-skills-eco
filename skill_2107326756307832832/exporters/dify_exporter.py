"""Export articles to Dify knowledge base (Dataset API)."""

import json
from typing import Any, Dict, List, Optional

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.utils import get_logger, RateLimiter, resolve_state_dir


logger = get_logger("dify-exporter")


class DifyExporter:
    """Export articles to Dify knowledge base via Dataset API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.dify.ai/v1",
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
        dataset_id: Optional[str] = None,
        dataset_name: str = "微信公众号文章",
        indexing_technique: str = "high_quality",
        process_rule: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Export articles to Dify Dataset.

        Args:
            articles: List of article dicts.
            dataset_id: Existing Dify Dataset ID. Creates new if None.
            dataset_name: Name for new dataset.
            indexing_technique: 'high_quality' or 'economy'.
            process_rule: Custom document processing rule.

        Returns:
            Result dict with success count and details.
        """
        if not self.api_key:
            return self._generate_local_export(articles)

        results = {"success": 0, "errors": [], "dataset_id": dataset_id}

        # Create dataset if needed
        if not dataset_id:
            dataset_id = self._create_dataset(dataset_name)
            if not dataset_id:
                return {"success": 0, "errors": ["创建 Dify Dataset 失败"], "dataset_id": None}
            results["dataset_id"] = dataset_id

        # Upload documents
        for article in articles:
            self.limiter.wait()
            success = self._upload_document(dataset_id, article, indexing_technique, process_rule)
            if success:
                results["success"] += 1
            else:
                results["errors"].append(article.get("title", "unknown"))

        logger.info(f"Dify 导出完成: 成功 {results['success']}, 失败 {len(results['errors'])}")
        return results

    def _create_dataset(self, name: str) -> Optional[str]:
        """Create a new Dify Dataset."""
        try:
            resp = requests.post(
                f"{self.base_url}/datasets",
                headers=self._headers(),
                json={"name": name},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("id")
        except Exception as e:
            logger.error(f"创建 Dataset 失败: {e}")
            return None

    def _upload_document(
        self,
        dataset_id: str,
        article: Dict[str, Any],
        indexing_technique: str = "high_quality",
        process_rule: Optional[Dict] = None,
    ) -> bool:
        """Upload a single article as a document to Dify Dataset."""
        title = article.get("title", "未知标题")
        content = article.get("content_text") or article.get("content_markdown", "")
        if not content and article.get("snippet"):
            content = article["snippet"]

        # Dify expects text with metadata
        text_with_meta = f"标题: {title}\n"
        if article.get("account"):
            text_with_meta += f"公众号: {article['account']}\n"
        if article.get("author"):
            text_with_meta += f"作者: {article['author']}\n"
        if article.get("pub_date"):
            text_with_meta += f"发布时间: {article['pub_date']}\n"
        if article.get("link"):
            text_with_meta += f"原文链接: {article['link']}\n"
        text_with_meta += f"\n{content}"

        try:
            # Create document via text upload
            payload = {
                "name": f"wechat_{title[:50]}",
                "text": text_with_meta,
                "indexing_technique": indexing_technique,
                "process_rule": process_rule or {
                    "mode": "automatic",
                },
            }
            resp = requests.post(
                f"{self.base_url}/datasets/{dataset_id}/document/create-by-text",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            logger.info(f"已上传: {title}")
            return True
        except Exception as e:
            logger.warning(f"上传失败 [{title}]: {e}")
            return False

    def list_datasets(self) -> List[Dict[str, Any]]:
        """List existing Dify Datasets."""
        if not self.api_key:
            logger.info("未配置 Dify API Key，无法列出 Dataset")
            return []
        try:
            resp = requests.get(
                f"{self.base_url}/datasets",
                headers=self._headers(),
                params={"page": 1, "limit": 100},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json().get("data", [])
        except Exception as e:
            logger.error(f"获取 Dataset 列表失败: {e}")
            return []

    def _generate_local_export(self, articles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate local export when no API key."""
        export_dir = str(resolve_state_dir() / "dify_export")
        os.makedirs(export_dir, exist_ok=True)

        for i, article in enumerate(articles):
            title = article.get("title", f"article_{i}")
            safe_title = "".join(c if c.isalnum() or c in "._- " else "_" for c in title)[:80]
            content = article.get("content_text") or article.get("content_markdown", "")

            text = f"标题: {title}\n"
            if article.get("account"):
                text += f"公众号: {article['account']}\n"
            if article.get("pub_date"):
                text += f"发布时间: {article['pub_date']}\n"
            text += f"\n{content}\n"

            path = os.path.join(export_dir, f"{i+1:03d}_{safe_title}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)

        logger.info(f"已导出 {len(articles)} 篇文章到: {export_dir}")
        return {
            "success": len(articles),
            "errors": [],
            "local_path": export_dir,
            "note": "未配置 Dify API Key, 已导出到本地目录",
        }