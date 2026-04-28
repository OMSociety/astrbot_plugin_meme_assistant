"""阿里云 OSS 图床 Provider

配置示例:
    {
        "access_key_id": "LTAI5xxxx",
        "access_key_secret": "xxxx",
        "endpoint": "oss-cn-hangzhou.aliyuncs.com",
        "bucket": "my-bucket",
        "prefix": "memes/",
        "cdn_domain": "https://cdn.example.com"  # 可选 CDN 域名
    }
"""

from pathlib import Path

from astrbot.api import logger

from ..interfaces.image_host import ImageHostInterface


class AliyunOSSProvider(ImageHostInterface):
    """阿里云 OSS 图床提供者。

    需要安装: pip install oss2
    """

    def __init__(self, config: dict):
        self.config = config
        self._bucket_obj = None
        self._init_client()

    def _init_client(self):
        """初始化 OSS 客户端。"""
        try:
            import oss2

            access_key_id = self.config.get("access_key_id", "")
            access_key_secret = self.config.get("access_key_secret", "")
            endpoint = self.config.get("endpoint", "")
            bucket_name = self.config.get("bucket", "")

            auth = oss2.Auth(access_key_id, access_key_secret)
            self._bucket_obj = oss2.Bucket(auth, endpoint, bucket_name)
            self._prefix = self.config.get("prefix", "memes/").strip("/")
            self._cdn_domain = self.config.get("cdn_domain", "").rstrip("/")
            logger.info("[AliyunOSSProvider] 初始化完成")
        except ImportError:
            logger.error(
                "[AliyunOSSProvider] 请安装 oss2: pip install oss2"
            )
        except Exception as e:
            logger.error(f"[AliyunOSSProvider] 初始化失败: {e}")

    def _make_url(self, key: str) -> str:
        """生成访问 URL。"""
        if self._cdn_domain:
            return f"{self._cdn_domain}/{key}"
        bucket = self.config.get("bucket", "")
        endpoint = self.config.get("endpoint", "")
        return f"https://{bucket}.{endpoint}/{key}"

    def _make_key(self, filename: str) -> str:
        prefix = self._prefix
        return f"{prefix}/{filename}" if prefix else filename

    def upload_image(self, file_path: Path) -> dict[str, str]:
        if not self._bucket_obj:
            raise RuntimeError("OSS Bucket 未初始化")

        key = self._make_key(file_path.name)
        import hashlib

        with open(file_path, "rb") as f:
            data = f.read()

        self._bucket_obj.put_object(key, data)
        file_hash = hashlib.md5(data).hexdigest()
        logger.debug(f"[AliyunOSSProvider] 上传成功: {key}")

        return {"url": self._make_url(key), "hash": file_hash, "id": key}

    def delete_image(self, image_hash: str) -> bool:
        if not self._bucket_obj:
            return False
        try:
            self._bucket_obj.delete_object(image_hash)
            return True
        except Exception as e:
            logger.warning(f"[AliyunOSSProvider] 删除失败: {e}")
            return False

    def get_image_list(self) -> list[dict[str, str]]:
        if not self._bucket_obj:
            return []
        result = []
        try:
            prefix = self._prefix + "/" if self._prefix else ""
            for obj in self._bucket_obj.list_objects(prefix=prefix).object_list:
                filename = obj.key.rsplit("/", 1)[-1] if "/" in obj.key else obj.key
                result.append(
                    {
                        "id": obj.key,
                        "url": self._make_url(obj.key),
                        "filename": filename,
                        "hash": obj.etag.strip('"'),
                        "size": obj.size,
                    }
                )
        except Exception as e:
            logger.error(f"[AliyunOSSProvider] 获取列表失败: {e}")
        return result

    def download_image(self, image_info: dict[str, str], save_path: Path) -> bool:
        if not self._bucket_obj:
            return False
        try:
            key = image_info.get("id", "")
            result = self._bucket_obj.get_object(key)
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_bytes(result.read())
            return True
        except Exception as e:
            logger.warning(f"[AliyunOSSProvider] 下载失败: {e}")
            return False
