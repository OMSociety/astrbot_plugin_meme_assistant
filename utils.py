import fcntl
import hashlib
import json
import logging
import os
import random
import re
import shutil
import string
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import aiohttp

from .config import CURRENT_DIR, DEFAULT_MEMES_INIT_MARKER, MEMES_DATA_PATH, MEMES_DIR

logger = logging.getLogger(__name__)
DEFAULT_MEMES_SOURCE_DIR = Path(CURRENT_DIR) / "memes"
_json_io_lock = threading.Lock()  # 并发保护 save_json / load_json


# ── 跨进程文件锁（POSIX fcntl.flock）──
# 用途：WebUI 子进程与主进程并发读写识别队列 JSON 时，防止读到半截数据。
@contextmanager
def flock_exclusive(filepath: str):
    """排他锁上下文管理器，用于写操作"""
    lock_path = filepath + ".lock"
    fd = open(lock_path, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


@contextmanager
def flock_shared(filepath: str):
    """共享锁上下文管理器，用于读操作"""
    lock_path = filepath + ".lock"
    fd = open(lock_path, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


def ensure_dir_exists(path: str) -> None:
    """确保目录存在，不存在则创建"""
    if not os.path.exists(path):
        os.makedirs(path)


def _calculate_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _get_available_target_path(target_path: Path) -> Path:
    if not target_path.exists():
        return target_path

    suffix = target_path.suffix
    stem = target_path.stem
    index = 1
    while True:
        candidate = target_path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _find_duplicate_file_by_content(target_dir: Path, content_hash: str) -> Path | None:
    if not target_dir.is_dir():
        return None

    for existing_file in target_dir.iterdir():
        if not existing_file.is_file():
            continue
        try:
            if _calculate_sha256(existing_file.read_bytes()) == content_hash:
                return existing_file
        except OSError as exc:
            logger.warning("读取已有默认表情包文件失败，跳过判重: %s", exc)
    return None


def get_default_meme_categories() -> list[str]:
    """返回插件内置默认表情包类别列表。"""
    if not DEFAULT_MEMES_SOURCE_DIR.is_dir():
        return []
    return sorted(
        item.name for item in DEFAULT_MEMES_SOURCE_DIR.iterdir() if item.is_dir()
    )


def restore_default_memes(category: str | None = None) -> dict[str, Any]:
    """恢复内置默认表情包到运行目录。"""
    if not DEFAULT_MEMES_SOURCE_DIR.is_dir():
        return {
            "source_exists": False,
            "available_categories": [],
            "copied_files": {},
            "duplicate_files": {},
            "renamed_files": {},
        }

    available_categories = get_default_meme_categories()
    if category is not None and category not in available_categories:
        return {
            "source_exists": True,
            "available_categories": available_categories,
            "category_exists": False,
            "copied_files": {},
            "duplicate_files": {},
            "renamed_files": {},
        }

    requested_categories = [category] if category else available_categories
    copied_files: dict[str, list[str]] = {}
    duplicate_files: dict[str, list[str]] = {}
    renamed_files: dict[str, list[dict[str, str]]] = {}

    ensure_dir_exists(MEMES_DIR)

    for category_name in requested_categories:
        source_category_dir = DEFAULT_MEMES_SOURCE_DIR / category_name
        if not source_category_dir.is_dir():
            continue

        target_category_dir = Path(MEMES_DIR) / category_name
        target_category_dir.mkdir(parents=True, exist_ok=True)

        for source_file in sorted(source_category_dir.iterdir()):
            if not source_file.is_file():
                continue

            content = source_file.read_bytes()
            content_hash = _calculate_sha256(content)
            duplicate_file = _find_duplicate_file_by_content(
                target_category_dir, content_hash
            )
            if duplicate_file is not None:
                duplicate_files.setdefault(category_name, []).append(
                    duplicate_file.name
                )
                continue

            target_path = _get_available_target_path(
                target_category_dir / source_file.name
            )
            shutil.copy2(source_file, target_path)
            copied_files.setdefault(category_name, []).append(target_path.name)

            if target_path.name != source_file.name:
                renamed_files.setdefault(category_name, []).append(
                    {"source": source_file.name, "saved": target_path.name}
                )

    return {
        "source_exists": True,
        "available_categories": available_categories,
        "category_exists": True,
        "copied_files": copied_files,
        "duplicate_files": duplicate_files,
        "renamed_files": renamed_files,
    }


def copy_default_memes_if_needed() -> bool:
    """仅在首次初始化时复制默认表情包，后续更新不再自动补回。"""
    ensure_dir_exists(MEMES_DIR)

    marker_path = Path(DEFAULT_MEMES_INIT_MARKER)
    if marker_path.exists():
        return False

    memes_dir = Path(MEMES_DIR)
    has_existing_runtime_data = Path(MEMES_DATA_PATH).is_file() or any(
        memes_dir.iterdir()
    )
    if has_existing_runtime_data:
        marker_path.touch(exist_ok=True)
        logger.info("检测到现有表情包数据，跳过自动复制默认表情包。")
        return False

    result = restore_default_memes()
    if not result["source_exists"]:
        logger.warning("默认表情包目录不存在: %s", DEFAULT_MEMES_SOURCE_DIR)
        return False

    marker_path.touch(exist_ok=True)
    copied_total = sum(len(files) for files in result["copied_files"].values())
    if copied_total > 0:
        logger.info(
            "已初始化默认表情包，共复制 %s 个文件到 %s", copied_total, MEMES_DIR
        )
        return True

    logger.info("默认表情包初始化已完成，但未复制任何文件。")
    return False


def save_json(data: dict[str, Any], filepath: str) -> bool:
    """保存 JSON 数据到文件（线程安全）"""
    try:
        ensure_dir_exists(os.path.dirname(filepath))
        with _json_io_lock:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"保存 JSON 文件失败 {filepath}: {e}")
        return False


def load_json(filepath: str, default: dict = None) -> dict:
    """从文件加载 JSON 数据（线程安全）"""
    try:
        with _json_io_lock:
            with open(filepath, encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"加载 JSON 文件失败 {filepath}: {e}")
        return default if default is not None else {}


def dict_to_string(dictionary):
    lines = [f"{key} - {value}\n" for key, value in dictionary.items()]
    return "\n".join(lines)


def generate_secret_key(length=8):
    """生成随机秘钥"""
    characters = string.ascii_letters + string.digits
    return "".join(random.choice(characters) for _ in range(length))


async def get_public_ip():
    """异步获取公网IPv4地址"""
    ipv4_apis = [
        "http://ipv4.ifconfig.me/ip",  # IPv4专用接口
        "http://api-ipv4.ip.sb/ip",  # 樱花云IPv4接口
        "http://v4.ident.me",  # IPv4专用
        "http://ip.qaros.com",  # 备用国内服务
        "http://ipv4.icanhazip.com",  # IPv4专用
        "http://4.icanhazip.com",  # 另一个变种地址
    ]

    async with aiohttp.ClientSession() as session:
        for api in ipv4_apis:
            try:
                async with session.get(api, timeout=5) as response:
                    if response.status == 200:
                        ip = (await response.text()).strip()
                        # 添加二次验证确保是IPv4格式
                        if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", ip):
                            return ip
            except Exception:
                continue

    return "[服务器公网ip]"


# ——————— 图片压缩工具 ———————
def compress_image_if_large(
    file_path: Path,
    max_width: int = 1920,
    max_size_bytes: int = 5 * 1024 * 1024,  # 5MB
    quality: int = 85,
) -> Path | None:
    """大图自动压缩，返回临时压缩文件路径；无需压缩则返回 None。

    Args:
        file_path: 原始图片路径
        max_width: 最大宽度（等比缩放）
        max_size_bytes: 超过此大小才压缩
        quality: JPEG/WebP 压缩质量

    Returns:
        压缩后的临时文件 Path，调用方负责用完后删除；无需压缩返回 None
    """
    try:
        from PIL import Image
        import tempfile

        file_size = file_path.stat().st_size
        if file_size <= max_size_bytes:
            return None  # 无需压缩

        img = Image.open(file_path)

        # 等比缩放
        if img.width > max_width:
            ratio = max_width / img.width
            new_size = (max_width, int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)

        # 转为 RGB（处理 RGBA / P 模式）
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        # 写入临时文件
        suffix = file_path.suffix.lower()
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        save_format = "JPEG" if suffix in (".jpg", ".jpeg") else suffix[1:].upper()
        img.save(tmp.name, format=save_format, quality=quality, optimize=True)

        logger.info(
            "图片压缩完成: %s → %s (%.1f%% 原始大小)",
            file_path.name,
            Path(tmp.name).name,
            Path(tmp.name).stat().st_size / file_size * 100,
        )
        return Path(tmp.name)

    except Exception as e:
        logger.warning("图片压缩失败 %s: %s", file_path.name, e)
        return None
