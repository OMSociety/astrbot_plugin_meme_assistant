import asyncio
import os
import secrets
import time

import hypercorn.asyncio
from hypercorn.config import Config
from quart import (
    Quart,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from astrbot.api import logger

from .backend.api import api
from .config import MEMES_DIR

_SERVER_START_TIME = time.time()


class ServerState:
    _instance = None

    def __new__(cls):
        if not cls._instance:
            cls._instance = super().__new__(cls)
            cls._instance.ready = asyncio.Event()
        return cls._instance

    @classmethod
    def reset(cls):
        """重置单例状态，供 restart 场景使用"""
        cls._instance = None


app = Quart(__name__)

app.register_blueprint(api, url_prefix="/api")


@app.route("/api/version", methods=["GET"])
async def api_version():
    """返回复合版本号，前端轮询此接口检测是否需要刷新"""
    meme_mtime = 0
    if os.path.exists(MEMES_DIR):
        meme_mtime = os.path.getmtime(MEMES_DIR)
    return jsonify({
        "version": f"{_SERVER_START_TIME:.0f}_{meme_mtime:.0f}",
        "server_start": _SERVER_START_TIME,
    })


WEBUI_TOKEN = None
_current_server = None


@app.route("/health", methods=["GET"])
async def health_check():
    return jsonify({"status": "running", "version": "1.0"})


@app.before_request
async def require_login():
    allowed_endpoints = ["login", "static"]
    if request.endpoint in allowed_endpoints:
        return
    token = request.cookies.get("meme_token")
    if token != WEBUI_TOKEN:
        return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
async def login():
    token = request.cookies.get("meme_token")
    if token == WEBUI_TOKEN:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        form_data = await request.form
        key = form_data.get("key")
        if key == WEBUI_TOKEN:
            resp = await make_response(redirect(url_for("index")))
            resp.set_cookie(
                "meme_token", WEBUI_TOKEN, httponly=True, max_age=60 * 60 * 24 * 30
            )
            return resp
        else:
            error = "密钥错误，请重试。"
    return await render_template("login.html", error=error)


@app.route("/sw.js")
async def service_worker():
    """P3-2: Service Worker 离线包 — 必须从根路径提供以控制全局作用域"""
    from quart import make_response as q_make_response

    resp = await q_make_response(await send_from_directory("static/js", "sw.js"))
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Content-Type"] = "application/javascript"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/")
async def index():
    token = request.cookies.get("meme_token")
    if token != WEBUI_TOKEN:
        return redirect(url_for("login"))
    return await render_template("index.html")


@app.route("/memes/<category>/<filename>")
async def serve_emoji(category, filename):
    category_path = os.path.join(MEMES_DIR, category)
    file_path = os.path.join(category_path, filename)
    if os.path.exists(file_path):
        response = await send_from_directory(category_path, filename)
        # 强缓存 1 天，浏览器二次加载直接走 disk cache
        response.headers["Cache-Control"] = "public, max-age=86400"
        return response
    else:
        return "File not found: " + file_path, 404




async def start_server(config=None):
    global WEBUI_TOKEN, _current_server

    # ── 线程方式运行，无需 reset event loop policy ──
    ServerState.reset()  # 重置旧单例状态
    state = ServerState()
    state.ready.clear()

    port = config.get("webui_port", 5000)
    state.port = port
    WEBUI_TOKEN = config.get("webui_token", "").strip()
    if not WEBUI_TOKEN:
        WEBUI_TOKEN = secrets.token_hex(16)

    app.config["PLUGIN_CONFIG"] = {
        "img_sync": config.get("img_sync"),
        "category_manager": config.get("category_manager"),
        "description_manager": config.get("description_manager"),
        "meme_manager": config.get("meme_manager"),
        "webui_port": port,
    }

    @app.before_serving
    async def notify_ready():
        state.ready.set()

    hypercorn_config = Config()
    hypercorn_config.bind = [f"0.0.0.0:{port}"]
    hypercorn_config.graceful_timeout = 5

    _current_server = await hypercorn.asyncio.serve(
        app,
        hypercorn_config,
    )
    return WEBUI_TOKEN
