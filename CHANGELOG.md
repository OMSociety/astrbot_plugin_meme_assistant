# 更新日志

## v1.1.0 (2026-04-28)

### 🧠 新增功能

- **逐表情 LLM 智能识别** — 上传或初始化表情包时，自动调用 Vision 模型（kimi_2.5 等）识别图片内容，生成中文描述与语义标签，存入 `meme_descriptions.json`
- **表情描述 WebUI 管理** — 管理后台新增逐表情描述面板：查看 LLM 识别结果、在线编辑描述/标签、删除描述
- **描述模糊搜索** — WebUI 支持按描述文本搜索表情，精准定位目标图片
- **批量识别与并发控制** — `/表情管理 识别 [类别]` 命令对指定类别批量识别，支持并发数配置
- **识别统计与覆盖率** — WebUI 展示各分类的已识别/未识别数量
- **`send_meme` LLM 工具增强** — prompt 注入中展示可用类别列表，LLM 可精确按类别发送

### 🐛 Bug 修复

- **WebUI 端口残留进程问题** — `_kill_port_owner` 新增自保护盾（`os.getpid()` 检查），精准杀旧子进程不伤主进程；`_auto_start_webui` 启动前自动释放端口，解决「改完代码刷新没变化」的顽固问题

### 🔧 优化

- 重构插件架构，新增 `backend/description_manager.py` 和 `backend/models.py` 模块
- WebUI API 新增 `/description/*` 路由族（GET/PUT/DELETE/search/stats）
- 配置项新增：`meme_identify_enabled`、`meme_identify_provider_id`、`meme_identify_on_upload`、`meme_identify_concurrency`

## v1.0.0 (2025-12)

- 重构自 [astrbot_plugin_meme_manager](https://github.com/anka-afk/astrbot_plugin_meme_manager) v3.20
- 保留全部原有功能：AI 智能发送、WebUI 管理、云端同步、分类管理
- 代码架构重组，文件名与导入路径规范化
- 新增作者标注：Slandre & Flandre
