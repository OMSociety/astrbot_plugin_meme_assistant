"""P3: 多图床 Provider 自动发现注册表"""
import importlib
import importlib.util
import os

from astrbot.api import logger

_PROVIDER_CACHE: dict[str, type] = {}

def _discover_providers():
    """自动扫描 providers/ 目录，注册所有 Provider"""
    global _PROVIDER_CACHE
    import sys
    providers_dir = os.path.join(os.path.dirname(__file__), "..", "providers")
    plugins_dir = os.path.dirname(providers_dir)  # astrbot_plugin_meme_assistant/
    
    # 确保包层级在 sys.modules 中存在（stardots_sync 用相对导入需要）
    pkg_name = "astrbot_plugin_meme_assistant"
    if pkg_name in sys.modules and not hasattr(sys.modules[pkg_name], "__path__"):
        sys.modules[pkg_name].__path__ = [plugins_dir]
    
    if not os.path.isdir(providers_dir):
        return
    for fname in os.listdir(providers_dir):
        if fname.endswith("_sync.py") and fname != "__init__.py":
            mod_name = fname[:-3]  # e.g. "stardots_sync"
            try:
                # 使用完整包名，让相对导入（from ..image_host...）能解析
                full_name = f"{pkg_name}.providers.{mod_name}"
                spec = importlib.util.spec_from_file_location(
                    full_name,
                    os.path.join(providers_dir, fname),
                )
                mod = importlib.util.module_from_spec(spec)
                sys.modules[full_name] = mod
                spec.loader.exec_module(mod)
                if hasattr(mod, "ImageProvider"):
                    provider_type = getattr(mod.ImageProvider, "PROVIDER_TYPE", mod_name.replace("_sync", ""))
                    _PROVIDER_CACHE[provider_type] = mod.ImageProvider
                    logger.info(f"[meme_assistant] Provider registered: {provider_type}")
            except Exception as e:
                logger.warning(f"[meme_assistant] Skip provider {mod_name}: {e}")


class ProviderRegistry:
    """Provider 注册表"""
    _discovered = False

    @classmethod
    def ensure_discovery(cls):
        if not cls._discovered:
            _discover_providers()
            cls._discovered = True

    @classmethod
    def create(cls, provider_type: str, config: dict, local_dir: str):
        """创建 Provider 实例"""
        cls.ensure_discovery()
        if provider_type not in _PROVIDER_CACHE:
            raise ValueError(f"Unknown provider type: {provider_type}. Available: {list(_PROVIDER_CACHE.keys())}")
        provider_cls = _PROVIDER_CACHE[provider_type]
        return provider_cls(config=config, local_dir=local_dir)

    @classmethod
    def available_providers(cls) -> list[str]:
        cls.ensure_discovery()
        return sorted(_PROVIDER_CACHE.keys())
