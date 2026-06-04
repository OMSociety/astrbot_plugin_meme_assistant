# 🎭 表情包助手

[![Version](https://img.shields.io/badge/version-v1.1.0-blue.svg)](https://github.com/OMSociety/astrbot_plugin_meme_assistant)
[![AstrBot](https://img.shields.io/badge/AstrBot-%E2%89%A5v4-green.svg)](https://github.com/AstrBotDevs/AstrBot)
[![License](https://img.shields.io/badge/license-MIT-orange.svg)](LICENSE)

为 LLM 提供智能表情包发送能力，支持 AI 识别发送、WebUI 管理、云端同步、逐表情 LLM 描述存储。让 Bot 在群聊中精准、自然地使用表情包。

> 本项目由 AI 编写，v1.0.0 重构自 [astrbot_plugin_meme_manager](https://github.com/anka-afk/astrbot_plugin_meme_manager)。

[快速开始](#-快速开始) • [功能列表](#-功能列表) • [配置项](#-配置项说明) • [LLM 工具](#-llm-可调用工具) • [更新日志](CHANGELOG.md)

---

## 📖 功能概览

### 核心能力
- **🤖 AI 智能发送** — LLM 根据对话上下文自动选择表情标签（`&&happy&&`），回复中插入对应表情图片
- **🛠️ LLM 工具发送** — LLM 可通过 `send_meme` 工具按类别主动发送表情包，不依赖文本标记
- **🧠 表情智能识别** — 逐表情调用 LLM Vision 模型识别图片内容，自动生成描述与标签，存入本地 JSON
- **🔍 表情描述搜索** — 支持按描述模糊搜索表情，LLM 可基于语义精准选图而非仅按类别
- **🌐 WebUI 管理界面** — 拖拽管理、批量操作、分类编辑、描述查看/编辑/删除，提供完整管理后台
- **☁️ 云端同步** — 支持 Stardots 图床同步，多设备表情包保持一致
- **📊 灵活发送控制** — 概率控制、数量限制、重复检测、回复带图、流式兼容

### 智能特性
- 🏷️ **自动 Prompt 维护** — 所有类别描述根据表情包文件夹自动生成，无需手动维护
- 📝 **逐表情元数据** — 每张表情包独立存储 LLM 识别描述、标签、识别时间，可在 WebUI 编辑
- 🔄 **批量识别** — 上传表情后自动触发 LLM 识别，支持并发控制与增量识别
- 💬 **回复带图** — 文本与表情图片可在同一条消息中发送（需关闭分段回复）

---

## 🚀 快速开始

### 安装

**方式一：插件市场**
- AstrBot WebUI → 插件市场 → 搜索 `astrbot_plugin_meme_assistant`

**方式二：GitHub 仓库**
- AstrBot WebUI → 插件管理 → ＋ 安装
- 粘贴仓库地址：`https://github.com/OMSociety/astrbot_plugin_meme_assistant`

### 依赖安装
```bash
pip install -r requirements.txt
```
核心依赖：Pillow、aiohttp，基于 AstrBot 内置 SDK。

### 首次使用
1. 安装后重启 AstrBot，插件会自动导入一套默认表情包
2. 无需修改人格设置，无需在 Prompt 中手动添加表情提示
3. 需要更多自定义时，私聊 Bot 发送：`/表情管理 开启管理后台`

---

## 📋 功能列表

### AI 智能发送

| 功能 | 说明 |
|:----|:----|
| `&&label&&` 标记匹配 | LLM 在文本中插入 `&&happy&&` 等标签，插件自动替换为对应表情包图片 |
| 备用标记支持 | 支持 `[label]` `(label)` `:label:` 等替代标记，容错更强 |
| 宽松匹配 | 英文标签支持模糊匹配（angry → anger），中文场景强烈推荐开启 |
| 重复检测 | 自动检测并过滤 `angryangryangry` 等重复标签 |
| 高置信度白名单 | 指定标签即使在英文上下文中也可能被识别 |

### LLM 表情工具

| 工具 | 说明 |
|:----|:----|
| `send_meme` | LLM 可主动调用，按类别发送表情包图片，不依赖文本标记 |

### 智能识别（v1.1.0 新增）

| 功能 | 说明 |
|:----|:----|
| 逐表情 LLM 识别 | 上传/初始化表情包时自动调用 Vision 模型生成描述与标签 |
| 描述持久化 | 识别结果存入 `meme_descriptions.json`，重启不丢失 |
| WebUI 编辑 | 管理后台可直接查看/修改/删除每张表情的描述与标签 |
| 模糊搜索 | WebUI 支持按描述文本模糊搜索表情，精准定位 |
| 批量识别 | 支持并发批量识别指定类别的未识别表情 |
| 识别统计 | WebUI 展示各分类的识别覆盖率 |
| ⚡ 识别熔断保护 | 连续识别失败自动暂停，冷却后自动恢复，防止 API 异常时刷屏报错 |

### WebUI 管理

| 功能 | 说明 |
|:----|:----|
| 拖拽管理 | 长按 0.8 秒进入拖拽模式，支持跨分类移动、批量选择、复制粘贴 |
| 分类管理 | 创建/编辑/删除分类，实时生效 |
| 上传进度 | 上传时可见进度条与结果提示，同内容文件自动跳过 |
| 批量操作 | 批量删除、分类清空、全量清空（含二次确认） |
| 移动端适配 | 侧栏折叠、滚动适配，手机也能管理 |

### 命令管理

| 指令 | 说明 |
|:----|:----|
| `/表情管理 开启管理后台` | 🚀 启动 WebUI（仅私聊），重复执行返回访问地址 |
| `/表情管理 关闭管理后台` | 🔒 关闭 WebUI |
| `/表情管理 查看图库` | 📚 列出所有可用表情类别 |
| `/表情管理 添加表情 [类别]` | ➕ 上传表情到指定类别 |
| `/表情管理 恢复默认表情包 [类别]` | ♻️ 恢复内置默认表情包（可按类别） |
| `/表情管理 清空指定类型 [类别]` | ⚠️ 清空类别内表情（保留类别，需二次确认） |
| `/表情管理 清空全部` | ⚠️ 清空所有表情（保留类别结构，需二次确认） |
| `/表情管理 删除类型本身 [类别]` | ⚠️ 删除类别及下面所有表情（需二次确认） |
| `/表情管理 同步状态` | 🔄 查看云端同步状态 |
| `/表情管理 同步到云端` | ☁️ 上传本地表情到云端 |
| `/表情管理 从云端同步` | ⬇️ 从云端下载表情到本地 |
| `/表情管理 识别 [类别]` | 🧠 对指定类别执行 LLM 识别（v1.1.0 新增） |

### 云端同步

| 功能 | 说明 |
|:----|:----|
| Stardots 图床 | 国内访问友好，免费 2024 张 / 10GB 月流量 |
| 同步面板 | WebUI 展示图床服务商、云端图片数量、占用空间 |

---

## ⚙️ 配置项说明

### 基础设置

| 配置项 | 类型 | 默认值 | 说明 |
|:----|:----|:----|:----|
| `webui_port` | int | `5000` | WebUI 管理后台端口号 |
| `webui_token` | string | `""` | WebUI 登录 Token（留空自动生成随机 Token） |
| `convert_static_to_gif` | bool | `false` | 静态图转 GIF 发送（部分平台兼容） |
| `content_cleanup_rule` | string | `&&[a-zA-Z]*&&` | 内容过滤正则，移除文本中的标签残留 |

### 发送控制

| 配置项 | 类型 | 默认值 | 说明 |
|:----|:----|:----|:----|
| `max_emotions_per_message` | int | `2` | 每条消息最多表情数量 |
| `emotions_probability` | int | `50` | 表情触发概率（1-100，步长 10） |
| `strict_max_emotions_per_message` | bool | `true` | 严格限制，超出裁剪 |
| `enable_repeated_emotion_detection` | bool | `true` | 重复标签检测 |
| `high_confidence_emotions` | list | 19 个默认标签 | 高置信度标签白名单 |

### 表情匹配

| 配置项 | 类型 | 默认值 | 说明 |
|:----|:----|:----|:----|
| `enable_loose_emotion_matching` | bool | `true` | 宽松匹配，中文场景强烈推荐 |
| `enable_alternative_markup` | bool | `true` | 备用标记（`[label]` `(label)` `:label:`） |
| `remove_invalid_alternative_markup` | bool | `true` | 移除无效标记；遇括号被误删请关闭 |

### 回复带图

| 配置项 | 类型 | 默认值 | 说明 |
|:----|:----|:----|:----|
| `enable_mixed_message` | bool | `true` | 文本与图片同一条消息发送 |
| `mixed_message_probability` | int | `50` | 回复带图概率（1-100） |
| `streaming_compatibility` | bool | `false` | 流式传输兼容（图片作为独立消息） |

> ⚠️ 若 AstrBot 开启了「分段回复」，回复带图功能可能失效。

### 情感模型

| 配置项 | 类型 | 默认值 | 说明 |
|:----|:----|:----|:----|
| `emotion_llm_enabled` | bool | `false` | 启用独立情感模型判断表情标签 |
| `emotion_llm_provider_id` | string | `""` | 情感模型 Provider ID（留空使用对话模型） |

### LLM 表情工具

| 配置项 | 类型 | 默认值 | 说明 |
|:----|:----|:----|:----|
| `meme_llm_tool_enabled` | bool | `true` | LLM 可通过 `send_meme` 工具主动发送表情包 |

### 智能识别（v1.1.0 新增）

| 配置项 | 类型 | 默认值 | 说明 |
|:----|:----|:----|:----|
| `meme_identify_enabled` | bool | `true` | 启用逐表情 LLM 识别 |
| `meme_identify_provider_id` | string | `""` | 识别用 Vision 模型的 Provider ID（留空使用默认） |
| `meme_identify_on_upload` | bool | `true` | 上传表情时自动触发识别 |
| `meme_identify_concurrency` | int | `2` | 批量识别并发数 |
| `meme_identify_circuit_threshold` | int | `5` | 连续识别失败 N 次后触发熔断，设为 0 禁用 |
| `meme_identify_circuit_cooldown` | int | `300` | 熔断后冷却秒数，到期自动恢复识别 |

### Prompt 注入

| 配置项 | 说明 |
|:----|:----|
| `prompt_head` | 自动插入 Prompt 头（类别描述自动追加） |
| `prompt_tail_1` | Prompt 尾（发送控制说明） |
| `prompt_tail_2` | Prompt 尾（安全控制体系） |

### 图床配置

| 配置项 | 类型 | 默认值 | 说明 |
|:----|:----|:----|:----|
| `image_host` | enum | `stardots` | 图床服务商 |
| `image_host_config` | object | — | 图床 API Key / Secret / 空间名称 |

---

## 🛠️ LLM 可调用工具

### 表情发送

| 工具 | 参数 | 说明 |
|:----|:----|:----|
| `send_meme` | `category`（类别名，如 `happy`） | 从指定类别随机发送一张表情包 |

### 使用示例
```
用户：来个开心的表情包
LLM 调用：send_meme(category="happy") → 发送 happy 类别中的随机表情
```

```
用户：骂他
LLM 调用：send_meme(category="angry") → 发送 angry 类别中的随机表情
```

---

## 📝 使用示例

```
用户：太难了这也
Bot：确实不容易&&sigh&& 不过总会好起来的~
# 插件自动将 &&sigh&& 替换为 sigh 类别表情包图片
```

```
群友：发个猫猫表情
LLM 调用：send_meme(category="meow") → 在群里发送猫猫表情包
```

```
用户（私聊）：/表情管理 开启管理后台
Bot：已开启，地址：http://192.168.1.100:5000
    密钥：xxxxxx
```

---

## 🔒 权限说明

| 操作 | 权限要求 |
|:----|:----|
| 表情发送（自动） | 所有群成员可用 |
| `send_meme` 工具 | LLM 自动决策，无需权限 |
| WebUI 管理后台 | 仅私聊可开启，需访问密钥 |
| 图床同步 | 需配置 API Key |
| 危险命令（清空/删除） | 需 30 秒内二次确认 |

---

## 📁 文件结构

```
astrbot_plugin_meme_assistant/
├── main.py                           # 主逻辑、LLM 工具注册、插件入口
├── _messaging_mixin.py               # 消息处理与表情替换
├── _emotion_mixin.py                 # 独立情感模型判断
├── _command_manage.py                # 表情管理指令（增删查改恢复）
├── _command_upload.py                # 上传与图床同步指令
├── _identify_mixin.py                # LLM 表情识别控制
├── _meme_recommender.py              # 表情推荐引擎
├── _prompt_renderer.py               # Prompt 注入渲染
├── _webui_mixin.py                   # WebUI 生命周期管理
├── config.py                         # 配置常量与路径
├── init.py                           # 插件初始化（默认表情包导入）
├── utils.py                          # 工具函数（文件操作、I/O锁、默认表情恢复）
├── webui.py                          # Quart WebUI 服务入口（含 GET /api/version 自动刷新端点）
├── backend/                          # 后端管理模块
│   ├── __init__.py                   # 模块导出
│   ├── api.py                        # WebUI REST API（类别/描述/识别 CRUD）
│   ├── category_manager.py           # 类别级描述管理（memes_data.json）
│   ├── description_manager.py        # 逐表情 LLM 描述管理（meme_descriptions.json）
│   ├── models.py                     # 数据模型与工具函数
│   └── provider_registry.py          # 图床 Provider 注册中心
├── image_host/                       # 图床同步模块
│   ├── __init__.py
│   ├── img_sync.py                   # 同步入口
│   ├── config.json                   # 图床配置文件
│   ├── provider_registry.py          # Provider 注册中心
│   ├── core/                         # 核心同步逻辑
│   │   ├── __init__.py
│   │   ├── sync_manager.py
│   │   ├── file_handler.py
│   │   └── upload_tracker.py
│   ├── interfaces/                   # 图床接口定义
│   │   ├── __init__.py
│   │   └── image_host.py
│   └── providers/                    # 图床服务商
│       ├── __init__.py
│       ├── stardots_provider.py
│       ├── cos_provider.py
│       ├── oss_provider.py
│       ├── s3_provider.py
│       └── provider_template.py
├── providers/                        # 图床同步适配层
│   ├── __init__.py
│   ├── stardots_sync.py
│   ├── cos_sync.py
│   ├── oss_sync.py
│   └── s3_sync.py
├── memes/                            # 默认表情包资源
├── static/                           # WebUI 前端资源
│   └── js/
│       ├── script.js                 # 前端逻辑（含自动版本轮询）
│       └── sw.js                     # Service Worker（离线缓存）
├── templates/                        # WebUI 模板
│   ├── index.html                    # 管理后台主页
│   └── login.html                    # 登录页
├── tests/                            # 单元测试
│   └── test_core.py                  # 核心功能测试（15 个用例）
├── _conf_schema.json                 # 配置项 schema
├── metadata.yaml                     # 插件元信息
├── README.md                         # 本文档
└── CHANGELOG.md                      # 更新日志
```

---

## 🌐 环境要求

- **AstrBot** ≥ v4.0.0
- **Python** ≥ 3.10
- **适配平台**：aiocqhttp（OneBot V11）、gewechat
- **图床**（可选）：Stardots

---

## 📝 更新日志

> 📋 **[查看完整更新日志 →](CHANGELOG.md)**

---

## 🤝 贡献与反馈

如遇问题请在 [GitHub Issues](https://github.com/OMSociety/astrbot_plugin_meme_assistant/issues) 提交，欢迎 Pull Request！

---

## 📜 许可证

本项目采用 **MIT License** 开源协议。

---

## 👤 作者

**Slandre & Flandre** — [@OMSociety](https://github.com/OMSociety)
