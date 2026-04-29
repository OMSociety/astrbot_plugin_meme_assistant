import asyncio
import json
import os
import random
import re
import tempfile
import time

from PIL import Image as PILImage

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.provider import LLMResponse

from .config import (
    MEMES_DIR,
)
from ._prompt_renderer import PromptRenderer
from ._meme_recommender import MemeRecommender


class EmotionMixin:
    def _is_position_in_thinking_tags(self, text: str, position: int) -> bool:
        """检查指定位置是否在thinking标签内

        Args:
            text: 原始文本
            position: 要检查的位置

        Returns:
            True如果位置在thinking标签内，False否则
        """
        # 找到所有thinking标签的开始和结束位置
        thinking_pattern = re.compile(
            r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE
        )

        for match in thinking_pattern.finditer(text):
            if match.start() <= position < match.end():
                return True
        return False

    @filter.on_llm_request()
    async def inject_meme_tool_prompt(self, event, req):
        """向 LLM 注入 send_meme 工具使用提示（模板化）"""
        if not self.meme_llm_tool_enabled:
            return
        available_categories = list(self.category_mapping.keys())
        if not available_categories:
            return
        instruction = self.renderer.render_meme_tool_prompt(available_categories)
        req.system_prompt = (req.system_prompt or "") + instruction

    @filter.on_llm_response(priority=99999)
    async def resp(self, event: AstrMessageEvent, response: LLMResponse):
        """处理 LLM 响应，识别表情"""

        if not response or not response.completion_text:
            return

        text = response.completion_text
        self.found_emotions = []

        # 阶段1-4: 逐步清理文本并收集表情
        clean_text = self._phase_strict_matching(text)
        clean_text = self._phase_alternative_markup(clean_text)
        clean_text = self._phase_repeated_emotions(clean_text)
        clean_text = self._phase_loose_matching(clean_text)

        # 阶段5: LLM 辅助识别（可选）
        await self._phase_llm_emotion(clean_text, event)

        # 阶段6: 去重、限数、清理
        self._phase_finalize(response, clean_text)

    # ===== Phase helper methods =====

    def _phase_strict_matching(self, text: str) -> str:
        """第一阶段：严格匹配 &&emotion&& 符号包裹的表情"""
        valid_emoticons = set(self.category_mapping.keys())
        hex_pattern = r"&&([^&&]+)&&"
        matches = re.finditer(hex_pattern, text)

        temp_replacements = []
        for match in matches:
            original = match.group(0)
            emotion = match.group(1).strip()
            if emotion in valid_emoticons:
                temp_replacements.append((original, emotion))
            else:
                temp_replacements.append((original, ""))

        clean_text = text
        for original, emotion in temp_replacements:
            clean_text = clean_text.replace(original, "", 1)
            if emotion:
                self.found_emotions.append(emotion)

        return clean_text

    def _phase_alternative_markup(self, text: str) -> str:
        """第二阶段：处理替代标记 [emotion] 和 (emotion)"""
        if not self.config.get("enable_alternative_markup", True):
            return text

        valid_emoticons = set(self.category_mapping.keys())
        remove_invalid = self.remove_invalid_alternative_markup
        clean_text = text

        # [emotion] 格式
        bracket_pattern = r"\[([^\[\]]+)\]"
        matches = re.finditer(bracket_pattern, clean_text)
        bracket_replacements = []
        invalid_brackets = [] if remove_invalid else None

        for match in matches:
            original = match.group(0)
            emotion = match.group(1).strip()
            if emotion in valid_emoticons:
                bracket_replacements.append((original, emotion))
            elif remove_invalid:
                invalid_brackets.append(original)

        if remove_invalid:
            for invalid in invalid_brackets:
                clean_text = clean_text.replace(invalid, "", 1)

        for original, emotion in bracket_replacements:
            clean_text = clean_text.replace(original, "", 1)
            self.found_emotions.append(emotion)

        # (emotion) 格式
        paren_pattern = r"\(([^()]+)\)"
        matches = re.finditer(paren_pattern, clean_text)
        paren_replacements = []
        invalid_parens = [] if remove_invalid else None

        for match in matches:
            original = match.group(0)
            emotion = match.group(1).strip()
            if emotion in valid_emoticons:
                if self._is_likely_emotion_markup(original, clean_text, match.start()):
                    paren_replacements.append((original, emotion))
            elif remove_invalid:
                invalid_parens.append(original)

        if remove_invalid:
            for invalid in invalid_parens:
                clean_text = clean_text.replace(invalid, "", 1)

        for original, emotion in paren_replacements:
            clean_text = clean_text.replace(original, "", 1)
            self.found_emotions.append(emotion)

        return clean_text

    def _phase_repeated_emotions(self, text: str) -> str:
        """第三阶段：检测重复表情模式（如 angryangryangry）"""
        if not self.config.get("enable_repeated_emotion_detection", True):
            return text

        valid_emoticons = set(self.category_mapping.keys())
        high_confidence = self.config.get("high_confidence_emotions", [])
        clean_text = text
        repeated = []

        for emotion in valid_emoticons:
            if len(emotion) < 3:
                continue

            if emotion in high_confidence:
                repeat_pattern = f"({re.escape(emotion)})\1{{1,}}"
            else:
                if len(emotion) < 4:
                    continue
                repeat_pattern = f"({re.escape(emotion)})\1{{2,}}"

            matches = re.finditer(repeat_pattern, clean_text)
            for match in matches:
                if self._is_position_in_thinking_tags(clean_text, match.start()):
                    continue
                original = match.group(0)
                clean_text = clean_text.replace(original, "", 1)
                self.found_emotions.append(emotion)
                repeated.append(emotion)

        logger.debug(f"[meme_manager] 重复检测阶段找到的表情: {repeated}")
        return clean_text

    def _phase_loose_matching(self, text: str) -> str:
        """第四阶段：智能识别可能的表情（松散模式）"""
        if not self.config.get("enable_loose_emotion_matching", True):
            return text

        valid_emoticons = set(self.category_mapping.keys())
        clean_text = text
        loose_emotions = []

        for emotion in valid_emoticons:
            pattern = r"\b(" + re.escape(emotion) + r")\b"
            for match in re.finditer(pattern, clean_text):
                word = match.group(1)
                position = match.start()

                if self._is_position_in_thinking_tags(clean_text, position):
                    continue

                if self._is_likely_emotion(word, clean_text, position, valid_emoticons):
                    self.found_emotions.append(word)
                    loose_emotions.append(word)
                    clean_text = (
                        clean_text[:position] + clean_text[position + len(word) :]
                    )

        logger.debug(f"[meme_manager] 松散匹配阶段找到的表情: {loose_emotions}")
        return clean_text

    async def _phase_llm_emotion(self, clean_text: str, event):
        """第五阶段：LLM 辅助情感识别（可选）"""
        if not self.emotion_llm_enabled:
            return

        valid_emoticons = set(self.category_mapping.keys())
        try:
            provider_id = self.emotion_llm_provider_id
            if not provider_id:
                provider_id = await self.context.get_current_chat_provider_id(
                    umo=event.unified_msg_origin
                )
            if provider_id:
                valid_list = sorted(valid_emoticons)
                prompt = self.renderer.render_emotion_llm_prompt(valid_list, clean_text)
                llm_resp = await self.context.llm_generate(
                    chat_provider_id=provider_id, prompt=prompt
                )
                if llm_resp and llm_resp.completion_text:
                    raw_text = llm_resp.completion_text.strip()
                    data = None
                    try:
                        data = json.loads(raw_text)
                    except Exception:
                        match = re.search(r"\{[\s\S]*\}", raw_text)
                        if match:
                            try:
                                data = json.loads(match.group(0))
                            except Exception:
                                data = None
                    if isinstance(data, dict):
                        emotions = data.get("emotions")
                        if isinstance(emotions, list):
                            for emo in emotions:
                                if isinstance(emo, str) and emo in valid_emoticons:
                                    self.found_emotions.append(emo)
                        elif isinstance(emotions, str) and emotions in valid_emoticons:
                            self.found_emotions.append(emotions)
        except Exception as e:
            logger.error(f"[meme_manager] 情感模型调用失败: {e}")

    def _phase_finalize(self, response, clean_text: str):
        """第六阶段：去重、应用数量限制、清理残留符号"""
        seen = set()
        filtered = []
        for emo in self.found_emotions:
            if emo not in seen:
                seen.add(emo)
                filtered.append(emo)
            if len(filtered) >= self.max_emotions_per_message:
                break

        self.found_emotions = filtered
        logger.info(f"[meme_manager] 去重后的最终表情列表: {self.found_emotions}")

        clean_text = re.sub(r"&&+", "", clean_text)
        response.completion_text = clean_text.strip()
        logger.debug(
            f"[meme_manager] 清理后的最终文本内容长度: {len(response.completion_text)}"
        )

    def _is_likely_emotion_markup(self, markup, text, position):
        """判断一个标记是否可能是表情而非普通文本的一部分"""
        # 获取标记前后的文本
        before_text = text[:position].strip()
        after_text = text[position + len(markup) :].strip()

        # 如果是在中文上下文中，更可能是表情
        has_chinese_before = bool(
            re.search(r"[\u4e00-\u9fff]", before_text[-1:] if before_text else "")
        )
        has_chinese_after = bool(
            re.search(r"[\u4e00-\u9fff]", after_text[:1] if after_text else "")
        )
        if has_chinese_before or has_chinese_after:
            return True

        # 如果在数字标记中，可能是引用标记如[1]，不是表情
        if re.match(r"\[\d+\]", markup):
            return False

        # 如果标记内有空格，可能是普通句子，不是表情
        if " " in markup[1:-1]:
            return False

        # 如果标记前后是完整的英文句子，可能不是表情
        english_context_before = bool(re.search(r"[a-zA-Z]\s+$", before_text))
        english_context_after = bool(re.search(r"^\s+[a-zA-Z]", after_text))
        if english_context_before and english_context_after:
            return False

        # 默认情况下认为可能是表情
        return True

    def _is_likely_emotion(self, word, text, position, valid_emotions):
        """判断一个单词是否可能是表情而非普通英文单词"""

        # 先获取上下文
        before_text = text[:position].strip()
        after_text = text[position + len(word) :].strip()

        # 规则1：检查是否在英文上下文中
        # 如果前面有英文单词+空格，或后面有空格+英文单词，可能是英文上下文
        english_context_before = bool(re.search(r"[a-zA-Z]\s+$", before_text))
        english_context_after = bool(re.search(r"^\s+[a-zA-Z]", after_text))

        # 在英文上下文中，不太可能是表情
        if english_context_before or english_context_after:
            return False

        # 规则2：前后有中文字符，更可能是表情
        has_chinese_before = bool(
            re.search(r"[\u4e00-\u9fff]", before_text[-1:] if before_text else "")
        )
        has_chinese_after = bool(
            re.search(r"[\u4e00-\u9fff]", after_text[:1] if after_text else "")
        )

        if has_chinese_before or has_chinese_after:
            return True

        # 规则3：如果是句子开头或结尾，可能是表情
        if not before_text or before_text.endswith(
            ("。", "，", "！", "？", ".", ",", ":", ";", "!", "?", "\n")
        ):
            return True

        # 规则4：如果前后都是标点或空格，可能是表情
        if (not before_text or before_text[-1] in " \t\n.,!?;:'\"()[]{}") and (
            not after_text or after_text[0] in " \t\n.,!?;:'\"()[]{}"
        ):
            return True

        # 规则5：如果是已知的表情占比很高(>=70%)的单词，即使在英文上下文中也可能是表情
        if word in self.config.get("high_confidence_emotions", []):
            return True

        return False

    def _convert_to_gif(self, image_path: str) -> str:
        """
        将静态图片转换为 GIF 格式。
        如果图片已经是 GIF，则返回原路径。
        如果转换成功，返回临时 GIF 文件的路径。
        """
        if not self.convert_static_to_gif:
            return image_path

        if image_path.lower().endswith(".gif"):
            return image_path

        try:
            with PILImage.open(image_path) as img:
                # 检查是否已经是 GIF (虽然后缀不是 .gif，但内容可能是)
                if img.format == "GIF":
                    return image_path

                # 创建临时文件
                temp_dir = tempfile.gettempdir()
                temp_filename = os.path.join(
                    temp_dir,
                    f"meme_{int(time.time())}_{random.randint(1000, 9999)}.gif",
                )

                # 转换为 RGB (如果是 RGBA 需要处理透明度)
                if img.mode in ("RGBA", "LA") or (
                    img.mode == "P" and "transparency" in img.info
                ):
                    # 创建白色背景
                    background = PILImage.new("RGB", img.size, (255, 255, 255))
                    if img.mode == "P":
                        img = img.convert("RGBA")
                    background.paste(img, mask=img.split()[3])  # 3 is the alpha channel
                    img = background
                else:
                    img = img.convert("RGB")

                # 保存为 GIF
                img.save(temp_filename, "GIF")
                logger.debug(f"[meme_manager] 已将静态图转换为 GIF: {temp_filename}")
                return temp_filename
        except Exception as e:
            logger.error(f"[meme_manager] 转换图片为 GIF 失败: {e}")
            return image_path

    def _list_memes_in_category(self, category: str) -> list[str]:
        """列出指定类别目录下的所有图片文件（带 TTL 缓存）"""
        cache_key = f"memes_list_{category}"
        now = time.time()
        entry = self._memes_cache.get(cache_key)
        if entry and (now - entry[1]) < 60:
            return entry[0]

        emotion_path = os.path.join(MEMES_DIR, category)
        if not os.path.exists(emotion_path):
            self._memes_cache[cache_key] = ([], now)
            return []

        memes = [
            f
            for f in os.listdir(emotion_path)
            if f.lower().endswith((".jpg", ".png", ".gif", ".webp"))
        ]
        self._memes_cache[cache_key] = (memes, now)
        return memes

    def _select_meme_for_category(
        self, category: str
    ) -> tuple[str | None, str | None, bool]:
        """Pipeline：选择表情主流程
        返回 (meme_name, final_path, is_temp)
        - meme_name: 原始文件名
        - final_path: 最终文件路径（可能是转换后的 GIF）
        - is_temp: 是否需要调用方清理临时文件
        """
        memes = self._list_memes_in_category(category)
        if not memes:
            return None, None, False

        meme = self.recommender.select(category, memes)
        if meme is None:
            meme = random.choice(memes)  # fallback
        original_path = os.path.join(MEMES_DIR, category, meme)
        final_path = self._convert_to_gif(original_path)
        is_temp = final_path != original_path
        return meme, final_path, is_temp

    # ═══════ P2: Pipeline 方法 ═══════

    async def _send_emotion(
        self, event: AstrMessageEvent, category: str
    ) -> tuple[str | None, bool]:
        """Pipeline: 选择→发送→清理 一张表情。

        Returns:
            (meme_name, success): meme_name 是选中的文件名，success 表示是否发送成功
        """
        meme_name, final_path, is_temp = self._select_meme_for_category(category)
        if not final_path:
            return None, False
        try:
            await self._send_image_to_event(event, final_path)
            return meme_name, True
        except Exception as e:
            logger.error(f"[meme_manager] 发送表情失败: {e}")
            return meme_name, False
        finally:
            if is_temp and os.path.exists(final_path):
                try:
                    os.remove(final_path)
                except Exception:
                    pass

    @filter.llm_tool(name="send_meme")
    async def send_meme_tool(self, event: AstrMessageEvent, category: str) -> str:
        """LLM 工具：发送指定类别的表情包图片。

        Args:
            category (str): 表情类别名称，如 happy、cute、angry 等

        Returns:
            str: 发送结果描述字符串
        """
        if not self.meme_llm_tool_enabled:
            return "send_meme 工具未启用"

        # 验证类别
        if category not in self.category_mapping:
            available = "、".join(list(self.category_mapping.keys())[:15])
            return f"表情类别「{category}」不存在。可用类别示例：{available}"

        # Pipeline: 选择→发送→清理
        meme_name, success = await self._send_emotion(event, category)
        if meme_name is None:
            return f"表情类别「{category}」暂无表情包图片，请先上传。"
        if success:
            return f"已成功发送一张「{category}」表情包 ({meme_name})。"
        return f"表情类别「{category}」的表情包 ({meme_name}) 发送失败。"
