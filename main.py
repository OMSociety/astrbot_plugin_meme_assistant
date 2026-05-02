import asyncio
import copy
import os

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import EventMessageType
from astrbot.api.star import Context, Star, register

from ._command_upload import CommandUploadMixin
from ._emotion_mixin import EmotionMixin
from ._identify_mixin import IdentifyMixin
from ._messaging_mixin import MessagingMixin
from ._webui_mixin import WebUIMixin
from .backend.category_manager import CategoryManager
from .backend.description_manager import DescriptionManager
from .backend.provider_registry import ProviderRegistry
from .config import MEMES_DIR
from .init import init_plugin
from ._prompt_renderer import PromptRenderer
from ._meme_recommender import MemeRecommender

_PROMPT_DIR = os.path.join(os.path.dirname(__file__), "prompts")


@register(
    "meme_manager", "anka", "anka - 表情包管理器 - 支持表情包发送及表情包上传", "3.20"
)
class MemeSender(
    Star,
    IdentifyMixin,
    EmotionMixin,
    MessagingMixin,
    WebUIMixin,
    CommandUploadMixin,
):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}

        if not init_plugin():
            raise RuntimeError("插件初始化失败")

        self._init_identify_progress()
        self.category_manager = CategoryManager()
        self.description_manager = DescriptionManager()
        self.img_sync = self._init_img_sync()

        # P3: 模板化 & 推荐器
        self.renderer = PromptRenderer(_PROMPT_DIR)
        self.recommender = MemeRecommender(meme_dir=MEMES_DIR)

        self._identify_poll_task: asyncio.Task | None = None

        # 运行时状态
        self.found_emotions = []
        self.upload_states = {}
        self._upload_lock = asyncio.Lock()
        self.pending_images = {}
        self._send_lock: dict[str, asyncio.Lock] = {}
        self._queue_lock = asyncio.Lock()
        self._memes_cache: dict[str, tuple[list[str], float]] = {}

        # 配置值加载
        self._init_webui_config()
        self._load_config_values()

        # 识别熔断器
        self._circuit_failures = 0
        self._circuit_tripped_at = 0.0

        personas = self.context.provider_manager.personas
        self.persona_backup = copy.deepcopy(personas)
        self._reload_personas()

        self._auto_start_webui()

        @self.meme_manager.command("查看图库")
        async def list_categories(self, event: AstrMessageEvent):
            """查看所有可用表情包类别"""
            async for msg in self._list_emotions_impl(event):
                yield msg

        # 提前启动识别轮询（不再等首次 LLM 响应）
        self._identify_poll_task = asyncio.ensure_future(self._auto_identify_loop())


    # ── 初始化辅助方法 ──────────────────────────────

    def _init_img_sync(self):
        """初始化图片同步提供器（Provider 自动发现）。"""
        image_host_type = self.config.get("image_host", "disabled")
        if image_host_type == "disabled":
            return None
        host_config = self.config.get("image_host_config", {}).get(image_host_type, {})
        if not host_config:
            return None
        try:
            return ProviderRegistry.create(
                provider_type=image_host_type,
                config=host_config,
                local_dir=MEMES_DIR,
            )
        except Exception as e:
            logger.error(f"[meme_assistant] Image sync init failed: {e}")
            return None

    def _load_config_values(self):
        """从 config dict 中加载所有配置值到 self 属性。"""
        cfg = self.config
        self.fault_tolerant_symbols = cfg.get("fault_tolerant_symbols", ["⬡"])

        # Prompt 相关
        prompt_cfg = cfg.get("prompt", {})
        self.prompt_head = prompt_cfg.get("prompt_head")
        self.prompt_tail_1 = prompt_cfg.get("prompt_tail_1")
        self.prompt_tail_2 = prompt_cfg.get("prompt_tail_2")
        self.vision_identify_prompt = prompt_cfg.get("vision_identify_prompt", "")

        # 表情发送策略
        self.max_emotions_per_message = cfg.get("max_emotions_per_message")
        self.emotions_probability = cfg.get("emotions_probability")
        self.strict_max_emotions_per_message = cfg.get("strict_max_emotions_per_message")
        self.emotion_llm_enabled = cfg.get("emotion_llm_enabled", False)
        self.emotion_llm_provider_id = cfg.get("emotion_llm_provider_id", "")
        self.meme_llm_tool_enabled = cfg.get("meme_llm_tool_enabled", True)
        self.enable_mixed_message = cfg.get("enable_mixed_message", True)
        self.mixed_message_probability = cfg.get("mixed_message_probability", 80)

        # 消息处理
        self.remove_invalid_alternative_markup = cfg.get("remove_invalid_alternative_markup", False)
        self.convert_static_to_gif = cfg.get("convert_static_to_gif", False)
        self.streaming_compatibility = cfg.get("streaming_compatibility", False)
        self.content_cleanup_rule = cfg.get("content_cleanup_rule", "&&[a-zA-Z]*&&")
        self.meme_identify_enabled = cfg.get("meme_identify_enabled", True)
        self.meme_identify_provider_id = cfg.get("meme_identify_provider_id", "")
        self.meme_identify_on_upload = cfg.get("meme_identify_on_upload", True)
        self.meme_identify_concurrency = cfg.get("meme_identify_concurrency", 2)
        self.meme_identify_circuit_threshold = cfg.get("meme_identify_circuit_threshold", 5)
        self.meme_identify_circuit_cooldown = cfg.get("meme_identify_circuit_cooldown", 300)

    @filter.command_group("表情管理")
    def meme_manager(self):
        """表情包管理命令组"""
        pass

    # ═══════════════════════════════════════════════════════════
    # 命令方法 — 薄壳委托（实现 → _command_manage.py / _command_upload.py）
    # ═══════════════════════════════════════════════════════════

    @filter.permission_type(filter.PermissionType.ADMIN)
    @meme_manager.command("关闭管理后台")
    async def stop_server(self, event: AstrMessageEvent):
        async for msg in self._stop_server_impl(event):
            yield msg

    async def list_emotions(self, event: AstrMessageEvent):
        async for msg in self._list_emotions_impl(event):
            yield msg

    @filter.permission_type(filter.PermissionType.ADMIN)
    @meme_manager.command("添加表情")
    async def upload_meme(self, event: AstrMessageEvent, category: str = None):
        async for msg in self._upload_meme_impl(event, category):
            yield msg

    @filter.permission_type(filter.PermissionType.ADMIN)
    @meme_manager.command("恢复默认表情包")
    async def restore_default_memes_command(
        self, event: AstrMessageEvent, category: str = None
    ):
        async for msg in self._restore_default_memes_impl(event, category):
            yield msg

    @filter.permission_type(filter.PermissionType.ADMIN)
    @meme_manager.command("清空指定类型")
    async def clear_category_command(
        self, event: AstrMessageEvent, category: str = None
    ):
        async for msg in self._clear_category_impl(event, category):
            yield msg

    @filter.permission_type(filter.PermissionType.ADMIN)
    @meme_manager.command("清空全部")
    async def clear_all_emojis_command(self, event: AstrMessageEvent):
        async for msg in self._clear_all_emojis_impl(event):
            yield msg

    @filter.permission_type(filter.PermissionType.ADMIN)
    @meme_manager.command("删除类型本身")
    async def delete_category_command(
        self, event: AstrMessageEvent, category: str = None
    ):
        async for msg in self._delete_category_impl(event, category):
            yield msg

    @filter.event_message_type(EventMessageType.ALL)
    @meme_manager.command("表情解释")
    async def explain_meme(self, event: AstrMessageEvent, category: str, filename: str):
        async for msg in self._explain_meme_impl(event, category, filename):
            yield msg

    @filter.permission_type(filter.PermissionType.ADMIN)
    @meme_manager.command("表情识别")
    async def identify_meme_command(
        self, event: AstrMessageEvent, category: str = None
    ):
        async for msg in self._identify_meme_command_impl(event, category):
            yield msg

    @filter.event_message_type(EventMessageType.ALL)
    async def handle_upload_image(self, event: AstrMessageEvent):
        async for msg in self._handle_upload_image_impl(event):
            yield msg

    async def reload_emotions(self):
        await self._reload_emotions_impl()

    @meme_manager.command("同步状态")
    async def check_sync_status(self, event: AstrMessageEvent, detail: str = None):
        async for msg in self._check_sync_status_impl(event, detail):
            yield msg

    @filter.permission_type(filter.PermissionType.ADMIN)
    @meme_manager.command("同步到云端")
    async def sync_to_remote(self, event: AstrMessageEvent):
        async for msg in self._sync_to_remote_impl(event):
            yield msg

    @meme_manager.command("图库统计")
    async def show_library_stats(self, event: AstrMessageEvent):
        async for msg in self._show_library_stats_impl(event):
            yield msg

    @filter.permission_type(filter.PermissionType.ADMIN)
    @meme_manager.command("从云端同步")
    async def sync_from_remote(self, event: AstrMessageEvent):
        async for msg in self._sync_from_remote_impl(event):
            yield msg

    @filter.permission_type(filter.PermissionType.ADMIN)
    @meme_manager.command("覆盖到云端")
    async def overwrite_to_remote(self, event: AstrMessageEvent):
        async for msg in self._overwrite_to_remote_impl(event):
            yield msg

    @filter.permission_type(filter.PermissionType.ADMIN)
    @meme_manager.command("从云端覆盖")
    async def overwrite_from_remote(self, event: AstrMessageEvent):
        async for msg in self._overwrite_from_remote_impl(event):
            yield msg

    # ═══════════════════════════════════════════════════════════
    # 生命周期
    # ═══════════════════════════════════════════════════════════

    async def terminate(self):
        personas = self.context.provider_manager.personas
        for persona, persona_backup in zip_longest(personas, self.persona_backup):
            if persona is not None and persona_backup is not None:
                persona["prompt"] = persona_backup["prompt"]
        if self.img_sync:
            self.img_sync.stop_sync()
        await self._shutdown_webui()
