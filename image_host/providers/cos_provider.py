"""腾讯云 COS 图床 Provider

配置示例:
    {
        "secret_id": "AKIDxxxx",
        "secret_key": "xxxx",
        "region": "ap-guangzhou",
        "bucket": "my-bucket-1250000000",
        "prefix": "memes/",
        "cdn_domain": "https://cdn.example.com"  # 可选 CDN 域名
    }
"""

from pathlib import Path

from astrbot.api import logger

from ..interfaces.image_host import ImageHostInterface


class COSProvider(ImageHostInterface):
    """腾讯云 COS 图床提供者。

    需要安装: pip install cos-python-sdk-v5
    """

    def __init__(self, config: dict):
        self.config = config
        self._client = None
        self._init_client()

    def _init_client(self):
        """初始化 COS 客户端。"""
        try:
            from qcloud_cos import CosConfig, CosS3Client

            secret_id = self.config.get("secret_id", "")
            secret_key = self.config.get("secret_key", "")
            region = self.config.get("region", "ap-guangzhou")

            cos_config = CosConfig(
                Region=region,
                SecretId=secret_id,
                SecretKey=secret_key,
            )
            self._client = CosS3Client(cos_config)
            self._bucket = self.config.get("bucket", "")
            self._prefix = self.config.get("prefix", "memes/").strip("/")
            self._cdn_domain = self.config.get("cdn_domain", "").rstrip("/")
            logger.info("[COSProvider] 初始化完成")
        except ImportError:
            logger.error(
                "[COSProvider] 请安装 cos-python-sdk-v5: pip install cos-python-sdk-v5"
            )
        except Exception as e:
            logger.error(f"[COSProvider] 初始化失败: {e}")

    def _make_url(self, key: str) -> str:
        """生成访问 URL。"""
        if self._cdn_domain:
            return f"{self._cdn_domain}/{key}"
        return f"https://{self._bucket}.cos.{self.config.get('region', 'ap-guangzhou')}.myqcloud.com/{key}"

    def _make_key(self, filename: str) -> str:
        """生成 COS 对象 Key。"""
        prefix = self._prefix
        return f"{prefix}/{filename}" if prefix else filename

    def upload_image(self, file_path: Path) -> dict[str, str]:
        if not self._client:
            raise RuntimeError("COS 客户端未初始化")

        key = self._make_key(file_path.name)
        import hashlib

        with open(file_path, "rb") as f:
            data = f.read()

        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
        )
        file_hash = hashlib.md5(data).hexdigest()
        logger.debug(f"[COSProvider] 上传成功: {key}")

        return {"url": self._make_url(key), "hash": file_hash, "id": key}

    def delete_image(self, image_hash: str) -> bool:
        if not self._client:
            return False
        try:
            self._client.delete_object(Bucket=self._bucket, Key=image_hash)
            return True
        except Exception as e:
            logger.warning(f"[COSProvider] 删除失败: {e}")
            return False

    def get_image_list(self) -> list[dict[str, str]]:
        if not self._client:
            return []
        result = []
        try:
            prefix = self._prefix + "/" if self._prefix else ""
            response = self._client.list_objects(
                Bucket=self._bucket, Prefix=prefix
            )
            for obj in response.get("Contents", []):
                key = obj["Key"]
                filename = key.rsplit("/", 1)[-1] if "/" in key else key
                result.append(
                    {
                        "id": key,
                        "url": self._make_url(key),
                        "filename": filename,
                        "hash": obj.get("ETag", "").strip('"'),
                        "size": obj.get("Size", 0),
                    }
                )
        except Exception as e:
            logger.error(f"[COSProvider] 获取列表失败: {e}")
        return result

    def download_image(self, image_info: dict[str, str], save_path: Path) -> bool:
        if not self._client:
            return False
        try:
            key = image_info.get("id", "")
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_bytes(response["Body"].get_raw_stream().read())
            return True
        except Exception as e:
            logger.warning(f"[COSProvider] 下载失败: {e}")
            return False
