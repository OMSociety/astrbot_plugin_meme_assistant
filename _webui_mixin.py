"""WebUI lifecycle management mixin for MemeSender.

Handles WebUI config init, auto-start, graceful shutdown, cleanup,
and the ``stop_server`` admin command.
"""

import asyncio
import logging
import os
import secrets

logger = logging.getLogger(__name__)


class WebUIMixin:
    # ── called by MemeSender.__init__ ──────────────────────────
    def _init_webui_config(self):
        self.webui_task: asyncio.Task | None = None
        self._webui_stop_event: asyncio.Event | None = None
        self.server_port: int = self.config.get("webui_port", 5000)
        self.webui_token: str = self.config.get("webui_token", "").strip()
        if not self.webui_token:
            self.webui_token = secrets.token_hex(16)
            logger.info(f"🔑 已自动生成 WebUI Token: {self.webui_token}")

    # ── auto-start ─────────────────────────────────────────────
    def _auto_start_webui(self):
        """插件加载时自动启动 WebUI（主事件循环 asyncio.create_task，参考代码执行器实现）"""
        from .webui import start_server

        max_retries = 3
        last_error = None
        for attempt in range(max_retries):
            try:
                if self.webui_task and not self.webui_task.done():
                    return
                self._kill_port_owner(self.server_port)
                config = {
                    "webui_port": self.server_port,
                    "webui_token": self.webui_token,
                    "img_sync": self.img_sync,
                    "category_manager": self.category_manager,
                    "description_manager": self.description_manager,
                    "meme_manager": self,
                }
                self._webui_stop_event = asyncio.Event()
                self.webui_task = asyncio.create_task(start_server(config))
                logger.info(
                    f"🌐 WebUI 已自动启动: http://localhost:{self.server_port}\n"
                    f"   Token: {self.webui_token}"
                )
                return
            except Exception as e:
                last_error = e
                logger.error(f"❌ WebUI 自动启动失败 (attempt {attempt + 1}): {e}")
        if last_error:
            raise RuntimeError(
                f"WebUI 启动失败，已重试 {max_retries} 次"
            ) from last_error

    # ── shutdown / cleanup ─────────────────────────────────────
    async def _shutdown_webui(self):
        """终止 WebUI 任务，设置停止信号并清理引用"""
        if self._webui_stop_event:
            self._webui_stop_event.set()
        if self.webui_task and not self.webui_task.done():
            self.webui_task.cancel()
            try:
                await asyncio.wait_for(self.webui_task, timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        self.webui_task = None
        self._webui_stop_event = None
        self.server_port = None
        logger.info("WebUI 服务已终止")

    async def check_webui_health(self) -> bool:
        """检查任务健康状态，意外退出时自动重启"""
        if self.webui_task is None:
            return True
        if self.webui_task.done():
            exc = self.webui_task.exception()
            if exc:
                logger.warning(f"⚠️ WebUI 任务异常退出: {exc}")
            else:
                logger.warning("⚠️ WebUI 任务意外退出，自动重启中")
            await self._shutdown_webui()
            self._auto_start_webui()
            return False
        return True

    # ── port killer ────────────────────────────────────────────
    @staticmethod
    def _kill_port_owner(port: int) -> None:
        """释放指定端口占用（socket 预检 + fuser 两段式）"""
        import socket
        import subprocess

        # ① socket 预检：端口空闲则直接返回
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            sock.close()
            return
        except OSError:
            sock.close()

        # ② fuser -k 杀占用进程
        try:
            result = subprocess.run(
                ["fuser", "-k", f"{port}/tcp"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                logger.info(f"🔪 fuser 已释放端口 {port}: {result.stdout.strip()}")
                return
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        logger.warning(f"⚠️ 无法释放端口 {port}，尝试继续启动")
