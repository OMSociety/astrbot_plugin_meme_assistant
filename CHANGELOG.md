# 更新日志

## v1.1.0 (2026-04-28)

### 🧠 新增功能

- **逐表情 LLM 智能识别** — 上传或初始化表情包时，自动调用 Vision 模型识别图片内容，生成中文描述与语义标签，存入 `meme_descriptions.json`
- **表情描述 WebUI 管理** — 管理后台新增逐表情描述面板：查看 LLM 识别结果、在线编辑描述/标签、删除描述
- **描述模糊搜索** — WebUI 支持按描述文本搜索表情，精准定位目标图片
- **批量识别与并发控制** — `/表情管理 识别 [类别]` 命令对指定类别批量识别，支持并发数配置
- **识别统计与覆盖率** — WebUI 展示各分类的已识别/未识别数量
- **⚡ 识别熔断保护** — 连续识别失败达到阈值后自动暂停，冷却期后自动恢复。覆盖单张识别、批量识别、后台轮询三个入口，防止 API 异常时刷屏报错（新增 `meme_identify_circuit_threshold` / `meme_identify_circuit_cooldown` 配置项）
- **WebUI 自动刷新** — `GET /api/version` 返回复合版本号（进程启动时间 + 表情包目录修改时间），前端 JS 每 5 秒轮询，代码/表情包变更后自动刷新页面，告别改完代码手动 F5
- **WebUI 界面重组** — 侧边栏三合一为紧凑「状态概览」卡片（配置/图床/LLM 状态一目了然），「检查同步状态」整合为右上角刷新按钮，同步/清空按钮等归入工具栏，消除大量意义不明的散落按钮
- **🎨 WebUI 体验优化** — 拖拽长按时长从 3 秒缩短为 0.8 秒，上传提示文字动态读取时长；同步状态面板无差异时完全隐藏（含 `<hr>` 分隔线），消除空白行；各分类下的批量选择摘要仅在已开启时显示；标签筛选与目录间多余 `<hr>` 移除，利用 `#sidebar` 自带 `border-top` 分隔
- **`send_meme` LLM 工具增强** — prompt 注入中展示可用类别列表，LLM 可精确按类别发送

### 🐛 Bug 修复

- **WebUI 端口残留进程问题** — `_kill_port_owner` 新增自保护盾（`os.getpid()` 检查），精准杀旧子进程不伤主进程；`_auto_start_webui` 启动前自动释放端口，解决「改完代码刷新没变化」的顽固问题

### 🔧 架构重构

- **主逻辑拆分** — `main.py` 从 2000+ 行拆分为多个 Mixin 模块：`_messaging_mixin.py`（消息处理）、`_emotion_mixin.py`（情感模型）、`_command_manage.py`（管理指令）、`_command_upload.py`（上传同步指令）、`_identify_mixin.py`（表情识别）、`_webui_mixin.py`（WebUI 生命周期）、`_meme_recommender.py`（表情推荐引擎）、`_prompt_renderer.py`（Prompt 渲染）
- **`_emotion_mixin.py` 函数拆分** — 将 200+ 行的 `_extract_emotion_labels` monster 函数拆分为多个语义清晰的小函数（标签解析、优先级判定、白名单匹配等）
- **`utils.py` I/O 并发锁** — `restore_default_memes` 等文件操作增加 `asyncio.Lock` 防竞态，避免多消息并发时的文件冲突
- 重构插件架构，新增 `backend/description_manager.py` 和 `backend/models.py` 模块
- WebUI API 新增 `/description/*` 路由族（GET/PUT/DELETE/search/stats）
- 配置项新增：`meme_identify_enabled`、`meme_identify_provider_id`、`meme_identify_on_upload`、`meme_identify_concurrency`、`webui_token`

### 📐 代码质量

- **ruff 格式标准化** — 全面修复 142 处格式问题（133 处自动修复 + 16 处 E701 同行 yield + 2 处 F841 未用变量 + 1 处未用导入），最终 29/29 文件通过 ruff format 检查
- **注释规范完善** — 每个类、每个方法均补全 docstring，提升代码可读性
- **清理冗余文件** — 删除拆分过程中残留的 `main.py.bak` 备份文件
- **合规审查通过** — 确认仅使用 `aiohttp`/`httpx`（无 `requests`）、持久化数据存储于 `data/` 目录、全面的错误处理（100+ try/except）

### 🧪 测试

- **单元测试基础设施** — 新增 `tests/test_core.py`，覆盖配置常量、数据路径、标记解析、标签匹配、重复检测等核心逻辑，15 个测试用例全部通过

## v1.0.0 (2025-12)

- 重构自 [astrbot_plugin_meme_manager](https://github.com/anka-afk/astrbot_plugin_meme_manager) v3.20
- 保留全部原有功能：AI 智能发送、WebUI 管理、云端同步、分类管理
- 代码架构重组，文件名与导入路径规范化
- 新增作者标注：Slandre & Flandre
