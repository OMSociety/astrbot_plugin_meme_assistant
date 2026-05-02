import os

from astrbot.api import logger

from .config import (
    BASE_DATA_DIR,
    DEFAULT_CATEGORY_DESCRIPTIONS,
    MEMES_DATA_PATH,
    MEMES_DIR,
    _ensure_dirs,
)
from .utils import ensure_dir_exists, save_json


def _ensure_category_dirs() -> list[str]:
    """在 MEMES_DIR 下为每个预设类别创建空文件夹。"""
    created = []
    for category in DEFAULT_CATEGORY_DESCRIPTIONS:
        cat_dir = os.path.join(MEMES_DIR, category)
        if not os.path.isdir(cat_dir):
            os.makedirs(cat_dir, exist_ok=True)
            created.append(category)
    return created


def init_plugin():
    """初始化插件：创建数据目录与空的类别文件夹，生成必要配置文件。"""
    try:
        # 必要目录（惰性）
        _ensure_dirs()

        # 数据根目录
        ensure_dir_exists(BASE_DATA_DIR)

        # 在 data/plugin_data/meme_manager/memes/ 下创建空白类别文件夹
        created = _ensure_category_dirs()
        if created:
            logger.info(
                "创建空白表情包类别目录 (%d 个): %s",
                len(created),
                ", ".join(created),
            )

        # 初始化 memes_data.json（类别描述）
        if not os.path.exists(MEMES_DATA_PATH):
            save_json(DEFAULT_CATEGORY_DESCRIPTIONS, MEMES_DATA_PATH)
            logger.info(f"创建默认类别描述文件: {MEMES_DATA_PATH}")

        return True
    except Exception as e:
        logger.error(f"插件初始化失败: {e}")
        return False
