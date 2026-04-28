"""表情推荐器 — 加权随机 + 使用频率衰减 + 同会话去重 + 精选标记

用法:
    from ._meme_recommender import MemeRecommender
    rec = MemeRecommender(meme_dir=MEMES_DIR)
    meme = rec.select(category="happy", session_id="chat_123")
    rec.mark_used("happy", "meme_001.jpg", session_id="chat_123")
"""

from __future__ import annotations

import math
import random
import time
from pathlib import Path

from astrbot.api import logger


def _default_weight(category: str, filename: str) -> float:
    """默认权重生成函数（基于文件名哈希，保证稳定性）"""
    seed = hash(f"{category}/{filename}") & 0x7FFFFFFF
    rng = random.Random(seed)
    return 0.5 + rng.random() * 0.5  # 0.5 ~ 1.0


class MemeRecommender:
    """智能表情推荐器。

    算法: weight = curated_bonus * freshness_decay * base_weight

    - curated_bonus: 精选标记加成（默认 2.0x）
    - freshness_decay: 上次使用距今越久权重越高（指数衰减）
    - base_weight: 基于使用次数的反比权重（使用越多权重越低）
    """

    def __init__(
        self,
        meme_dir: str | Path,
        curated_bonus: float = 2.0,
        decay_half_life: float = 3600.0,  # 1 小时半衰期
        max_unused_boost: float = 3.0,  # 从未使用过的最大加成
        curated_tags_file: str | Path | None = None,
    ):
        """初始化推荐器。

        Args:
            meme_dir: 表情包根目录
            curated_bonus: 精选标记的权重加成倍数
            decay_half_life: 时间衰减半衰期（秒）
            max_unused_boost: 从未使用过的最大加成（倍）
            curated_tags_file: 精选标记文件路径（JSON: {"category": ["filename", ...]}）
        """
        self._meme_dir = Path(meme_dir)
        self._curated_bonus = curated_bonus
        self._decay_half_life = decay_half_life
        self._max_unused_boost = max_unused_boost

        # 使用统计: {category: {filename: {"count": int, "last_used": float}}}
        self._usage: dict[str, dict[str, dict]] = {}

        # 同会话去重: {session_id: set(f"{category}/{filename}")}
        self._session_used: dict[str, set[str]] = {}

        # 精选标记: {category: set(filename)}
        self._curated: dict[str, set[str]] = {}
        if curated_tags_file:
            self._load_curated(curated_tags_file)

    # ── 精选标记管理 ──────────────────────────

    def _load_curated(self, path: str | Path):
        """加载精选标记文件。"""
        import json

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for cat, files in data.items():
                self._curated[cat] = set(files)
            logger.info(
                f"[meme_recommender] 加载精选标记: {sum(len(v) for v in self._curated.values())} 个"
            )
        except Exception as e:
            logger.warning(f"[meme_recommender] 加载精选标记失败: {e}")

    def set_curated(self, category: str, filename: str, curated: bool = True):
        """设置/取消精选标记。"""
        if curated:
            self._curated.setdefault(category, set()).add(filename)
        else:
            if category in self._curated:
                self._curated[category].discard(filename)

    def is_curated(self, category: str, filename: str) -> bool:
        """检查是否为精选。"""
        return filename in self._curated.get(category, set())

    # ── 使用追踪 ──────────────────────────────

    def mark_used(
        self,
        category: str,
        filename: str,
        session_id: str | None = None,
        count: int = 1,
    ):
        """标记表情已使用。"""
        t = time.time()
        cat_usage = self._usage.setdefault(category, {})
        entry = cat_usage.setdefault(filename, {"count": 0, "last_used": 0.0})
        entry["count"] += count
        entry["last_used"] = t

        if session_id:
            self._session_used.setdefault(session_id, set()).add(
                f"{category}/{filename}"
            )

    def get_usage_count(self, category: str, filename: str) -> int:
        """获取使用次数。"""
        return self._usage.get(category, {}).get(filename, {}).get("count", 0)

    def get_last_used(self, category: str, filename: str) -> float:
        """获取上次使用时间戳。"""
        return self._usage.get(category, {}).get(filename, {}).get("last_used", 0.0)

    def reset_session(self, session_id: str):
        """重置指定会话的去重记录。"""
        self._session_used.pop(session_id, None)

    # ── 权重计算 ──────────────────────────────

    def _calc_weight(
        self, category: str, filename: str, now: float | None = None
    ) -> float:
        """计算单张表情的综合权重。"""
        if now is None:
            now = time.time()

        # 1. 精选加成
        curated = self._curated_bonus if self.is_curated(category, filename) else 1.0

        # 2. freshness_decay
        last_used = self.get_last_used(category, filename)
        if last_used <= 0:
            freshness = self._max_unused_boost
        else:
            elapsed = now - last_used
            # 指数衰减：半衰期后权重恢复到 1.0
            freshness = 1.0 + (self._max_unused_boost - 1.0) * math.pow(
                2.0, -elapsed / self._decay_half_life
            )

        # 3. 使用频率反比
        usage_count = self.get_usage_count(category, filename)
        frequency = 1.0 / (1.0 + math.log1p(usage_count))  # log1p 平滑

        return curated * freshness * frequency

    # ── 选择算法 ──────────────────────────────

    def select(
        self,
        category: str,
        memes: list[str],
        session_id: str | None = None,
    ) -> str | None:
        """加权随机选择一张表情。

        Args:
            category: 表情类别
            memes: 该类别下的文件名列表
            session_id: 会话 ID，用于去重

        Returns:
            选中的文件名，或 None（无法选择时）
        """
        if not memes:
            return None

        # 过滤同会话已用的
        candidates = memes
        if session_id:
            used = self._session_used.get(session_id, set())
            candidates = [
                m for m in memes if f"{category}/{m}" not in used
            ] or memes  # 全都用过就放宽限制

        if not candidates:
            return None

        now = time.time()
        weights = [
            self._calc_weight(category, fn, now) * _default_weight(category, fn)
            for fn in candidates
        ]

        total = sum(weights)
        if total <= 0:
            return random.choice(candidates)

        # 加权随机
        r = random.uniform(0, total)
        cumulative = 0.0
        for fn, w in zip(candidates, weights):
            cumulative += w
            if r <= cumulative:
                return fn

        return candidates[-1]  # fallback（浮点精度保护）
