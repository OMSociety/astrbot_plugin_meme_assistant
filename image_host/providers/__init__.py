"""图床提供者模块"""

from .provider_template import ProviderTemplate as ImageHostProvider
from .stardots_provider import StarDotsProvider

__all__ = ["StarDotsProvider", "ImageHostProvider"]
