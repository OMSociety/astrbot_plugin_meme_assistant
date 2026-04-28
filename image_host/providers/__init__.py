"""图床提供者模块 — 自动发现所有 ImageHostInterface 实现"""

from ..provider_registry import discover_providers, create_provider, get_available_providers

# 保留直接导入以保持向后兼容
from .stardots_provider import StarDotsProvider
from .provider_template import ProviderTemplate as ImageHostProvider

__all__ = [
    "StarDotsProvider",
    "ImageHostProvider",
    "discover_providers",
    "create_provider",
    "get_available_providers",
]
