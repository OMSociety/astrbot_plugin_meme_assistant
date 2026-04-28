"""Stardots 图床 Provider（适配 image_host/providers/stardots_provider）"""
import logging
from pathlib import Path

logger = logging.getLogger("astrbot.meme_assistant")

from ..image_host.providers.stardots_provider import StarDotsProvider


class ImageProvider:
    """ProviderRegistry 兼容的 Stardots 图床适配器"""
    PROVIDER_TYPE = "stardots"

    def __init__(self, config: dict, local_dir: str):
        self.local_dir = Path(local_dir) if local_dir else Path(".")
        self.config = config
        self._provider = StarDotsProvider(
            config={
                "key": config.get("key", ""),
                "secret": config.get("secret", ""),
                "space": config.get("space", "memes"),
                "local_dir": str(self.local_dir),
            }
        )
        logger.info("[meme_assistant] Stardots provider loaded")

    async def upload(self, file_path: str, remote_path: str = "") -> str:
        """上传单个文件，返回 URL"""
        path = Path(file_path)
        if not path.is_absolute():
            path = self.local_dir / path
        result = self._provider.upload_image(path)
        return result["url"]

    async def sync_all(self):
        """全量同步（委托给 image_host/img_sync.ImageSync）"""
        from ..image_host.img_sync import ImageSync
        syncer = ImageSync(self.config, self.local_dir)
        await syncer.sync()
