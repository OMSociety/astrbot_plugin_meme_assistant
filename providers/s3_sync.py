"""AWS S3 兼容图床 Provider 存根"""
import logging
logger = logging.getLogger("astrbot.meme_assistant")

class ImageProvider:
    PROVIDER_TYPE = "s3"

    def __init__(self, config: dict, local_dir: str):
        self.config = config
        self.local_dir = local_dir
        logger.info("[meme_assistant] S3 provider stub loaded (implement upload/sync methods)")

    async def upload(self, file_path: str, remote_path: str = "") -> str:
        raise NotImplementedError("S3 upload not yet implemented")

    async def sync_all(self):
        raise NotImplementedError("S3 sync not yet implemented")
