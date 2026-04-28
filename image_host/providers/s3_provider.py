"""Amazon S3 兼容图床 Provider

支持: AWS S3 / MinIO / Cloudflare R2 等 S3 兼容服务

配置示例 (AWS S3):
    {
        "access_key_id": "AKIAxxxx",
        "secret_access_key": "xxxx",
        "region": "us-east-1",
        "bucket": "my-bucket",
        "prefix": "memes/",
        "endpoint_url": null,           # null 表示使用 AWS 默认
        "cdn_domain": "https://cdn.example.com"  # 可选
    }

配置示例 (MinIO):
    {
        "access_key_id": "minioadmin",
        "secret_access_key": "minioadmin",
        "endpoint_url": "http://localhost:9000",
        "bucket": "memes",
        "prefix": "",
        "cdn_domain": ""
    }

配置示例 (Cloudflare R2):
    {
        "access_key_id": "xxx",
        "secret_access_key": "xxx",
        "endpoint_url": "https://<account_id>.r2.cloudflarestorage.com",
        "bucket": "memes",
        "prefix": "",
        "cdn_domain": "https://cdn.example.com"
    }
"""

from pathlib import Path

from astrbot.api import logger

from ..interfaces.image_host import ImageHostInterface


class S3Provider(ImageHostInterface):
    """Amazon S3 兼容图床提供者。

    需要安装: pip install boto3
    """

    def __init__(self, config: dict):
        self.config = config
        self._client = None
        self._init_client()

    def _init_client(self):
        """初始化 S3 客户端。"""
        try:
            import boto3

            kwargs = {
                "aws_access_key_id": self.config.get("access_key_id", ""),
                "aws_secret_access_key": self.config.get("secret_access_key", ""),
            }

            region = self.config.get("region")
            if region:
                kwargs["region_name"] = region

            endpoint_url = self.config.get("endpoint_url")
            if endpoint_url:
                kwargs["endpoint_url"] = endpoint_url

            self._client = boto3.client("s3", **kwargs)
            self._bucket = self.config.get("bucket", "")
            self._prefix = self.config.get("prefix", "memes/").strip("/")
            self._cdn_domain = self.config.get("cdn_domain", "").rstrip("/")
            self._region = region or "us-east-1"
            logger.info("[S3Provider] 初始化完成")
        except ImportError:
            logger.error(
                "[S3Provider] 请安装 boto3: pip install boto3"
            )
        except Exception as e:
            logger.error(f"[S3Provider] 初始化失败: {e}")

    def _make_url(self, key: str) -> str:
        """生成访问 URL。"""
        if self._cdn_domain:
            return f"{self._cdn_domain}/{key}"
        endpoint = self.config.get("endpoint_url", "")
        if endpoint:
            return f"{endpoint.rstrip('/')}/{self._bucket}/{key}"
        return f"https://{self._bucket}.s3.{self._region}.amazonaws.com/{key}"

    def _make_key(self, filename: str) -> str:
        prefix = self._prefix
        return f"{prefix}/{filename}" if prefix else filename

    def upload_image(self, file_path: Path) -> dict[str, str]:
        if not self._client:
            raise RuntimeError("S3 客户端未初始化")

        key = self._make_key(file_path.name)
        import hashlib

        with open(file_path, "rb") as f:
            data = f.read()

        content_type = "image/gif" if file_path.suffix.lower() == ".gif" else "image/png"
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        file_hash = hashlib.md5(data).hexdigest()
        logger.debug(f"[S3Provider] 上传成功: {key}")

        return {"url": self._make_url(key), "hash": file_hash, "id": key}

    def delete_image(self, image_hash: str) -> bool:
        if not self._client:
            return False
        try:
            self._client.delete_object(Bucket=self._bucket, Key=image_hash)
            return True
        except Exception as e:
            logger.warning(f"[S3Provider] 删除失败: {e}")
            return False

    def get_image_list(self) -> list[dict[str, str]]:
        if not self._client:
            return []
        result = []
        try:
            prefix = self._prefix + "/" if self._prefix else ""
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
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
            logger.error(f"[S3Provider] 获取列表失败: {e}")
        return result

    def download_image(self, image_info: dict[str, str], save_path: Path) -> bool:
        if not self._client:
            return False
        try:
            key = image_info.get("id", "")
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_bytes(response["Body"].read())
            return True
        except Exception as e:
            logger.warning(f"[S3Provider] 下载失败: {e}")
            return False
