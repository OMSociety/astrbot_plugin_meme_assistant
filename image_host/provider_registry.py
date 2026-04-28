"""Provider 自动发现与注册表 — 支持 COS/OSS/S3 等多图床

用法:
    from .provider_registry import discover_providers, create_provider
    providers = discover_providers()
    provider = create_provider("cos", config)
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from pathlib import Path
from typing import Type

from astrbot.api import logger

from .interfaces.image_host import ImageHostInterface


# ═════════════════════════════════════════════════════════════
# Provider 自动发现
# ═════════════════════════════════════════════════════════════

_PROVIDER_REGISTRY: dict[str, Type[ImageHostInterface]] = {}


def discover_providers(
    providers_package: str = ".providers",
    base_path: Path | None = None,
) -> dict[str, Type[ImageHostInterface]]:
    """扫描 providers 目录，自动发现所有 ImageHostInterface 实现。

    Args:
        providers_package: providers 包的相对导入路径
        base_path: providers 目录的绝对路径（可选，用于非标准目录）

    Returns:
        {provider_name: ProviderClass} 映射
    """
    global _PROVIDER_REGISTRY

    if _PROVIDER_REGISTRY:
        return _PROVIDER_REGISTRY

    try:
        # 尝试相对导入
        package = importlib.import_module(providers_package, package=__package__)
        _scan_package(package, providers_package)
    except (ImportError, ModuleNotFoundError) as e:
        logger.warning(f"[provider_registry] 相对导入失败: {e}，尝试手动扫描")

        if base_path and base_path.exists():
            _scan_directory(base_path)

    if not _PROVIDER_REGISTRY:
        logger.warning(
            "[provider_registry] 未发现任何 Provider，回退到内置 Provider"
        )
        _register_builtin()

    logger.info(
        f"[provider_registry] 已发现 {len(_PROVIDER_REGISTRY)} 个 Provider: "
        f"{list(_PROVIDER_REGISTRY.keys())}"
    )
    return _PROVIDER_REGISTRY


def _scan_package(package, package_name: str):
    """扫描 Python 包中的 Provider 类。"""
    for _, module_name, is_pkg in pkgutil.iter_modules(
        package.__path__, package.__name__ + "."
    ):
        if is_pkg:
            continue
        # 跳过模板和双下划线模块
        if module_name.endswith("_template") or "__" in module_name:
            continue
        try:
            module = importlib.import_module(module_name)
            _extract_providers(module)
        except Exception as e:
            logger.debug(f"[provider_registry] 跳过模块 {module_name}: {e}")


def _scan_directory(directory: Path):
    """手动扫描目录中的 .py 文件。"""
    import sys

    sys.path.insert(0, str(directory.parent))
    try:
        for py_file in directory.glob("*.py"):
            if py_file.name.startswith("_") or py_file.name.endswith("_template.py"):
                continue
            module_name = py_file.stem
            try:
                module = importlib.import_module(module_name)
                _extract_providers(module)
            except Exception as e:
                logger.debug(
                    f"[provider_registry] 跳过文件 {py_file.name}: {e}"
                )
    finally:
        if str(directory.parent) in sys.path:
            sys.path.remove(str(directory.parent))


def _extract_providers(module):
    """从模块中提取 ImageHostInterface 子类。"""
    for name, obj in inspect.getmembers(module, inspect.isclass):
        if obj is ImageHostInterface:
            continue
        if issubclass(obj, ImageHostInterface) and not inspect.isabstract(obj):
            # 生成 provider name: 类名去掉 Provider 后缀，小写
            provider_name = _class_to_provider_name(name)
            _PROVIDER_REGISTRY[provider_name] = obj


def _register_builtin():
    """注册内置 Provider（fallback）。"""
    try:
        from .providers.stardots_provider import StarDotsProvider

        _PROVIDER_REGISTRY["stardots"] = StarDotsProvider
    except ImportError:
        pass


def _class_to_provider_name(class_name: str) -> str:
    """从类名推断 provider 名称。

    StarDotsProvider → stardots
    COSProvider → cos
    AliyunOSSProvider → aliyunoss → alioss
    """
    # 去掉 Provider 后缀
    if class_name.lower().endswith("provider"):
        class_name = class_name[:-8]
    # CamelCase → snake_case 简化版
    name = ""
    for i, ch in enumerate(class_name):
        if ch.isupper() and i > 0:
            name += "_"
        name += ch.lower()
    # 常见别名
    aliases = {
        "aliyun_oss": "alioss",
        "aliyun_oss_provider": "alioss",
        "amazon_s3": "s3",
    }
    return aliases.get(name, name.replace("_", ""))


# ═════════════════════════════════════════════════════════════
# Provider 工厂
# ═════════════════════════════════════════════════════════════


def create_provider(provider_name: str, config: dict) -> ImageHostInterface | None:
    """根据名称创建 Provider 实例。

    Args:
        provider_name: 图床名称（如 'stardots', 'cos', 'oss', 's3'）
        config: Provider 配置字典

    Returns:
        ImageHostInterface 实例，或 None
    """
    discover_providers()

    # 标准化名称
    normalized = provider_name.lower().strip().replace("-", "").replace("_", "")
    if normalized in _PROVIDER_REGISTRY:
        return _PROVIDER_REGISTRY[normalized](config)

    # 模糊匹配
    for name, cls in _PROVIDER_REGISTRY.items():
        if normalized in name or name in normalized:
            logger.info(
                f"[provider_registry] 模糊匹配 Provider: {provider_name} → {name}"
            )
            return cls(config)

    logger.error(f"[provider_registry] 未找到 Provider: {provider_name}")
    return None


def get_available_providers() -> list[str]:
    """返回所有已发现的 Provider 名称列表。"""
    return list(discover_providers().keys())
