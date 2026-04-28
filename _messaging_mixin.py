import os
import random
import re
import traceback

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain, ResultContentType


class MessagingMixin:
    async def _send_image_to_event(
        self, event: AstrMessageEvent, image: str | Image
    ) -> None:
        """统一消息发送：根据平台类型选择 event.send 或 context.send_message"""
        if isinstance(image, str):
            image = Image.fromFileSystem(image)
        chain = MessageChain([image])
        if event.get_platform_name() == "gewechat":
            await event.send(chain)
        else:
            await self.context.send_message(event.unified_msg_origin, chain)

    async def _send_memes_streaming(self, event: AstrMessageEvent):
        """流式传输兼容模式：在流式消息发送完成后，主动发送表情图片作为独立消息。"""
        if not self.found_emotions:
            return

        try:
            random_value = random.randint(1, 100)
            if random_value > self.emotions_probability:
                return

            for emotion in self.found_emotions:
                if not emotion:
                    continue
                await self._send_emotion(event, emotion)
        except Exception as e:
            logger.error(f"[meme_manager] 流式模式处理表情失败: {e}")
            logger.error(traceback.format_exc())
        finally:
            self.found_emotions = []

    @filter.on_decorating_result(priority=99999)
    async def on_decorating_result(self, event: AstrMessageEvent):
        """在消息发送前清理文本中的表情标签，并添加表情图片"""
        logger.debug("[meme_manager] on_decorating_result 开始处理")

        result = event.get_result()
        if not result:
            return

        # 流式传输兼容处理
        if result.result_content_type == ResultContentType.STREAMING_FINISH:
            if self.streaming_compatibility:
                await self._send_memes_streaming(event)
            return

        try:
            # 第一步：获取并清理原始消息链中的文本
            original_chain = result.chain
            cleaned_components = []

            if original_chain:
                cleaned_components = self._clean_chain_components(
                    self._normalize_chain(original_chain)
                )

            # 第二步：收集并处理表情图片
            if self.found_emotions:
                random_value = random.randint(1, 100)
                if random_value > self.emotions_probability:
                    pass  # 不发送
                else:
                    emotion_images = []
                    temp_files = []
                    for emotion in self.found_emotions:
                        if not emotion:
                            continue
                        meme_name, final_path, is_temp = self._select_meme_for_category(
                            emotion
                        )
                        if not final_path:
                            continue
                        if is_temp:
                            temp_files.append(final_path)
                        try:
                            emotion_images.append(Image.fromFileSystem(final_path))
                        except Exception as e:
                            logger.error(f"添加表情图片失败: {e}")

                    if emotion_images:
                        if temp_files:
                            existing_temp_files = (
                                event.get_extra("meme_manager_temp_files") or []
                            )
                            event.set_extra(
                                "meme_manager_temp_files",
                                existing_temp_files + temp_files,
                            )

                        use_mixed_message = (
                            self.enable_mixed_message
                            and random.randint(1, 100) <= self.mixed_message_probability
                        )
                        if use_mixed_message:
                            cleaned_components = self._merge_components_with_images(
                                cleaned_components, emotion_images
                            )
                        else:
                            event.set_extra(
                                "meme_manager_pending_images", emotion_images
                            )

                # 清空已处理的表情列表
                self.found_emotions = []

            # 第三步：更新消息链
            if cleaned_components:
                result.chain = cleaned_components
            elif original_chain:
                # 防御性清理 && 残留
                raw = self._normalize_chain(original_chain)
                final = []
                for comp in raw:
                    if isinstance(comp, Plain):
                        t = re.sub(r"&&+", "", comp.text)
                        if t.strip():
                            final.append(Plain(t.strip()))
                    else:
                        final.append(comp)
                if final:
                    result.chain = final

            logger.debug("[meme_manager] on_decorating_result 处理完成")

        except Exception as e:
            logger.error(f"处理消息装饰失败: {str(e)}")
            logger.error(traceback.format_exc())

    @filter.after_message_sent()
    async def after_message_sent(self, event: AstrMessageEvent):
        """消息发送后处理。用于发送未混合的表情图片。"""
        pending_images = event.get_extra("meme_manager_pending_images")

        try:
            if pending_images:
                for image in pending_images:
                    await self._send_image_to_event(event, image)
        except Exception as e:
            logger.error(f"发送表情图片失败: {str(e)}")
            logger.error(traceback.format_exc())
        finally:
            event.set_extra("meme_manager_pending_images", None)
            self._cleanup_temp_files(event)

    # ──────────────────────────────────────────────────────
    #  Phase 2: 表情包智能识别
    # ──────────────────────────────────────────────────────

    def _normalize_chain(self, chain) -> list:
        """统一消息链格式：str / MessageChain / list → list[Component]"""
        if isinstance(chain, str):
            return [Plain(chain)] if chain.strip() else []
        if isinstance(chain, MessageChain):
            return list(chain.chain)
        if isinstance(chain, list):
            return chain
        return []

    def _clean_chain_components(self, components: list) -> list:
        """清理组件列表中的表情标签，返回清理后的组件列表"""
        cleaned = []
        for comp in components:
            if isinstance(comp, Plain):
                text = comp.text
                if self.content_cleanup_rule:
                    text = re.sub(self.content_cleanup_rule, "", text)
                if text.strip():
                    cleaned.append(Plain(text.strip()))
            else:
                cleaned.append(comp)
        return cleaned

    def _cleanup_temp_files(self, event: AstrMessageEvent) -> None:
        """清理 event 上暂存的临时文件"""
        temp_files = event.get_extra("meme_manager_temp_files")
        if not temp_files:
            return
        for temp_file in temp_files:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception as e:
                logger.error(f"[meme_manager] 清理临时文件失败: {e}")
        event.set_extra("meme_manager_temp_files", None)

    def _merge_components_with_images(self, components, images):
        """将表情图片与文本组件智能配对，支持分段回复

        Args:
            components: 清理后的消息组件列表
            images: 表情图片列表

        Returns:
            合并后的消息组件列表，图片会合理地分布在文本中
        """
        logger.debug(
            f"[meme_manager] _merge_components_with_images 输入: 组件总数={len(components)}, 图片总数={len(images)}"
        )

        if not images:
            return components

        if not components:
            # 没有文本组件，只发送图片
            return images

        # 找到所有 Plain 组件的索引
        plain_indices = [
            i for i, comp in enumerate(components) if isinstance(comp, Plain)
        ]
        logger.debug(f"[meme_manager] Plain 组件的索引位置列表: {plain_indices}")

        if not plain_indices:
            # 没有 Plain 组件，直接添加图片到末尾
            return components + images

        # 策略：将图片均匀分布在文本组件中，优先在文本后添加图片
        # 这样在分段回复时，图片更容易和对应的文本一起发送
        merged_components = components.copy()
        images_per_text = max(
            1, len(images) // len(plain_indices)
        )  # 每个文本至少配一张图片
        image_index = 0
        images_inserted_so_far = 0  # 跟踪已插入的图片数量

        for idx, plain_idx in enumerate(plain_indices):
            if image_index >= len(images):
                break

            # 计算这个文本应该配多少张图片
            if idx == len(plain_indices) - 1:
                # 最后一个文本组件，分配所有剩余图片
                images_for_this_text = len(images) - image_index
            else:
                images_for_this_text = min(images_per_text, len(images) - image_index)

            logger.debug(
                f"[meme_manager] Plain 组件 {idx} (索引={plain_idx}) 分配的图片数量: {images_for_this_text}"
            )

            # 在这个文本组件后插入图片
            # 注意：plain_idx 是在原始 components 中的位置，但由于我们已经插入了一些图片，
            # 需要考虑已插入图片对当前位置的影响
            insert_pos = plain_idx + 1 + images_inserted_so_far

            for _ in range(images_for_this_text):
                if image_index < len(images):
                    merged_components.insert(insert_pos, images[image_index])
                    image_index += 1
                    insert_pos += 1
                    images_inserted_so_far += 1

        logger.debug(
            f"[meme_manager] 合并前组件总数: {len(components)}, 合并后组件总数: {len(merged_components)}"
        )

        return merged_components
