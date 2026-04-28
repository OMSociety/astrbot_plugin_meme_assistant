"""WebUI lifecycle management mixin for MemeSender.

Handles WebUI config init, auto-start, graceful shutdown, cleanup,
and the ``stop_server`` admin command.
"""

import logging
import os
import secrets
from multiprocessing import Process

logger = logging.getLogger(__name__)


class WebUIMixin:
    # ── called by MemeSender.__init__ ──────────────────────────
    def _init_webui_config(self):
        self.webui_process: Process | None = None
        self.server_port: int = self.config.get("webui_port", 5000)
        self.webui_token: str = self.config.get("webui_token", "").strip()
        if not self.webui_token:
            self.webui_token = secrets.token_hex(16)
            logger.info(f"🔑 已自动生成 WebUI Token: {self.webui_token}")

    # ── auto-start ─────────────────────────────────────────────
    def _auto_start_webui(self):
        """插件加载时自动启动 WebUI，启动前强制释放端口"""
        from .webui import run_server

        max_retries = 3
        last_error = None
        for attempt in range(max_retries):
            try:
                if self.webui_process and self.webui_process.is_alive():
                    return
                self._kill_port_owner(self.server_port)
                config = {
                    "webui_port": self.server_port,
                    "webui_token": self.webui_token,
                    "img_sync": self.img_sync,
                    "category_manager": self.category_manager,
                    "description_manager": self.description_manager,
                }
                self.webui_process = Process(
                    target=run_server,
                    args=(config,),
                    daemon=True,
                )
                self.webui_process.start()
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
        """终止 WebUI 子进程，先 SIGTERM 再 SIGKILL 兜底"""
        if not self.webui_process:
            return
        self.webui_process.terminate()
        self.webui_process.join(timeout=5)
        if self.webui_process.is_alive():
            logger.warning("WebUI 进程未响应 SIGTERM，发送 SIGKILL")
            self.webui_process.kill()
            self.webui_process.join(timeout=3)
        logger.info("WebUI 进程已终止")

    async def _cleanup_webui(self):
        """清理 WebUI 进程引用"""
        self.server_port = None
        if self.webui_process:
            if self.webui_process.is_alive():
                self.webui_process.terminate()
                self.webui_process.join()
        self.webui_process = None

    async def check_webui_health(self) -> bool:
        """检查子进程健康状态，意外退出时自动重启"""
        if self.webui_process is None:
            return True
        if not self.webui_process.is_alive():
            logger.warning("⚠️ WebUI 子进程意外退出，自动重启中")
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
