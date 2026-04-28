"""上传/同步命令 Mixin — 图片上传、图床同步等命令实现。"""

import asyncio
import io
import os
import ssl
import time

import aiohttp
from PIL import Image as PILImage

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image, Plain

from ._command_manage import CommandManageMixin
from .config import MEMES_DIR


class CommandUploadMixin(CommandManageMixin):
    """图片上传与图床同步命令实现。"""

    async def _upload_meme_impl(self, event: AstrMessageEvent, category: str = None):
        """上传表情包到指定类别。"""
        if not category:
            yield event.plain_result(
                "📌 若要添加表情，请按照此格式操作：\n/表情管理 添加表情 [类别名称]\n（输入/查看图库 可获取类别列表）"
            )
            return

        if category not in self.category_manager.get_descriptions():
            yield event.plain_result(
                f'您输入的表情包类别"{category}"是无效的哦。\n可以使用/查看表情包来查看可用的类别。'
            )
            return

        user_key = f"{event.session_id}_{event.get_sender_id()}"
        async with self._upload_lock:
            self.upload_states[user_key] = {
                "category": category,
                "expire_time": time.time() + 30,
            }
        yield event.plain_result(
            f"请在30秒内发送要添加到【{category}】类别的图片（可发送多张图片）。"
        )

    async def _handle_upload_image_impl(self, event: AstrMessageEvent):
        """处理用户上传的图片。"""
        user_key = f"{event.session_id}_{event.get_sender_id()}"

        async with self._upload_lock:
            upload_state = self.upload_states.get(user_key)
            if not upload_state or time.time() > upload_state["expire_time"]:
                if user_key in self.upload_states:
                    del self.upload_states[user_key]
                return

        images = [c for c in event.message_obj.message if isinstance(c, Image)]
        if not images:
            yield event.plain_result("请发送图片文件来进行上传哦。")
            return

        category = upload_state["category"]
        save_dir = os.path.join(MEMES_DIR, category)

        try:
            os.makedirs(save_dir, exist_ok=True)
            saved_files = await self._download_images(images, save_dir)

            del self.upload_states[user_key]

            result_msg = [
                Plain(f'✅ 已经成功收录了 {len(saved_files)} 张新表情到"{category}"图库！')
            ]
            if self.img_sync:
                result_msg.append(Plain("\n"))
                result_msg.append(Plain("☁️ 检测到已配置图床，如需同步到云端请使用命令：同步到云端"))

            yield event.chain_result(result_msg)
            await self.reload_emotions()

            if saved_files and self.meme_identify_enabled and self.meme_identify_on_upload:
                asyncio.ensure_future(
                    self._identify_meme_batch(
                        [(category, fn) for fn in saved_files],
                        provider_id=self.meme_identify_provider_id,
                    )
                )
        except Exception as e:
            yield event.plain_result(f"保存失败了：{str(e)}")

    async def _check_sync_status_impl(
        self, event: AstrMessageEvent, detail: str = None
    ):
        """检查表情包与图床的同步状态。"""
        if not self.img_sync:
            yield event.plain_result(
                "图床服务尚未配置，请先在插件页面的配置中完成图床配置哦。"
            )
            return

        try:
            provider_name = self.img_sync.provider.__class__.__name__
            storage_info = self._get_storage_info()
            status = self.img_sync.check_status()
            to_upload = status.get("to_upload", [])
            to_download = status.get("to_download", [])

            result = [
                "📊 图床同步状态报告",
                "",
                f"🔧 图床服务: {provider_name}",
                f"📁 {storage_info}",
                "",
                "📈 文件统计:",
                f"  • 需要上传: {len(to_upload)} 个文件",
                f"  • 需要下载: {len(to_download)} 个文件",
                "",
            ]

            result.extend(self._build_category_stats(to_upload, to_download))
            result.extend(self._build_file_examples(to_upload, to_download))

            if not to_upload and not to_download:
                result.append("✅ 云端与本地图库已经完全同步啦！")
                if detail and detail.strip() == "详细":
                    result.extend(self._build_synced_detail())
            else:
                result.append("⏳ 需要同步以保持云端与本地图库一致")
                result.append(
                    "💡 使用 '/表情管理 同步到云端' 或 '/表情管理 从云端同步' 进行同步"
                )

            self._append_upload_tracker_info(result)

            yield event.plain_result("\n".join(result))
        except Exception as e:
            logger.error(f"检查同步状态失败: {str(e)}")
            yield event.plain_result(f"检查同步状态失败: {str(e)}")

    async def _sync_to_remote_impl(self, event: AstrMessageEvent):
        """将本地表情包同步到云端。"""
        if not self.img_sync:
            yield event.plain_result(
                "图床服务尚未配置，请先在配置文件中完成图床配置哦。"
            )
            return

        try:
            yield event.plain_result("⚡ 正在开启云端同步任务...")
            success = await self.img_sync.start_sync("upload")
            if success:
                yield event.plain_result("云端同步已完成！")
            else:
                yield event.plain_result("云端同步失败，请查看日志哦。")
        except Exception as e:
            logger.error(f"同步到云端失败: {str(e)}")
            yield event.plain_result(f"同步到云端失败: {str(e)}")

    async def _sync_from_remote_impl(self, event: AstrMessageEvent):
        """从云端同步表情包到本地。"""
        if not self.img_sync:
            yield event.plain_result(
                "图床服务尚未配置，请先在配置文件中完成图床配置哦。"
            )
            return

        try:
            yield event.plain_result("开始从云端进行同步...")
            success = await self.img_sync.start_sync("download")
            if success:
                yield event.plain_result("从云端同步已完成！")
                await self.reload_emotions()
            else:
                yield event.plain_result("从云端同步失败，请查看日志哦。")
        except Exception as e:
            logger.error(f"从云端同步失败: {str(e)}")
            yield event.plain_result(f"从云端同步失败: {str(e)}")

    async def _overwrite_to_remote_impl(self, event: AstrMessageEvent):
        """让云端完全和本地一致（会删除云端多出的图）。"""
        if not self.img_sync:
            yield event.plain_result(
                "图床服务尚未配置，请先在配置文件中完成图床配置哦。"
            )
            return

        try:
            yield event.plain_result(
                "⚠️ 正在执行覆盖到云端任务（将清理云端多余文件）..."
            )
            success = await self.img_sync.start_sync("overwrite_to_remote")
            if success:
                yield event.plain_result(
                    "覆盖到云端任务已完成！云端现在与本地完全一致。"
                )
            else:
                yield event.plain_result("任务失败，请查看日志。")
        except Exception as e:
            logger.error(f"覆盖到云端失败: {str(e)}")
            yield event.plain_result(f"覆盖到云端失败: {str(e)}")

    async def _overwrite_from_remote_impl(self, event: AstrMessageEvent):
        """让本地完全和云端一致（会删除本地多出的图）。"""
        if not self.img_sync:
            yield event.plain_result(
                "图床服务尚未配置，请先在配置文件中完成图床配置哦。"
            )
            return

        try:
            yield event.plain_result(
                "⚠️ 正在执行从云端覆盖任务（将清理本地多余文件）..."
            )
            success = await self.img_sync.start_sync("overwrite_from_remote")
            if success:
                yield event.plain_result(
                    "从云端覆盖任务已完成！本地现在与云端完全一致。"
                )
            else:
                yield event.plain_result("任务失败，请查看日志。")
        except Exception as e:
            logger.error(f"从云端覆盖失败: {str(e)}")
            yield event.plain_result(f"从云端覆盖失败: {str(e)}")


    # ── 同步状态报告辅助方法 ──────────────────────────────

    def _get_storage_info(self) -> str:
        """获取存储类型描述。"""
        if hasattr(self.img_sync.provider, "bucket_name"):
            return f"存储桶: {self.img_sync.provider.bucket_name}"
        if hasattr(self.img_sync.provider, "album_id"):
            return f"相册ID: {self.img_sync.provider.album_id}"
        return "未知存储类型"

    @staticmethod
    def _count_by_category(file_list: list) -> dict:
        stats = {}
        for f in file_list:
            cat = f.get("category", "未分类")
            stats[cat] = stats.get(cat, 0) + 1
        return stats

    @staticmethod
    def _build_category_section(title: str, stats: dict) -> list:
        if not stats:
            return []
        lines = [title]
        for cat, count in sorted(stats.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  • {cat}: {count} 个")
        lines.append("")
        return lines

    def _build_category_stats(self, to_upload: list, to_download: list) -> list:
        lines = []
        upload_stats = self._count_by_category(to_upload)
        if upload_stats:
            lines.extend(self._build_category_section("📤 待上传文件分类:", upload_stats))
        download_stats = self._count_by_category(to_download)
        if download_stats:
            lines.extend(self._build_category_section("📥 待下载文件分类:", download_stats))
        return lines

    @staticmethod
    def _build_file_example_section(title: str, files: list) -> list:
        if not files:
            return []
        lines = [title]
        for f in files[:5]:
            lines.append(f"  • {f.get('category', '未分类')}/{f['filename']}")
        if len(files) > 5:
            lines.append(f"  • ...还有 {len(files) - 5} 个文件")
        lines.append("")
        return lines

    def _build_file_examples(self, to_upload: list, to_download: list) -> list:
        lines = []
        lines.extend(self._build_file_example_section("📤 待上传文件示例（前5个）:", to_upload))
        lines.extend(self._build_file_example_section("📥 待下载文件示例（前5个）:", to_download))
        return lines

    def _build_synced_detail(self) -> list:
        """构建已同步的云端+本地详细统计。"""
        lines = ["", "📋 详细信息:", ""]
        # 云端
        try:
            if hasattr(self.img_sync.provider, "get_image_list"):
                remote_images = self.img_sync.provider.get_image_list()
                remote_stats = self._count_by_category(remote_images)
                if remote_stats:
                    lines.append("📂 云端文件分类详情:")
                    for cat, count in sorted(remote_stats.items(), key=lambda x: x[1], reverse=True):
                        lines.append(f"  • {cat}: {count} 个")
                    lines.append(f"📊 云端总计: {len(remote_images)} 个文件")
                else:
                    lines.append("📂 云端无文件")
        except Exception as e:
            lines.append(f"⚠️ 获取云端详情失败: {str(e)}")
        # 本地
        local_total = 0
        local_stats = {}
        if os.path.exists(MEMES_DIR):
            for category in os.listdir(MEMES_DIR):
                cp = os.path.join(MEMES_DIR, category)
                if os.path.isdir(cp):
                    images = [f for f in os.listdir(cp)
                              if f.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp"))]
                    if images:
                        local_stats[category] = len(images)
                        local_total += len(images)
        lines.append("")
        if local_stats:
            lines.append("📂 本地文件分类详情:")
            for cat, count in sorted(local_stats.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"  • {cat}: {count} 个")
            lines.append(f"📊 本地总计: {local_total} 个文件")
        else:
            lines.append("📂 本地无文件")
        return lines

    def _append_upload_tracker_info(self, result: list):
        if (hasattr(self.img_sync.sync_manager, "upload_tracker")
                and self.img_sync.sync_manager.upload_tracker):
            try:
                if hasattr(self.img_sync.sync_manager.upload_tracker, "get_uploaded_files"):
                    uploaded = self.img_sync.sync_manager.upload_tracker.get_uploaded_files()
                    result.append("")
                    result.append(f"📝 上传记录: 已记录 {len(uploaded)} 个文件")
            except Exception:
                pass

    async def _download_images(self, images: list, save_dir: str) -> list:
        """批量下载图片到指定目录，返回保存的文件名列表。"""
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        ext_mapping = {"jpeg": ".jpg", "png": ".png", "gif": ".gif", "webp": ".webp"}
        saved = []

        for idx, img in enumerate(images, 1):
            timestamp = int(time.time())
            try:
                content = await self._fetch_image_content(img.url, ssl_context)
                file_type = self._detect_image_format(content)
                ext = ext_mapping.get(file_type, ".bin")
                filename = f"{timestamp}_{idx}{ext}"
                save_path = os.path.join(save_dir, filename)
                with open(save_path, "wb") as f:
                    f.write(content)
                saved.append(filename)
            except Exception as e:
                logger.error(f"下载图片失败: {str(e)}")

        return saved

    async def _fetch_image_content(self, url: str, ssl_context) -> bytes:
        """下载单张图片的原始字节。"""
        if "multimedia.nt.qq.com.cn" in url:
            insecure_url = url.replace("https://", "http://", 1)
            async with aiohttp.ClientSession() as session:
                async with session.get(insecure_url) as resp:
                    return await resp.read()
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=ssl_context)
        ) as session:
            async with session.get(url) as resp:
                return await resp.read()

    @staticmethod
    def _detect_image_format(content: bytes) -> str:
        """检测图片格式，返回小写扩展名（如 png/jpeg/webp）。"""
        try:
            with PILImage.open(io.BytesIO(content)) as img_obj:
                return img_obj.format.lower()
        except Exception:
            return "unknown"
