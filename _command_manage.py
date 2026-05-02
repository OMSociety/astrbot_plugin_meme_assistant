"""管理命令 Mixin — 表情包增删改查及统计命令 + 命令辅助基类。"""

import os

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.utils.session_waiter import (
    SessionController,
    SessionFilter,
    session_waiter,
)

from .backend.models import (
    clear_all_emojis,
    clear_category_emojis,
    get_emoji_by_category,
)
from .config import IMAGE_EXTENSIONS, MEMES_DIR


class CommandManageMixin:
    """表情包管理命令实现 + 命令辅助基类（类别增删查、统计、恢复默认、确认流程等）。"""

    class ConfirmationCancelled(Exception):
        """危险操作被用户取消时抛出。"""

    class SenderScopedSessionFilter(SessionFilter):
        """将会话确认绑定到同一 session 中的同一发送者。"""

        def filter(self, event: AstrMessageEvent) -> str:
            sender_id = str(event.get_sender_id() or "").strip()
            return f"{event.unified_msg_origin}:{sender_id}"

    def _get_manageable_categories(self) -> set[str]:
        """返回已配置类别与本地类别的并集。"""
        return (
            set(self.category_manager.get_descriptions())
            | self.category_manager.get_local_categories()
        )

    async def _wait_for_command_confirmation(
        self, event: AstrMessageEvent, timeout: int = 30
    ) -> bool:
        """等待同一发送者回复确认文本。"""

        @session_waiter(timeout=timeout, record_history_chains=False)
        async def confirmation_waiter(
            controller: SessionController, confirm_event: AstrMessageEvent
        ) -> None:
            reply = (confirm_event.message_str or "").strip()

            if reply in {"确认", "确定"}:
                controller.stop()
                return

            if reply in {"取消", "退出"}:
                await confirm_event.send(confirm_event.plain_result("已取消本次操作。"))
                controller.stop(self.ConfirmationCancelled())
                return

            await confirm_event.send(
                confirm_event.plain_result(
                    '请回复"确认"继续执行，或回复"取消"终止本次操作。'
                )
            )
            controller.keep(timeout=timeout, reset_timeout=True)

        try:
            await confirmation_waiter(event, self.SenderScopedSessionFilter())
            return True
        except TimeoutError:
            await event.send(event.plain_result("⌛ 等待确认超时，操作已取消。"))
            return False
        except self.ConfirmationCancelled:
            return False

    def _format_category_counts(
        self, category_counts: dict[str, int], limit: int = 8
    ) -> str:
        """渲染压缩的类别数量摘要，用于确认提示。"""
        non_empty_items = [
            (category, count)
            for category, count in sorted(category_counts.items())
            if count > 0
        ]
        if not non_empty_items:
            return "无可删除的表情包文件。"

        lines = [
            f"- {category}: {count} 个" for category, count in non_empty_items[:limit]
        ]
        if len(non_empty_items) > limit:
            lines.append(f"- 其余 {len(non_empty_items) - limit} 个类型已省略")
        return "\n".join(lines)

    async def _stop_server_impl(self, event: AstrMessageEvent):
        """关闭表情包管理服务器。"""
        try:
            is_running = bool(self.webui_task and not self.webui_task.done())
            if not is_running:
                yield event.plain_result("ℹ️ 管理后台当前未运行。")
                return

            await self._shutdown_webui()
            yield event.plain_result("✅ 管理后台已关闭。")
        except Exception as e:
            yield event.plain_result(f"❌ 管理后台关闭失败：{str(e)}")

    async def _list_emotions_impl(self, event: AstrMessageEvent):
        """查看所有可用表情包类别。"""
        descriptions = self.category_mapping
        categories = "\n".join(
            [f"- {tag}: {desc}" for tag, desc in descriptions.items()]
        )
        yield event.plain_result(f"🖼️ 当前图库：\n{categories}")

    async def _restore_default_memes_impl(
        self, event: AstrMessageEvent, category: str = None
    ):
        """恢复默认表情包功能已移除 — 插件不再内置默认表情包资源。"""
        yield event.plain_result(
            "ℹ️ 插件不再内置默认表情包资源。\n"
            "请使用 /表情管理 添加表情 [类别名称] 自行上传表情包。"
        )

    async def _clear_category_impl(self, event: AstrMessageEvent, category: str = None):
        """清空指定类型下的所有表情包，但保留类型本身。"""
        if not category:
            yield event.plain_result(
                "📌 若要清空指定类型，请按照此格式操作：\n/表情管理 清空指定类型 [类别名称]"
            )
            return

        category = category.strip()
        available_categories = self._get_manageable_categories()
        if category not in available_categories:
            yield event.plain_result(
                f'⚠️ 未找到类型"{category}"。\n可先使用 /表情管理 查看图库 查看当前类型。'
            )
            return

        emoji_count = len(get_emoji_by_category(category))
        if emoji_count == 0:
            yield event.plain_result(f'📭 类型"{category}"当前没有可清空的表情包。')
            return

        yield event.plain_result(
            f'⚠️ 即将清空类型"{category}"下的 {emoji_count} 个表情包，但会保留类型本身。\n'
            '请在 30 秒内回复"确认"继续执行，或回复"取消"终止本次操作。'
        )
        if not await self._wait_for_command_confirmation(event):
            return

        result = clear_category_emojis(category)
        deleted_count = len(result["deleted_files"])
        yield event.plain_result(
            f'✅ 已清空类型"{category}"，共删除 {deleted_count} 个表情包。'
        )

    async def _clear_all_emojis_impl(self, event: AstrMessageEvent):
        """清空所有类型下的表情包，但保留类型和描述配置。"""
        available_categories = sorted(self._get_manageable_categories())
        category_counts = {
            category: len(get_emoji_by_category(category))
            for category in available_categories
        }
        total_count = sum(category_counts.values())

        if total_count == 0:
            yield event.plain_result("📭 当前没有可清空的表情包文件。")
            return

        category_count = sum(1 for count in category_counts.values() if count > 0)
        summary = self._format_category_counts(category_counts)
        yield event.plain_result(
            f"⚠️ 即将清空全部表情包，共 {total_count} 个文件，涉及 {category_count} 个类型。\n"
            "该操作会保留所有类型名称和描述配置。\n"
            f"{summary}\n"
            '请在 30 秒内回复"确认"继续执行，或回复"取消"终止本次操作。'
        )
        if not await self._wait_for_command_confirmation(event):
            return

        result = clear_all_emojis()
        deleted_total = sum(result["deleted_by_category"].values())
        yield event.plain_result(
            f"✅ 已清空全部表情包，共删除 {deleted_total} 个文件，类型配置已保留。"
        )

    async def _delete_category_impl(
        self, event: AstrMessageEvent, category: str = None
    ):
        """删除指定类型本身，同时移除其描述配置和本地文件夹。"""
        if not category:
            yield event.plain_result(
                "📌 若要删除类型本身，请按照此格式操作：\n/表情管理 删除类型本身 [类别名称]"
            )
            return

        category = category.strip()
        available_categories = self._get_manageable_categories()
        if category not in available_categories:
            yield event.plain_result(
                f'⚠️ 未找到类型"{category}"。\n可先使用 /表情管理 查看图库 查看当前类型。'
            )
            return

        emoji_count = len(get_emoji_by_category(category))
        yield event.plain_result(
            f'⚠️ 即将删除类型"{category}"本身，并移除其描述配置'
            f"{f'，同时删除其中的 {emoji_count} 个表情包' if emoji_count > 0 else ''}。\n"
            "该操作不可恢复。\n"
            '请在 30 秒内回复"确认"继续执行，或回复"取消"终止本次操作。'
        )
        if not await self._wait_for_command_confirmation(event):
            return

        if not self.category_manager.delete_category(category):
            yield event.plain_result(f'❌ 删除类型"{category}"失败，请稍后重试。')
            return

        self._reload_personas()
        yield event.plain_result(
            f'✅ 已删除类型"{category}"'
            f"{f'，并移除 {emoji_count} 个表情包。' if emoji_count > 0 else '。'}"
        )

    async def _explain_meme_impl(
        self, event: AstrMessageEvent, category: str, filename: str
    ):
        """查询单张表情包的 LLM 描述。"""
        data = self.description_manager.get(category, filename)
        if (
            not data
            or not data.get("description")
            or data.get("description") == "待识别"
        ):
            yield event.plain_result(
                f'🥺 表情包"{category}/{filename}"还没有识别的描述呢...\n'
                f"可以用 /表情识别 {category} 来批量识别该类别的表情包~"
            )
            return

        desc = data["description"]
        tags = data.get("tags", [])
        tags_str = "、".join(tags) if tags else "无"
        model = data.get("model", "未知")

        yield event.plain_result(
            f'🖼️ 表情包"{category}/{filename}"\n'
            f"📝 描述：{desc}\n"
            f"🏷️ 标签：{tags_str}\n"
            f"🤖 识别模型：{model}"
        )

    async def _identify_meme_command_impl(
        self, event: AstrMessageEvent, category: str = None
    ):
        """批量识别指定类别中未识别的表情包。"""
        if not self.meme_identify_enabled:
            yield event.plain_result(
                "⚠️ 表情包智能识别未启用，请先在 WebUI 配置 meme_identify_enabled"
            )
            return

        if not self.meme_identify_provider_id:
            yield event.plain_result(
                "⚠️ 未配置 meme_identify_provider_id，请在 WebUI 配置 LLM Provider"
            )
            return

        if category:
            yield event.plain_result(
                f'🔍 正在识别"{category}"类别下的表情包，请稍候...'
            )
            result = await self._identify_category(category)
            yield event.plain_result(
                f"✅ 识别完成！成功 {result['success']} 张，失败 {result['failed']} 张，"
                f"跳过 {result['skipped']} 张（已有描述）"
            )
        else:
            categories = [
                d
                for d in MEMES_DIR.iterdir()
                if d.is_dir() and not d.name.startswith(".")
            ]
            if not categories:
                yield event.plain_result("❌ 没有找到任何表情包类别")
                return

            yield event.plain_result(
                f"🔍 正在识别全部 {len(categories)} 个类别的表情包，请稍候..."
            )
            total = {"success": 0, "failed": 0, "skipped": 0}
            for cat_dir in categories:
                cat = cat_dir.name
                result = await self._identify_category(cat)
                for k in total:
                    total[k] += result[k]

            yield event.plain_result(
                f"✅ 全部识别完成！成功 {total['success']} 张，失败 {total['failed']} 张，"
                f"跳过 {total['skipped']} 张（已有描述）"
            )

    async def _reload_emotions_impl(self):
        """动态重新加载表情配置。"""
        try:
            self._memes_cache.clear()
            self.category_manager.sync_with_filesystem()
            self._reload_personas()
        except Exception as e:
            logger.error(f"重新加载表情配置失败: {str(e)}")

    async def _show_library_stats_impl(self, event: AstrMessageEvent):
        """显示图库详细统计信息。"""
        try:
            local_stats, local_total = self._collect_local_stats()
            remote_total = 0
            remote_stats = {}

            result = ["📊 表情包图库统计报告", "", "📁 本地图库统计:"]
            result.extend(self._build_local_stats_section(local_stats, local_total))

            if self.img_sync:
                result.append("")
                result.append("☁️ 云端图库统计:")
                cloud_lines, remote_total, remote_stats = (
                    self._build_cloud_stats_section()
                )
                result.extend(cloud_lines)
                result.append("")
                result.append("📈 本地与云端对比:")
                result.extend(
                    self._build_comparison_section(
                        local_total, remote_total, local_stats, remote_stats
                    )
                )
            else:
                result.append("")
                result.append("☁️ 云端图库: 未配置")

            result.extend(self._build_storage_estimation(local_total, remote_total))
            yield event.plain_result("\n".join(result))
        except Exception as e:
            logger.error(f"获取图库统计失败: {str(e)}")
            yield event.plain_result(f"获取图库统计失败: {str(e)}")

    # ── 图库统计辅助方法 ──────────────────────────────

    @staticmethod
    def _collect_local_stats() -> tuple:
        """收集本地图库统计 (类别->数量, 总数)。"""
        local_stats = {}
        local_total = 0
        if os.path.exists(MEMES_DIR):
            for category in os.listdir(MEMES_DIR):
                cp = os.path.join(MEMES_DIR, category)
                if os.path.isdir(cp):
                    images = [f for f in os.listdir(cp) if f.endswith(IMAGE_EXTENSIONS)]
                    if images:
                        local_stats[category] = len(images)
                        local_total += len(images)
        return local_stats, local_total

    @staticmethod
    def _build_local_stats_section(local_stats: dict, local_total: int) -> list:
        if not local_stats:
            return ["  • 本地图库为空"]
        lines = [
            f"  • 总文件数: {local_total} 个",
            f"  • 分类数: {len(local_stats)} 个",
            "",
            "📂 本地分类详情:",
        ]
        for cat, count in sorted(local_stats.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  • {cat}: {count} 个")
        return lines

    def _build_cloud_stats_section(self) -> tuple:
        """构建云端统计段落，返回 (lines, remote_total, remote_stats)。"""
        try:
            remote_images = self.img_sync.provider.get_image_list()
            remote_total = len(remote_images)
            remote_stats = {}
            for img in remote_images:
                cat = img.get("category", "未分类")
                remote_stats[cat] = remote_stats.get(cat, 0) + 1

            lines = [
                f"  • 总文件数: {remote_total} 个",
                f"  • 分类数: {len(remote_stats)} 个",
                "",
                "📂 云端分类详情:",
            ]
            for cat, count in sorted(
                remote_stats.items(), key=lambda x: x[1], reverse=True
            ):
                lines.append(f"  • {cat}: {count} 个")
            return lines, remote_total, remote_stats
        except Exception as e:
            return [f"  • 获取云端统计失败: {str(e)}"], 0, {}

    @staticmethod
    def _build_comparison_section(
        local_total: int, remote_total: int, local_stats: dict, remote_stats: dict
    ) -> list:
        """构建本地 vs 云端对比段落。"""
        lines = [
            f"  • 本地文件: {local_total} 个",
            f"  • 云端文件: {remote_total} 个",
        ]
        if local_total > remote_total:
            lines.append(f"  • 本地比云端多 {local_total - remote_total} 个文件")
        elif remote_total > local_total:
            lines.append(f"  • 云端比本地多 {remote_total - local_total} 个文件")
        else:
            lines.append("  • 本地与云端文件数相同")

        local_cats = set(local_stats.keys())
        remote_cats = set(remote_stats.keys())
        only_local = local_cats - remote_cats
        only_remote = remote_cats - local_cats
        common = local_cats & remote_cats

        if only_local:
            lines.append(f"  • 仅本地有的分类: {', '.join(sorted(only_local))}")
        if only_remote:
            lines.append(f"  • 仅云端有的分类: {', '.join(sorted(only_remote))}")
        if common:
            lines.append(f"  • 共同分类: {len(common)} 个")
        return lines

    @staticmethod
    def _build_storage_estimation(local_total: int, remote_total: int) -> list:
        """构建存储空间估算段落。"""
        lines = ["", "💾 存储空间估算:"]
        if local_total > 0:
            lines.append(f"  • 本地图库约: {local_total * 500 / 1024:.1f} MB")
        if remote_total > 0:
            lines.append(f"  • 云端图库约: {remote_total * 500 / 1024:.1f} MB")
        return lines
