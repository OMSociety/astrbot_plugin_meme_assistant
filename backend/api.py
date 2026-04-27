import logging
import os

from quart import Blueprint, current_app, jsonify, request

from ..config import MEME_IDENTIFY_QUEUE_PATH, MEMES_DIR
from .models import (
    DuplicateEmojiError,
    add_emoji_to_category,
    batch_copy_emojis,
    batch_delete_emojis,
    batch_move_emojis,
    clear_all_emojis,
    clear_category_emojis,
    delete_emoji_from_category,
    get_emoji_by_category,
    move_emoji_to_category,
    scan_emoji_folder,
)

api = Blueprint("api", __name__)

logger = logging.getLogger(__name__)


def _get_provider_label(img_sync) -> str:
    """返回当前图床 provider 的展示名称。"""
    provider_type = getattr(img_sync, "provider_type", "")
    if provider_type == "stardots":
        return "StarDots"

    provider = getattr(img_sync, "provider", None)
    if provider is not None:
        return provider.__class__.__name__
    return "未知图床"


@api.route("/emoji", methods=["GET"])
async def get_all_emojis():
    """获取所有表情包（按类别分组）"""
    emoji_data = await scan_emoji_folder()
    for category in emoji_data:
        if not isinstance(emoji_data[category], list):
            emoji_data[category] = []
    return jsonify(emoji_data)


@api.route("/emoji/<category>", methods=["GET"])
async def get_emojis_by_category(category):
    """获取指定类别的表情包"""
    emojis = get_emoji_by_category(category)
    if emojis is None:
        return jsonify({"message": "Category not found"}), 404
    return jsonify(emojis if isinstance(emojis, list) else []), 200


@api.route("/emoji/add", methods=["POST"])
async def add_emoji():
    """添加表情包到指定类别"""
    try:
        # 检查是否有文件 - 使用 await 获取请求文件
        files = await request.files
        if not files or "image_file" not in files:
            return jsonify({"message": "没有找到上传的图片文件"}), 400

        image_file = files["image_file"]

        # 使用 await 获取表单数据
        form = await request.form
        category = form.get("category")

        if not category:
            return jsonify({"message": "没有指定类别"}), 400

        if not image_file or not image_file.filename:
            return jsonify({"message": "无效的图片文件"}), 400

        # 记录上传信息
        logger.info(f"收到上传请求: 类别={category}, 文件名={image_file.filename}")

        try:
            result = add_emoji_to_category(category, image_file)

            # 添加成功后同步配置
            plugin_config = current_app.config.get("PLUGIN_CONFIG", {})
            category_manager = plugin_config.get("category_manager")
            if category_manager:
                category_manager.sync_with_filesystem()

            logger.info(f"表情包添加成功: {result['path']}")
            return jsonify(
                {
                    "message": "表情包添加成功",
                    "path": result["path"],
                    "category": category,
                    "filename": result["filename"],
                }
            ), 201

        except DuplicateEmojiError as inner_e:
            logger.info(f"跳过重复表情包: {inner_e}")
            return (
                jsonify(
                    {
                        "message": str(inner_e),
                        "code": "duplicate_emoji",
                        "category": category,
                        "filename": inner_e.existing_filename,
                    }
                ),
                409,
            )
        except Exception as inner_e:
            logger.error(f"处理上传文件时出错: {inner_e}", exc_info=True)
            return jsonify({"message": f"处理上传文件时出错: {str(inner_e)}"}), 500

    except Exception as e:
        logger.error(f"处理上传请求时发生未知异常: {e}", exc_info=True)
        return jsonify({"message": f"处理上传请求时发生未知异常: {str(e)}"}), 500


@api.route("/emoji/delete", methods=["POST"])
async def delete_emoji():
    """删除指定类别的表情包"""
    data = await request.get_json()
    category = data.get("category")
    image_file = data.get("image_file")
    if not category or not image_file:
        return jsonify({"message": "Category and image file are required"}), 400

    if delete_emoji_from_category(category, image_file):
        return jsonify(
            {
                "message": "Emoji deleted successfully",
                "category": category,
                "filename": image_file,
            }
        ), 200
    else:
        return jsonify({"message": "Emoji not found"}), 404


@api.route("/emoji/batch_delete", methods=["POST"])
async def batch_delete_emoji():
    """批量删除指定类别的表情包"""
    data = await request.get_json()
    category = data.get("category")
    image_files = data.get("image_files")

    if not category or not isinstance(image_files, list) or not image_files:
        return jsonify({"message": "Category and image_files are required"}), 400

    result = batch_delete_emojis(category, image_files)
    if not result["category_exists"]:
        return jsonify({"message": "Category not found"}), 404

    deleted_files = result["deleted_files"]
    missing_files = result["missing_files"]
    return jsonify(
        {
            "message": "Batch delete completed",
            "category": category,
            "deleted_files": deleted_files,
            "missing_files": missing_files,
            "deleted_count": len(deleted_files),
            "missing_count": len(missing_files),
        }
    ), 200


@api.route("/emoji/move", methods=["POST"])
async def move_emoji():
    """移动单个表情包到指定类别。"""
    data = await request.get_json()
    source_category = data.get("source_category")
    target_category = data.get("target_category")
    image_file = data.get("image_file")

    if not source_category or not target_category or not image_file:
        return (
            jsonify(
                {
                    "message": "source_category, target_category and image_file are required"
                }
            ),
            400,
        )

    if source_category == target_category:
        return jsonify({"message": "Source and target category must be different"}), 400

    result = move_emoji_to_category(source_category, image_file, target_category)
    if not result["source_category_exists"]:
        return jsonify({"message": "Source category not found"}), 404
    if result["conflict"]:
        return jsonify({"message": "Target file already exists"}), 409
    if result["missing"]:
        return jsonify({"message": "Emoji not found"}), 404

    return jsonify(
        {
            "message": "Emoji moved successfully",
            "source_category": result["source_category"],
            "target_category": result["target_category"],
            "filename": result["filename"],
        }
    ), 200


@api.route("/emoji/batch_move", methods=["POST"])
async def batch_move_emoji():
    """批量移动指定类别的表情包到另一个类别。"""
    data = await request.get_json()
    source_category = data.get("source_category")
    target_category = data.get("target_category")
    image_files = data.get("image_files")

    if (
        not source_category
        or not target_category
        or not isinstance(image_files, list)
        or not image_files
    ):
        return (
            jsonify(
                {
                    "message": "source_category, target_category and image_files are required"
                }
            ),
            400,
        )

    if source_category == target_category:
        return jsonify({"message": "Source and target category must be different"}), 400

    result = batch_move_emojis(source_category, image_files, target_category)
    if not result["source_category_exists"]:
        return jsonify({"message": "Source category not found"}), 404

    moved_files = result["moved_files"]
    missing_files = result["missing_files"]
    conflicting_files = result["conflicting_files"]
    return jsonify(
        {
            "message": "Batch move completed",
            "source_category": source_category,
            "target_category": target_category,
            "moved_files": moved_files,
            "missing_files": missing_files,
            "conflicting_files": conflicting_files,
            "moved_count": len(moved_files),
            "missing_count": len(missing_files),
            "conflict_count": len(conflicting_files),
        }
    ), 200


@api.route("/emoji/batch_copy", methods=["POST"])
async def batch_copy_emoji():
    """批量复制指定类别的表情包到另一个类别。"""
    data = await request.get_json()
    source_category = data.get("source_category")
    target_category = data.get("target_category")
    image_files = data.get("image_files")

    if (
        not source_category
        or not target_category
        or not isinstance(image_files, list)
        or not image_files
    ):
        return (
            jsonify(
                {
                    "message": "source_category, target_category and image_files are required"
                }
            ),
            400,
        )

    result = batch_copy_emojis(source_category, image_files, target_category)
    if not result["source_category_exists"]:
        return jsonify({"message": "Source category not found"}), 404

    copied_files = result["copied_files"]
    missing_files = result["missing_files"]
    conflicting_files = result["conflicting_files"]
    return jsonify(
        {
            "message": "Batch copy completed",
            "source_category": source_category,
            "target_category": target_category,
            "copied_files": copied_files,
            "missing_files": missing_files,
            "conflicting_files": conflicting_files,
            "copied_count": len(copied_files),
            "missing_count": len(missing_files),
            "conflict_count": len(conflicting_files),
        }
    ), 200


@api.route("/category/clear", methods=["POST"])
async def clear_category():
    """清空指定类别下的所有表情包，但保留类别和配置。"""
    data = await request.get_json()
    category = data.get("category")
    if not category:
        return jsonify({"message": "Category is required"}), 400

    result = clear_category_emojis(category)
    if not result["category_exists"]:
        return jsonify({"message": "Category not found"}), 404

    deleted_files = result["deleted_files"]
    return jsonify(
        {
            "message": "Category cleared successfully",
            "category": category,
            "deleted_files": deleted_files,
            "deleted_count": len(deleted_files),
        }
    ), 200


@api.route("/emoji/clear_all", methods=["POST"])
async def clear_all_emoji():
    """清空所有类别中的表情包，但保留类别和配置。"""
    result = clear_all_emojis()
    deleted_by_category = result["deleted_by_category"]
    deleted_count = sum(deleted_by_category.values())
    return jsonify(
        {
            "message": "All emojis cleared successfully",
            "deleted_by_category": deleted_by_category,
            "deleted_count": deleted_count,
            "affected_categories": len(deleted_by_category),
        }
    ), 200


@api.route("/emotions", methods=["GET"])
async def get_emotions():
    """获取表情包类别描述"""
    try:
        plugin_config = current_app.config.get("PLUGIN_CONFIG", {})
        category_manager = plugin_config.get("category_manager")
        descriptions = category_manager.get_descriptions()
        return jsonify(descriptions)
    except Exception as e:
        current_app.logger.error(f"获取标签描述失败: {e}")
        return jsonify({"error": "获取标签描述失败"}), 500


@api.route("/category/delete", methods=["POST"])
async def delete_category():
    """删除表情包类别"""
    try:
        data = await request.get_json()

        category = data.get("category")
        if not category:
            return jsonify({"message": "Category is required"}), 400

        plugin_config = current_app.config.get("PLUGIN_CONFIG", {})
        category_manager = plugin_config.get("category_manager")

        if not category_manager:
            return jsonify({"message": "Category manager not found"}), 404

        if category_manager.delete_category(category):
            return jsonify({"message": "Category deleted successfully"}), 200
        else:
            return jsonify({"message": "Failed to delete category"}), 500
    except Exception as e:
        return jsonify({"message": f"Failed to delete category: {str(e)}"}), 500


@api.route("/sync/status", methods=["GET"])
async def get_sync_status():
    """获取同步状态"""
    try:
        plugin_config = current_app.config.get("PLUGIN_CONFIG", {})
        category_manager = plugin_config.get("category_manager")

        if not category_manager:
            raise ValueError("未找到类别管理器")

        logger.info("获取同步状态...")
        missing_in_config, deleted_categories = category_manager.get_sync_status()

        return jsonify(
            {
                "status": "ok",
                "missing_in_config": missing_in_config,
                "deleted_categories": deleted_categories,
                "differences": {
                    "missing_in_config": missing_in_config,
                    "deleted_categories": deleted_categories,
                },
            }
        )
    except Exception as e:
        logger.error(f"获取同步状态失败: {e}")
        return jsonify({"error": "获取同步状态失败"}), 500


@api.route("/sync/config", methods=["POST"])
async def sync_config():
    """同步配置与文件夹结构的 API 端点"""
    try:
        plugin_config = current_app.config.get("PLUGIN_CONFIG", {})
        category_manager = plugin_config.get("category_manager")

        if not category_manager:
            raise ValueError("未找到类别管理器")

        logger.info("开始同步配置...")
        if category_manager.sync_with_filesystem():
            logger.info("配置同步成功")
            return jsonify({"message": "配置同步成功"}), 200
        else:
            logger.warning("配置同步失败")
            return jsonify({"message": "配置同步失败"}), 500
    except Exception as e:
        logger.error(f"配置同步失败: {e}")
        return jsonify({"message": f"配置同步失败: {str(e)}"}), 500


@api.route("/category/update_description", methods=["POST"])
async def update_category_description():
    """更新类别的描述"""
    try:
        data = await request.get_json()
        category = data.get("tag")
        description = data.get("description")
        if not category or not description:
            return jsonify({"message": "Category and description are required"}), 400

        plugin_config = current_app.config.get("PLUGIN_CONFIG", {})
        category_manager = plugin_config.get("category_manager")

        if not category_manager:
            return jsonify({"message": "Category manager not found"}), 404

        if category_manager.update_description(category, description):
            # 返回更新后的类别和描述
            return jsonify({"category": category, "description": description}), 200
        else:
            return jsonify({"message": "Failed to update category description"}), 500
    except Exception as e:
        return jsonify(
            {"message": f"Failed to update category description: {str(e)}"}
        ), 500


@api.route("/category/restore", methods=["POST"])
async def restore_category():
    """恢复或创建新类别"""
    try:
        data = await request.get_json()

        category = data.get("category")
        description = data.get("description", "请添加描述")

        if not category:
            return jsonify({"message": "Category is required"}), 400

        plugin_config = current_app.config.get("PLUGIN_CONFIG", {})
        category_manager = plugin_config.get("category_manager")

        if not category_manager:
            return jsonify({"message": "Category manager not found"}), 404

        # 创建类别目录
        category_path = os.path.join(MEMES_DIR, category)
        os.makedirs(category_path, exist_ok=True)

        # 更新类别描述
        if category_manager.update_description(category, description):
            return jsonify(
                {"message": "Category created successfully", "description": description}
            ), 200
        else:
            return jsonify({"message": "Failed to create category"}), 500

    except Exception as e:
        return jsonify({"message": f"Failed to create category: {str(e)}"}), 500


@api.route("/category/rename", methods=["POST"])
async def rename_category():
    """重命名类别"""
    try:
        data = await request.get_json()
        old_name = data.get("old_name")
        new_name = data.get("new_name")
        if not old_name or not new_name:
            return jsonify({"message": "Old and new category names are required"}), 400

        plugin_config = current_app.config.get("PLUGIN_CONFIG", {})
        category_manager = plugin_config.get("category_manager")

        if not category_manager:
            return jsonify({"message": "Category manager not found"}), 404

        if category_manager.rename_category(old_name, new_name):
            return jsonify({"message": "Category renamed successfully"}), 200
        else:
            return jsonify({"message": "Failed to rename category"}), 500
    except Exception as e:
        return jsonify({"message": f"Failed to rename category: {str(e)}"}), 500


@api.route("/img_host/sync/status", methods=["GET"])
async def get_img_host_sync_status():
    """获取同步状态"""
    try:
        plugin_config = current_app.config.get("PLUGIN_CONFIG", {})
        img_sync = plugin_config.get("img_sync")
        if not img_sync:
            return jsonify({"error": "图床服务未配置"}), 400

        status = img_sync.check_status()
        status["upload_count"] = len(status.get("to_upload", []))
        status["download_count"] = len(status.get("to_download", []))
        status["provider_label"] = _get_provider_label(img_sync)
        return jsonify(status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api.route("/img_host/sync/upload", methods=["POST"])
async def sync_to_remote():
    """同步到云端"""
    try:
        plugin_config = current_app.config.get("PLUGIN_CONFIG", {})
        img_sync = plugin_config.get("img_sync")
        if not img_sync:
            return jsonify({"message": "图床服务未配置"}), 400

        img_sync.sync_process = img_sync._start_sync_process("upload")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"message": str(e)}), 500


@api.route("/img_host/sync/download", methods=["POST"])
async def sync_from_remote():
    """从云端同步"""
    try:
        plugin_config = current_app.config.get("PLUGIN_CONFIG", {})
        img_sync = plugin_config.get("img_sync")
        if not img_sync:
            return jsonify({"message": "图床服务未配置"}), 400

        img_sync.sync_process = img_sync._start_sync_process("download")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"message": str(e)}), 500


# ── 表情包描述管理 API ──────────────────────────────────────────


def _get_description_manager():
    """从 app config 获取 description_manager"""
    plugin_config = current_app.config.get("PLUGIN_CONFIG", {})
    return plugin_config.get("description_manager")


@api.route("/description/<category>/<filename>", methods=["GET"])
async def get_meme_description(category, filename):
    """获取单张表情包的描述"""
    dm = _get_description_manager()
    if not dm:
        return jsonify({"message": "Description manager not available"}), 503

    data = dm.get(category, filename)
    if not data:
        return jsonify({"message": "Description not found"}), 404
    return jsonify({"category": category, "filename": filename, **data})


@api.route("/description/stats", methods=["GET"])
async def get_description_stats():
    """获取表情包描述覆盖统计"""
    dm = _get_description_manager()
    if not dm:
        return jsonify({"message": "Description manager not available"}), 503
    return jsonify(dm.get_stats())


@api.route("/description/<category>/<filename>", methods=["PUT"])
async def update_meme_description(category, filename):
    """更新单张表情包的描述/标签（WebUI 编辑用）"""
    dm = _get_description_manager()
    if not dm:
        return jsonify({"message": "Description manager not available"}), 503

    data = await request.get_json()
    description = data.get("description")
    tags = data.get("tags")

    if description is not None:
        dm.update_description(category, filename, description)
    if tags is not None and isinstance(tags, list):
        dm.update_tags(category, filename, tags)

    return jsonify({"message": "Description updated"})


@api.route("/description/<category>/<filename>", methods=["DELETE"])
async def delete_meme_description(category, filename):
    """删除单张表情包的描述"""
    dm = _get_description_manager()
    if not dm:
        return jsonify({"message": "Description manager not available"}), 503

    if dm.delete(category, filename):
        return jsonify({"message": "Description deleted"})
    return jsonify({"message": "Description not found"}), 404


@api.route("/description/search", methods=["GET"])
async def search_meme_descriptions():
    """模糊搜索表情包描述"""
    dm = _get_description_manager()
    if not dm:
        return jsonify({"message": "Description manager not available"}), 503

    q = request.args.get("q", "").strip()
    category = request.args.get("category")
    limit = int(request.args.get("limit", 10))

    if not q:
        return jsonify({"message": "Query parameter 'q' is required"}), 400

    results = dm.search(query=q, category=category, limit=limit)
    return jsonify({"query": q, "results": results, "total": len(results)})


def _write_identify_queue(tasks: list[dict]) -> bool:
    """向识别队列追加任务（跨进程通信）"""
    import json as _json

    try:
        existing = []
        if MEME_IDENTIFY_QUEUE_PATH.exists():
            existing = _json.loads(
                MEME_IDENTIFY_QUEUE_PATH.read_text(encoding="utf-8") or "[]"
            )
        existing.extend(tasks)
        MEME_IDENTIFY_QUEUE_PATH.write_text(
            _json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return True
    except Exception as e:
        logger.error(f"写入识别队列失败: {e}")
        return False


@api.route("/description/identify", methods=["POST"])
async def trigger_identify():
    """触发 LLM 识别：将指定类别中未识别的文件写入队列"""
    dm = _get_description_manager()
    if not dm:
        return jsonify({"message": "Description manager not available"}), 503

    data = await request.get_json()
    category = data.get("category")
    if not category:
        return jsonify({"message": "Category is required"}), 400

    # 找出该类别中所有未识别的文件
    category_path = MEMES_DIR / category
    if not category_path.is_dir():
        return jsonify({"message": "Category not found", "category": category}), 404

    files = [f.name for f in category_path.iterdir() if f.is_file()]
    unidentified = [
        f
        for f in files
        if not dm.get(category, f)
        or dm.get(category, f).get("description", "") == "待识别"
    ]

    tasks = [
        {"action": "identify", "category": category, "filename": f}
        for f in unidentified
    ]
    if not tasks:
        return jsonify(
            {
                "message": "No unidentified files in this category",
                "category": category,
                "count": 0,
            }
        ), 200

    success = _write_identify_queue(tasks)
    return jsonify(
        {
            "message": f"Added {len(tasks)} files to identify queue"
            if success
            else "Failed to write queue",
            "category": category,
            "count": len(tasks),
        }
    ), 202 if success else 500


@api.route("/description/identify_all", methods=["POST"])
async def trigger_identify_all():
    """触发全量 LLM 识别：所有类别中未识别的文件写入队列"""
    dm = _get_description_manager()
    if not dm:
        return jsonify({"message": "Description manager not available"}), 503

    all_tasks = []
    for cat_dir in MEMES_DIR.iterdir():
        if not cat_dir.is_dir():
            continue
        category = cat_dir.name
        files = [f.name for f in cat_dir.iterdir() if f.is_file()]
        for f in files:
            entry = dm.get(category, f)
            if not entry or entry.get("description", "") == "待识别":
                all_tasks.append(
                    {"action": "identify", "category": category, "filename": f}
                )

    if not all_tasks:
        return jsonify({"message": "No unidentified files found", "count": 0}), 200

    success = _write_identify_queue(all_tasks)
    return jsonify(
        {
            "message": f"Added {len(all_tasks)} files to identify queue"
            if success
            else "Failed to write queue",
            "count": len(all_tasks),
        }
    ), 202 if success else 500


@api.route("/description/reidentify_all", methods=["POST"])
async def trigger_reidentify_all():
    """触发全量重新识别：清除所有描述并重新识别"""
    dm = _get_description_manager()
    if not dm:
        return jsonify({"message": "Description manager not available"}), 503

    # 搜集所有文件
    all_tasks = []
    for cat_dir in MEMES_DIR.iterdir():
        if not cat_dir.is_dir():
            continue
        category = cat_dir.name
        for f in cat_dir.iterdir():
            if f.is_file():
                all_tasks.append(
                    {"action": "reidentify", "category": category, "filename": f.name}
                )

    if not all_tasks:
        return jsonify({"message": "No files found", "count": 0}), 200

    success = _write_identify_queue(all_tasks)
    return jsonify(
        {
            "message": f"Added {len(all_tasks)} files to full re-identify queue"
            if success
            else "Failed to write queue",
            "count": len(all_tasks),
        }
    ), 202 if success else 500


@api.route("/img_host/sync/check_process", methods=["GET"])
async def check_sync_process():
    """检查同步进程状态"""
    try:
        plugin_config = current_app.config.get("PLUGIN_CONFIG", {})
        img_sync = plugin_config.get("img_sync")
        if not img_sync or not img_sync.sync_process:
            return jsonify({"completed": True, "success": True})

        if not img_sync.sync_process.is_alive():
            success = img_sync.sync_process.exitcode == 0
            img_sync.sync_process = None
            return jsonify({"completed": True, "success": success})

        return jsonify({"completed": False})
    except Exception as e:
        return jsonify({"message": str(e)}), 500
