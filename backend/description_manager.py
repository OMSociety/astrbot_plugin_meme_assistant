"""
表情包逐文件描述管理器

管理 meme_descriptions.json，为每张表情包图片存储 LLM 识别的描述、标签等元数据。
与 category_manager.py（管理类别级描述）平行协作。
"""

import threading
from datetime import datetime, timedelta, timezone

from astrbot.api import logger

from ..config import MEME_DESCRIPTIONS_PATH
from ..utils import load_json, save_json

# 默认空数据结构
DEFAULT_DESCRIPTIONS = {"version": 1, "entries": {}}

# CST 时区
CST = timezone(offset=timedelta(hours=8))


def _now_iso() -> str:
    """返回当前 CST 时间的 ISO 格式字符串"""
    return datetime.now(CST).isoformat()


def _make_key(category: str, filename: str) -> str:
    """生成描述存储的 key：{category}/{filename}"""
    return f"{category}/{filename}"


class DescriptionManager:
    """管理每张表情包的 LLM 识别描述"""

    def __init__(self):
        self._data = self._load()
        self._lock = threading.Lock()

    def _load(self) -> dict:
        """加载描述数据文件"""
        data = load_json(MEME_DESCRIPTIONS_PATH, DEFAULT_DESCRIPTIONS)
        if "version" not in data:
            data["version"] = 1
        if "entries" not in data:
            data["entries"] = {}
        return data

    def _save(self) -> bool:
        """持久化到文件（线程安全）"""
        with self._lock:
            return save_json(self._data, MEME_DESCRIPTIONS_PATH)

    # ── 基础 CRUD ──────────────────────────────────────────

    def get(self, category: str, filename: str) -> dict | None:
        """获取单张表情的描述，不存在返回 None"""
        key = _make_key(category, filename)
        return self._data["entries"].get(key)

    def set(
        self,
        category: str,
        filename: str,
        description: str,
        tags: list[str],
        model: str = "",
    ) -> bool:
        """设置/更新单张表情的描述"""
        key = _make_key(category, filename)
        now = _now_iso()

        existing = self._data["entries"].get(key, {})
        entry = {
            "description": description,
            "tags": tags if isinstance(tags, list) else [],
            "model": model,
            "identified_at": existing.get("identified_at", now),
            "edited_at": None if existing else None,
        }

        # 如果是更新（已有记录），记录编辑时间
        if existing:
            entry["identified_at"] = existing.get("identified_at", now)
            entry["edited_at"] = now

        self._data["entries"][key] = entry
        return self._save()

    def update_description(
        self, category: str, filename: str, description: str
    ) -> bool:
        """仅更新描述文本（WebUI 编辑用）"""
        existing = self.get(category, filename)
        if not existing:
            return False

        return self.set(
            category=category,
            filename=filename,
            description=description,
            tags=existing.get("tags", []),
            model=existing.get("model", ""),
        )

    def update_tags(self, category: str, filename: str, tags: list[str]) -> bool:
        """仅更新标签（WebUI 编辑用）"""
        existing = self.get(category, filename)
        if not existing:
            return False

        return self.set(
            category=category,
            filename=filename,
            description=existing.get("description", ""),
            tags=tags,
            model=existing.get("model", ""),
        )

    def delete(self, category: str, filename: str) -> bool:
        """删除单张表情的描述"""
        key = _make_key(category, filename)
        if key not in self._data["entries"]:
            return False
        del self._data["entries"][key]
        return self._save()

    def delete_category(self, category: str) -> int:
        """删除指定类别下所有描述，返回删除数量"""
        prefix = f"{category}/"
        to_delete = [k for k in self._data["entries"] if k.startswith(prefix)]
        for k in to_delete:
            del self._data["entries"][k]
        if to_delete:
            self._save()
        return len(to_delete)

    # ── 批量查询 ──────────────────────────────────────────

    def get_by_category(self, category: str) -> dict[str, dict]:
        """获取指定类别下所有表情的描述"""
        prefix = f"{category}/"
        return {
            k[len(prefix) :]: v
            for k, v in self._data["entries"].items()
            if k.startswith(prefix)
        }

    def get_all(self) -> dict:
        """获取全部描述数据（只读视图）"""
        return self._data.copy()

    def get_all_entries(self) -> dict[str, dict]:
        """获取所有 entries"""
        return self._data["entries"].copy()

    # ── 搜索 ──────────────────────────────────────────────

    def search(
        self,
        query: str,
        category: str = None,
        limit: int = 10,
    ) -> list[dict]:
        """
        模糊搜索表情包

        匹配策略：
        - description 子串匹配（权重 2.0）
        - tags 精确匹配（权重 1.5）
        - tags 子串模糊匹配（权重 1.0）

        返回按分数降序的结果列表，每个元素包含：
        {category, filename, description, tags, score}
        """
        query_lower = query.lower().strip()
        if not query_lower:
            return []

        results = []
        for key, entry in self._data["entries"].items():
            # 解析 key
            if "/" not in key:
                continue
            entry_category, filename = key.split("/", 1)

            # 类别过滤
            if category and entry_category != category:
                continue

            score = 0.0
            desc_lower = entry.get("description", "").lower()
            tags_lower = [t.lower() for t in entry.get("tags", [])]

            # description 子串匹配
            if query_lower in desc_lower:
                score += 2.0

            # tags 匹配
            for tag in tags_lower:
                if tag == query_lower:
                    score += 1.5
                elif query_lower in tag:
                    score += 1.0

            if score > 0:
                results.append(
                    {
                        "category": entry_category,
                        "filename": filename,
                        "description": entry.get("description", ""),
                        "tags": entry.get("tags", []),
                        "score": round(score, 2),
                    }
                )

        # 按分数降序，取 top N
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    # ── 统计 ──────────────────────────────────────────────

    def get_stats(self) -> dict:
        """获取描述覆盖统计"""
        total = len(self._data["entries"])
        with_description = sum(
            1
            for e in self._data["entries"].values()
            if e.get("description") and e["description"] != "待识别"
        )
        by_category = {}
        for key in self._data["entries"]:
            cat = key.split("/", 1)[0] if "/" in key else "unknown"
            by_category[cat] = by_category.get(cat, 0) + 1

        return {
            "total_entries": total,
            "with_description": with_description,
            "without_description": total - with_description,
            "by_category": by_category,
        }

    # ── 批量操作 ──────────────────────────────────────────

    def get_unidentified(
        self, existing_files: dict[str, list[str]]
    ) -> list[tuple[str, str]]:
        """
        找出尚未识别的文件
        existing_files: {category: [filename, ...]}
        返回: [(category, filename), ...]
        """
        unidentified = []
        for category, filenames in existing_files.items():
            for filename in filenames:
                if not self.get(category, filename):
                    unidentified.append((category, filename))
        return unidentified

    def mark_pending(self, category: str, filename: str) -> bool:
        """标记文件为待识别"""
        key = _make_key(category, filename)
        if key not in self._data["entries"]:
            self._data["entries"][key] = {
                "description": "待识别",
                "tags": [],
                "model": "",
                "identified_at": None,
                "edited_at": None,
            }
            return self._save()
        return True
