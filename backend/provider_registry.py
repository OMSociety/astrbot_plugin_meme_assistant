"""P3: 多图床 Provider 注册表 — 统一入口

本模块是 ImageSync 的单例工厂，直接委托给 image_host/img_sync.ImageSync。
不再需要 providers/ 目录下的 *_sync.py 中间层。
"""

from astrbot.api import logger


class ProviderRegistry:
    """Provider 注册表 — 工厂模式创建 ImageSync 实例"""

    @staticmethod
    def create(provider_type: str, config: dict, local_dir: str):
        """创建 ImageSync 实例

        Args:
            provider_type: 图床类型 (stardots / cos / oss / s3)
            config: Provider 配置字典
            local_dir: 本地图片目录路径

        Returns:
            ImageSync 实例，或 None（provider_type 不支持时抛异常）
        """
        from ..image_host.img_sync import ImageSync

        normalized = provider_type.lower().strip()
        available = ("stardots", "cos", "oss", "s3")

        if normalized not in available:
            raise ValueError(
                f"Unknown provider type: {provider_type}. "
                f"Available: {list(available)}"
            )

        logger.info(f"[meme_assistant] Creating ImageSync for provider: {normalized}")
        return ImageSync(
            config=config,
            local_dir=local_dir,
            provider_type=normalized,
        )

    @staticmethod
    def available_providers() -> list[str]:
        return ["stardots", "cos", "oss", "s3"]
