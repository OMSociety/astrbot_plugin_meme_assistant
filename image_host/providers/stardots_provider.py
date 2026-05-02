"""StarDots 图床提供者（httpx 同步实现）。"""

import hashlib
import json
import random
import string
import threading
import time
from pathlib import Path
from typing import TypedDict

import httpx

from astrbot.api import logger

from ..interfaces.image_host import ImageHostInterface
from ...utils import compress_image_if_large


class StarDotsError(Exception):
    """StarDots 相关错误的基类"""


class AuthenticationError(StarDotsError):
    """认证错误"""


class NetworkError(StarDotsError):
    """网络错误"""


class InvalidResponseError(StarDotsError):
    """响应格式错误"""


class ImageInfo(TypedDict):
    url: str
    id: str
    filename: str
    category: str
    size: int | None


class StarDotsProvider(ImageHostInterface):
    """StarDots 图床提供者实现（基于 httpx 同步客户端）。"""

    BASE_URL = "https://api.stardots.io"
    CATEGORY_SEPARATOR = "@@CAT@@"
    DEFAULT_CATEGORY = "default"
    MIME_TYPES = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }

    def __init__(self, config: dict[str, str]):
        """
        初始化 StarDots 图床。

        Args:
            config: {
                'key': 'your_key',
                'secret': 'your_secret',
                'space': 'your_space_name'
            }
        """
        required_fields = {"key", "secret", "space"}
        missing_fields = required_fields - set(config.keys())
        if missing_fields:
            raise ValueError(f"Missing required config fields: {missing_fields}")
        self.config = config
        self.key = config["key"]
        self.secret = config["secret"]
        self.space = config["space"]
        self.base_url = self.BASE_URL
        self.server_time_offset = 0  # 服务器时间偏移量

        # 配置 httpx 同步客户端：30 秒超时，禁用 SSL 验证，自动重试
        transport = httpx.HTTPTransport(
            verify=False,
            retries=3,
        )
        self.session = httpx.Client(
            transport=transport,
            timeout=30.0,
        )

        self._sync_server_time()  # 初始化时同步服务器时间
        local_dir = config.get("local_dir", "")
        self.records_file = (
            Path(local_dir) / "category_records.json"
            if local_dir
            else Path("category_records.json")
        )
        self._load_records()  # 加载分类记录
        self._record_lock = threading.Lock()  # 并发保护分类记录读写

    def _sync_server_time(self) -> None:
        """同步服务器时间。"""
        try:
            response = self.session.get(f"{self.base_url}/openapi/space/list")
            if response.status_code == 200:
                result = response.json()
                server_ts = result.get("ts", 0) // 1000  # 转换为秒
                local_ts = int(time.time())
                self.server_time_offset = server_ts - local_ts
        except Exception:
            self.server_time_offset = 8 * 3600  # 如果失败，使用默认的 UTC+8

    def _generate_headers(self) -> dict[str, str]:
        """生成请求头。"""
        timestamp = str(int(time.time() + self.server_time_offset))
        nonce = "".join(random.choices(string.ascii_letters + string.digits, k=10))

        sign_str = f"{timestamp}|{self.secret}|{nonce}"
        sign = hashlib.md5(sign_str.encode()).hexdigest().upper()

        return {
            "x-stardots-timestamp": timestamp,
            "x-stardots-nonce": nonce,
            "x-stardots-key": self.key,
            "x-stardots-sign": sign,
            "Content-Type": "application/json",
        }

    def _load_records(self):
        """从文件加载分类记录。"""
        try:
            with self._record_lock:
                if self.records_file.exists():
                    with open(self.records_file, encoding="utf-8") as f:
                        self._upload_records = json.load(f)
                else:
                    self._upload_records = {}
        except Exception:
            self._upload_records = {}

    def _save_records(self):
        """保存分类记录到文件。"""
        try:
            with self._record_lock:
                with open(self.records_file, "w", encoding="utf-8") as f:
                    json.dump(self._upload_records, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("保存分类记录失败: %s", e)

    def _encode_category(self, category: str) -> str:
        """将分类路径编码到文件名中。"""
        if not category or category == ".":
            return ""
        return category.replace("/", "@@DIR@@").replace("\\", "@@DIR@@")

    def _decode_category(self, encoded: str) -> str:
        """从编码的文件名中解码分类路径。"""
        if not encoded:
            return self.DEFAULT_CATEGORY
        return encoded.replace("@@DIR@@", "/")

    @staticmethod
    def _extract_image_size(image_info: dict) -> int | None:
        """尽量从 StarDots 返回数据中提取文件大小。"""
        candidate_keys = ("size", "fileSize", "file_size", "bytes", "length")
        for key in candidate_keys:
            value = image_info.get(key)
            if isinstance(value, (int, float)):
                return int(value)
            if isinstance(value, str) and value.isdigit():
                return int(value)
        return None

    def upload_image(self, file_path: Path) -> ImageInfo:
        """上传图片到 StarDots。"""
        max_retries = 3
        retry_delay = 2

        for _attempt in range(max_retries):
            try:
                self._sync_server_time()
                headers = self._generate_headers()
                headers.pop("Content-Type")  # 上传文件需要移除 Content-Type

                mime_type = self.MIME_TYPES.get(file_path.suffix.lower(), "image/jpeg")

                base_dir = Path(self.config.get("local_dir", ""))
                try:
                    rel_path = file_path.relative_to(base_dir)
                except ValueError:
                    rel_path = Path(file_path.name)

                category = str(rel_path.parent).replace("\\", "/")
                if category == ".":
                    category = ""

                encoded_category = self._encode_category(category)
                remote_filename = (
                    f"{encoded_category}@@CAT@@{rel_path.name}"
                    if encoded_category
                    else rel_path.name
                )

                logger.info("开始上传: %s", remote_filename)

                # 大图自动压缩
                tmp_compressed = compress_image_if_large(file_path)
                upload_path = tmp_compressed if tmp_compressed else file_path

                try:
                    with open(upload_path, "rb") as f:
                        files = {
                            "file": (remote_filename, f, mime_type),
                            "space": (None, self.space),
                        }

                        response = httpx.put(
                            f"{self.base_url}/openapi/file/upload",
                            headers=headers,
                            files=files,
                            verify=False,
                            timeout=60.0,
                        )

                    if response.status_code == 200:
                        result = response.json()
                        if result["success"]:
                            logger.info("上传成功 URL: %s", result["data"]["url"])
                            return {
                                "url": result["data"]["url"],
                                "id": str(rel_path),
                                "filename": rel_path.name,
                                "category": category,
                            }
                    else:
                        error_msg = f"HTTP {response.status_code}"
                        try:
                            error_msg = response.json().get("message", error_msg)
                        except Exception:
                            pass
                        logger.error("上传失败: %s", error_msg)
                        raise NetworkError(error_msg)
                finally:
                    # 清理临时压缩文件
                    if tmp_compressed and tmp_compressed.exists():
                        try:
                            tmp_compressed.unlink()
                        except OSError:
                            pass

            except httpx.RequestError as e:
                logger.error("网络错误: %s，重试中...", e)
                time.sleep(retry_delay)
                continue
            except Exception as e:
                logger.error("上传异常: %s，重试中...", e)
                time.sleep(retry_delay)
                continue

        raise NetworkError(f"Upload failed after {max_retries} retries")

    def delete_image(self, image_id: str) -> bool:
        """从 StarDots 删除图片。"""
        headers = self._generate_headers()
        data = {"space": self.space, "filenameList": [image_id]}

        response = self.session.delete(
            f"{self.base_url}/openapi/file/delete",
            headers=headers,
            json=data,
        )

        if response.status_code == 200:
            result = response.json()
            return result["success"]
        return False

    def get_image_list(self) -> list[ImageInfo]:
        """获取 StarDots 空间中的所有图片（分页拉取）。"""
        page = 1
        page_size = 100
        max_consecutive_failures = 5
        consecutive_failures = 0
        all_images: list[ImageInfo] = []

        while True:
            try:
                self._sync_server_time()
                headers = self._generate_headers()
                params = {"space": self.space, "page": page, "pageSize": page_size}

                response = self.session.get(
                    f"{self.base_url}/openapi/file/list",
                    headers=headers,
                    params=params,
                )

                if response.status_code == 200:
                    result = response.json()
                    if result["success"]:
                        data = result["data"]
                        images = data["list"]
                        if not images:
                            return all_images

                        for img in images:
                            filename = img["name"]
                            if "@@CAT@@" in filename:
                                encoded_category, name = filename.split("@@CAT@@", 1)
                                category = self._decode_category(encoded_category)
                                file_id = f"{category}/{name}" if category else name
                            else:
                                category = ""
                                name = filename
                                file_id = name

                            file_id = file_id.replace("\\", "/")

                            all_images.append(
                                {
                                    "url": img["url"],
                                    "id": file_id,
                                    "filename": name,
                                    "category": category,
                                    "size": self._extract_image_size(img),
                                }
                            )

                        if len(images) < page_size:
                            return all_images

                        consecutive_failures = 0
                        page += 1
                        continue
                    else:
                        msg = result.get("message", "")
                        consecutive_failures += 1
                        if consecutive_failures > max_consecutive_failures:
                            logger.error(
                                "连续 %d 次获取图片列表失败，停止同步",
                                max_consecutive_failures,
                            )
                            return all_images
                        if "invalid timestamp" in msg.lower():
                            time.sleep(1)
                            continue
                        if "invalid nonce" in msg.lower():
                            time.sleep(1)
                            continue
                        logger.warning(
                            "获取图片列表失败: %s",
                            result.get("message", "未知错误"),
                        )
                        time.sleep(1)
                        continue

                else:
                    consecutive_failures += 1
                    if consecutive_failures > max_consecutive_failures:
                        logger.error(
                            "连续 %d 次 HTTP 错误，停止同步",
                            max_consecutive_failures,
                        )
                        return all_images
                    logger.warning("HTTP 错误: %s", response.status_code)
                    time.sleep(1)
                    continue

            except Exception as e:
                logger.warning("获取远程文件列表失败: %s", e)
                consecutive_failures += 1
                if consecutive_failures > max_consecutive_failures:
                    logger.error(
                        "连续 %d 次异常，停止同步",
                        max_consecutive_failures,
                    )
                    return all_images
                if all_images:
                    return all_images
                raise

        return all_images

    def download_image(self, image_info: dict[str, str], save_path: Path) -> bool:
        """从 StarDots 下载图片。"""
        max_retries = 3
        retry_delay = 1
        temp_path = save_path.with_suffix(".tmp")

        encoded_category = self._encode_category(image_info["category"])
        original_name = (
            f"{encoded_category}@@CAT@@{image_info['filename']}"
            if image_info["category"] != "default"
            else image_info["filename"]
        )

        for _attempt in range(max_retries):
            try:
                self._sync_server_time()
                headers = self._generate_headers()

                data = {
                    "space": self.space,
                    "filename": original_name,
                }

                # 获取临时访问票据
                ticket_response = self.session.post(
                    f"{self.base_url}/openapi/file/ticket",
                    headers=headers,
                    json=data,
                )

                if ticket_response.status_code != 200:
                    logger.error(
                        "票据请求失败，状态码: %d", ticket_response.status_code
                    )
                    time.sleep(retry_delay)
                    continue

                ticket_result = ticket_response.json()
                if not ticket_result["success"]:
                    error_msg = ticket_result.get("message", "未知错误")
                    logger.error("获取票据失败: %s", error_msg)
                    time.sleep(retry_delay)
                    continue

                # 构建正确的下载 URL
                base_url = f"https://i.stardots.io/{self.space}/{original_name}"
                url = f"{base_url}?ticket={ticket_result['data']['ticket']}"

                # 下载文件
                with self.session.stream("GET", url) as response:
                    content_type = response.headers.get("Content-Type", "")

                    if response.status_code != 200 or "image/" not in content_type:
                        logger.error(
                            "下载失败，状态码: %d, Content-Type: %s",
                            response.status_code,
                            content_type,
                        )
                        time.sleep(retry_delay)
                        continue

                    save_path.parent.mkdir(parents=True, exist_ok=True)

                    try:
                        with open(temp_path, "wb") as f:
                            for chunk in response.iter_bytes(chunk_size=8192):
                                if chunk:
                                    f.write(chunk)

                        if temp_path.stat().st_size > 1000:
                            temp_path.replace(save_path)
                            return True
                        else:
                            logger.error(
                                "下载的文件太小: %d bytes",
                                temp_path.stat().st_size,
                            )
                    finally:
                        if temp_path.exists():
                            temp_path.unlink()

            except Exception as e:
                logger.error("下载异常: %s", e)
                time.sleep(retry_delay)
                continue

        return False
