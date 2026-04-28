import asyncio
import json
import os
import re
import time

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.message_components import Image

from .config import (
    DEFAULT_CATEGORY_DESCRIPTIONS,
    MEME_IDENTIFY_QUEUE_PATH,
    MEMES_DATA_PATH,
    MEMES_DIR,
)
from .utils import (
    dict_to_string,
    flock_exclusive,
    load_json,
)
from ._prompt_renderer import PromptRenderer


class IdentifyMixin:
    def _reload_personas(self):
        """重新加载表情配置并构建提示词并注入全局人格"""
        self.category_mapping = load_json(
            MEMES_DATA_PATH, DEFAULT_CATEGORY_DESCRIPTIONS
        )
        self.category_mapping_string = dict_to_string(self.category_mapping)
        personas = self.context.provider_manager.personas
        # 如果启用模型情感分析，不注入新的提示词
        if self.emotion_llm_enabled:
            self.sys_prompt_add = ""
            for persona, persona_backup in zip(personas, self.persona_backup):
                persona["prompt"] = persona_backup["prompt"]
            return
        self.sys_prompt_add = self.renderer.render_system_prompt(
            categories=self.category_mapping_string,
            max_emotions=self.max_emotions_per_message,
            prompt_head=self.prompt_head,
            prompt_tail_1=self.prompt_tail_1,
            prompt_tail_2=self.prompt_tail_2,
        )
        # 注入全局人格，以便利用缓存并减少对聊天内容的影响(如果不启用模型分析情感)
        for persona, persona_backup in zip(personas, self.persona_backup):
            persona["prompt"] = persona_backup["prompt"] + self.sys_prompt_add

    def _ensure_default_category_descriptions(self, categories: list[str]) -> None:
        """为恢复出来但缺少描述的默认类别补回默认描述。"""
        existing_descriptions = self.category_manager.get_descriptions()
        updated = False

        for category in categories:
            if category in existing_descriptions:
                continue
            default_description = DEFAULT_CATEGORY_DESCRIPTIONS.get(category)
            if not default_description:
                continue
            if self.category_manager.update_description(category, default_description):
                existing_descriptions[category] = default_description
                updated = True

        if updated:
            self._reload_personas()

    def _check_meme_directories(self):
        """检查表情包目录是否存在并且包含图片"""
        logger.info(f"开始检查表情包根目录: {MEMES_DIR}")
        if not os.path.exists(MEMES_DIR):
            logger.error(f"表情包根目录不存在，请检查: {MEMES_DIR}")
            return

        for emotion in self.category_manager.get_descriptions().values():
            emotion_path = os.path.join(MEMES_DIR, emotion)
            if not os.path.exists(emotion_path):
                logger.error(
                    f"表情分类 {emotion} 对应的目录不存在，请查看: {emotion_path}"
                )
                continue

            memes = [
                f
                for f in os.listdir(emotion_path)
                if f.endswith((".jpg", ".png", ".gif"))
            ]
            if not memes:
                logger.error(f"表情分类 {emotion} 对应的目录为空: {emotion_path}")
            else:
                logger.info(
                    f"表情分类 {emotion} 对应的目录 {emotion_path} 包含 {len(memes)} 个图片"
                )

    def _check_identify_circuit(self) -> bool:
        """
        识别熔断器检查。

        Returns:
            True  → 熔断中，应停止识别
            False → 正常，可以继续
        """
        threshold = self.meme_identify_circuit_threshold
        if threshold <= 0:
            return False  # 禁用熔断

        if self._circuit_failures < threshold:
            return False

        cooldown = self.meme_identify_circuit_cooldown
        elapsed = time.time() - self._circuit_tripped_at
        if elapsed >= cooldown:
            logger.info(
                f"[meme_manager] 🔄 识别熔断器冷却完成，恢复识别 "
                f"(连续失败 {self._circuit_failures} 次，冷却 {elapsed:.0f}s)"
            )
            self._circuit_failures = 0
            self._circuit_tripped_at = 0.0
            return False

        return True

    @filter.on_llm_request()
    async def _identify_meme(
        self, category: str, filename: str, provider_id: str = None
    ) -> bool:
        """
        使用 LLM 识别单张表情包并存储描述。

        返回 True 表示识别成功，False 表示失败/跳过。
        """
        if not self.meme_identify_enabled:
            return False

        provider_id = provider_id or self.meme_identify_provider_id
        if not provider_id:
            logger.warning("[meme_manager] meme_identify_provider_id 未配置，跳过识别")
            return False

        # 检查是否已有有效描述（避免重复识别）
        existing = self.description_manager.get(category, filename)
        if (
            existing
            and existing.get("description")
            and existing["description"] != "待识别"
        ):
            logger.debug(f"[meme_manager] {category}/{filename} 已有描述，跳过识别")
            return True

        # 读取图片文件
        from .config import MEMES_DIR

        image_path = MEMES_DIR / category / filename
        if not image_path.exists():
            logger.warning(f"[meme_manager] 图片不存在: {image_path}")
            return False

        try:
            import base64

            with open(image_path, "rb") as f:
                image_base64 = base64.b64encode(f.read()).decode("utf-8")

            # 构造 LLM 请求
            prompt = """请用中文简要描述这张表情包图片的内容，包含以下维度：

1. **画面描述**：画面中有什么（人物/动物/文字/物体等）
2. **表情/情绪**：角色的表情和情绪基调
3. **文字内容**：如果有文字，文字是什么
4. **适用场景**：这张表情包适合在什么社交场景下使用

然后给出 3-5 个中文标签（用逗号分隔）。

请严格按照以下 JSON 格式回复，不要有多余内容：
{"description": "描述文本", "tags": ["标签1", "标签2", "标签3"]}"""

            llm_response = await self._call_llm_vision(
                prompt=prompt,
                image_base64=image_base64,
                provider_id=provider_id,
            )

            if not llm_response:
                logger.warning(f"[meme_manager] LLM 返回空: {category}/{filename}")
                self._circuit_failures += 1
                return False

            # 解析 JSON 响应
            data = None
            # 尝试直接解析
            try:
                data = json.loads(llm_response.strip())
            except json.JSONDecodeError:
                # 尝试提取 JSON 块
                match = re.search(r"\{[\s\S]*\}", llm_response)
                if match:
                    try:
                        data = json.loads(match.group(0))
                    except json.JSONDecodeError:
                        pass

            if not data or "description" not in data:
                logger.warning(
                    f"[meme_manager] LLM 返回格式异常: {category}/{filename}: {llm_response[:200]}"
                )
                self._circuit_failures += 1
                return False

            description = str(data.get("description", "")).strip()
            tags = data.get("tags", [])
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]

            if not description:
                self._circuit_failures += 1
                return False

            self.description_manager.set(
                category=category,
                filename=filename,
                description=description,
                tags=tags,
                model=provider_id,
            )
            logger.info(f"[meme_manager] ✅ 识别成功: {category}/{filename}")
            self._circuit_failures = 0
            return True

        except Exception as e:
            logger.error(f"[meme_manager] 识别失败 {category}/{filename}: {e}")
            self._circuit_failures += 1
            if self._circuit_failures >= self.meme_identify_circuit_threshold > 0:
                self._circuit_tripped_at = time.time()
                logger.error(
                    f"[meme_manager] ⚡ 识别熔断器触发！"
                    f"连续失败 {self._circuit_failures} 次，"
                    f"暂停 {self.meme_identify_circuit_cooldown}s"
                )
            return False

    async def _identify_meme_batch(
        self, files: list[tuple[str, str]], provider_id: str = None
    ) -> dict:
        """
        批量识别表情包（带并发控制）

        参数:
            files: [(category, filename), ...]
        返回:
            {"success": n, "failed": n, "skipped": n}
        """
        if not files:
            return {"success": 0, "failed": 0, "skipped": 0}

        # ── 熔断器检查 ──
        if self._check_identify_circuit():
            remaining = self.meme_identify_circuit_cooldown - (time.time() - self._circuit_tripped_at)
            logger.warning(
                f"[meme_manager] ⚡ 熔断中，跳过批量识别 "
                f"(剩余冷却 {remaining:.0f}s，{len(files)} 张图片)"
            )
            return {"success": 0, "failed": 0, "skipped": len(files), "circuit_open": True}

        semaphore = asyncio.Semaphore(self.meme_identify_concurrency)

        async def identify_one(cat: str, fn: str):
            async with semaphore:
                return await self._identify_meme(cat, fn, provider_id)

        tasks = [identify_one(cat, fn) for cat, fn in files]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        success = sum(1 for r in results if r is True)
        failed = sum(1 for r in results if r is False)
        skipped = sum(1 for r in results if isinstance(r, Exception))

        if skipped:
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.error(f"批量识别异常 [{files[i]}]: {r}")

        return {"success": success, "failed": failed, "skipped": skipped}

    async def _identify_category(self, category: str, provider_id: str = None) -> dict:
        """
        识别指定类别下所有未识别的表情包

        返回: {"success": n, "failed": n, "skipped": n}
        """
        from .config import MEMES_DIR

        category_dir = MEMES_DIR / category
        if not category_dir.exists():
            return {"success": 0, "failed": 0, "skipped": 0}

        existing_files = {}
        for f in category_dir.iterdir():
            if f.is_file() and f.suffix.lower() in (
                ".jpg",
                ".jpeg",
                ".png",
                ".gif",
                ".webp",
                ".bmp",
            ):
                existing_files.setdefault(category, []).append(f.name)

        unidentified = self.description_manager.get_unidentified(existing_files)
        if not unidentified:
            return {"success": 0, "failed": 0, "skipped": 0}

        return await self._identify_meme_batch(unidentified, provider_id)

    async def _call_llm_vision(
        self,
        prompt: str,
        image_base64: str,
        provider_id: str,
        max_retries: int = 3,
        base_delay: float = 1.5,
    ) -> str | None:
        """
        调用 LLM 视觉模型识别图片（带指数退避重试）。
        v1.1.0: 适配 AstrBot v4.23.x 新版 text_chat API，不再使用已废弃的 LLMRequest。
        """
        for attempt in range(max_retries):
            try:
                provider = self.context.provider_manager.get_provider_by_id(provider_id)
                if not provider:
                    logger.warning(f"[meme_manager] 未找到 provider: {provider_id}")
                    return None

                response = await provider.text_chat(
                    prompt=prompt,
                    image_urls=[f"data:image/png;base64,{image_base64}"],
                )
                if response and hasattr(response, "completion_text"):
                    return response.completion_text
                return None

            except Exception as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    logger.warning(
                        f"[meme_manager] LLM 调用失败，{delay:.1f}s 后重试 "
                        f"({attempt + 1}/{max_retries}): {e}"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"[meme_manager] LLM 调用最终失败 "
                        f"({max_retries}/{max_retries}): {e}"
                    )

        return None

    async def _auto_identify_loop(self):
        """后台轮询识别队列（跨进程通信）。"""
        import json as _json

        logger.info("🔍 识别队列轮询已启动")
        while True:
            try:
                tasks = await self._read_identify_queue()
                if tasks is None:
                    await asyncio.sleep(3)
                    continue

                remaining = await self._process_identify_tasks(tasks)
                await self._write_remaining_tasks(remaining)

            except Exception as e:
                logger.error(f"[meme_manager] 识别队列轮询异常: {e}")

            await asyncio.sleep(3)


    # ── 识别队列辅助方法 ──────────────────────────────

    async def _read_identify_queue(self) -> list | None:
        """读取并清空队列文件，返回任务列表。无任务返回 None。"""
        import json as _json

        async with self._queue_lock:
            if not MEME_IDENTIFY_QUEUE_PATH.exists():
                return None

            queue_path = str(MEME_IDENTIFY_QUEUE_PATH)
            with flock_exclusive(queue_path):
                content = MEME_IDENTIFY_QUEUE_PATH.read_text(encoding="utf-8").strip()
                tasks = _json.loads(content) if content else []
                MEME_IDENTIFY_QUEUE_PATH.write_text("", encoding="utf-8")

        return tasks if tasks else None

    async def _process_identify_tasks(self, tasks: list) -> list:
        """逐条处理识别任务，返回未完成的任务列表。锁外执行，不阻塞新任务写入。"""
        remaining = []
        for task in tasks:
            action = task.get("action", "")
            cat = task.get("category", "")
            fn = task.get("filename", "")

            if self._check_identify_circuit():
                remaining.append(task)
                continue

            if action == "reidentify":
                self.description_manager.delete(cat, fn)

            try:
                ok = await self._identify_meme(cat, fn)
                if not ok:
                    remaining.append(task)
            except Exception as e:
                logger.warning(
                    f"[meme_manager] 识别失败 {cat}/{fn}: {e}，已隔离，继续处理剩余任务"
                )
                remaining.append(task)
        return remaining

    async def _write_remaining_tasks(self, remaining: list):
        """将未完成任务写回队列文件，合并期间新到达的任务。"""
        import json as _json

        async with self._queue_lock:
            merged = list(remaining)
            queue_path = str(MEME_IDENTIFY_QUEUE_PATH)
            with flock_exclusive(queue_path):
                if MEME_IDENTIFY_QUEUE_PATH.exists():
                    new_content = MEME_IDENTIFY_QUEUE_PATH.read_text(encoding="utf-8").strip()
                    if new_content:
                        try:
                            new_tasks = _json.loads(new_content)
                            if new_tasks:
                                merged = new_tasks + remaining
                        except _json.JSONDecodeError:
                            logger.warning(
                                "[meme_manager] 队列文件 JSON 解析失败，仅保留未完成任务"
                            )

                if merged:
                    MEME_IDENTIFY_QUEUE_PATH.write_text(
                        _json.dumps(merged, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                else:
                    MEME_IDENTIFY_QUEUE_PATH.unlink(missing_ok=True)

        if merged:
            logger.info(f"[meme_manager] 本轮识别完成，剩余 {len(merged)} 个任务")
        else:
            logger.info("[meme_manager] 队列全部处理完毕")
