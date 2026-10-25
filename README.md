<div align="center">

<img src="./logo.png" alt="ActiveReply Logo" width="128" height="128" />

# 主动回复（ActiveReply）

**astrbot_plugin_active_reply** · 基于 [astrbot_plugin_astrbot_enhance_mode](https://github.com/Axi404/astrbot_plugin_astrbot_enhance_mode) 重写的群聊主动回复与交互增强插件

`astrbot_version: ">=4.0.0"` · `category: 工具` · `v1.0.0`

让 Bot 不再只会被动回答——按概率或模型判定主动开口，带群聊上下文、智能引用、按需看图、联网搜索与长期记忆。

</div>

---

## ✨ 主要功能

| 功能 | 说明 | 默认 |
| --- | --- | --- |
| **主动回复** | 两种触发模式：`probability`（固定概率随机触发）与 `model_choice`（模型按人格判定是否回复） | 关闭 |
| **触发积累** | 连续未回复达到阈值时强制回复 / 强制判定一次（probability 模式直接回复，model_choice 模式触发一次判定） | 10 条 |
| **黑/白名单** | 按 QQ 群号控制主动回复生效范围 | 黑名单 |
| **群聊上下文增强** | 结构化记录群聊历史（用户 ID / 角色标签 / 消息 ID）并注入 LLM 上下文 | 开启* |
| **智能回复** | 解析 `<mention/>` 与 `<quote/>` 标签，自动 @ 用户、引用消息 | 开启* |
| **图片转述** | 历史默认记 `[Image]`，模型可调用工具按需转述图片并回填历史 | 关闭 |
| **联网搜索** | `grok_web_search` 工具，指定独立 provider 实时检索 | 关闭 |
| **记忆 RAG** | `enhance_memory_rag_write/read` 长期记忆，embedding 召回 + 时间/角色/群过滤 | 开启 |
| **插件指令** | `/执行回复` 立即强制执行一次回复或判定（仅管理员） | - |
| **PluginPages** | 群配置面板（汉堡菜单按群独立配置）+ 记忆 RAG 管理面板，支持深色模式 | - |

> \* 群聊上下文增强与智能回复需「群聊回复增强 → React Pattern」开启后生效；关闭 React Pattern 不影响主动回复本身。

## 🚀 快速上手

1. 将 `astrbot_plugin_active_reply` 文件夹放入 AstrBot `plugins/` 目录（或在 AstrBot 中通过商店/Git 安装），重启 AstrBot。
2. 在 AstrBot 插件管理中找到「主动回复（ActiveReply）」，点击配置。
3. 「消息主动回复 → 启用」设为开启；保持「群聊回复增强 → React Pattern」开启可获得完整体验。
4. 建议关闭 AstrBot 内置的主动回复与群聊上下文感知，避免重复触发/重复注入。

## 🛠 插件指令

- `/执行回复`（管理员）：立即按当前回复方式执行一次回复或判定，指令消息本身不参与触发积累。

## 📖 详细文档

完整运行逻辑、配置说明与各功能运行方式请阅读 [**插件说明.md**](./插件说明.md)。

## 📄 版本历史

见 [**CHANGELOG.md**](./CHANGELOG.md)。

## 📦 依赖

- `aiohttp>=3.8.0`（AstrBot 环境自带）

## 📃 License

MIT
