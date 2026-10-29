<div align="center">

<img src="logo.png" width="132" alt="ActiveReply logo" />

# 主动回复（ActiveReply）

**让 Bot 自己决定何时开口、@ 谁、引用哪条消息。**

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.0.0-4b8bf5)](https://docs.astrbot.app/dev/star/plugin-new.html)
[![Python](https://img.shields.io/badge/Python-%3E%3D3.10-3776ab)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-v1.0.2-12a37a)](./CHANGELOG.md)

</div>

---

## 导航

[使用前检查](#使用前建议检查) · [功能概览](#主要功能) · [快速开始](#快速开始) ·
[指令与工具](#指令) · [插件页面](#插件页面) · [开发文档](#文档) · [项目结构](#项目结构)

## ⚠️ 使用前建议检查

### 建议先完成

1. 在 AstrBot 配置中关闭内置「群聊上下文感知」（`group_icl_enable`），避免重复注入历史。
2. 关闭 AstrBot 内置主动回复（`provider_settings.active_reply.enable`），避免 Bot 对同一消息回复两次。
3. 关闭 AstrBot 内置引用回复（`reply_with_quote`），让本插件按需决定是否引用。
4. 开启 AstrBot 用户识别（`provider_settings.identifier`），以便模型获得用户 ID 与角色提示。
5. 如需真实 @ 与引用，在插件配置中同时开启「群聊回复增强 → React Pattern」和「智能回复」。
6. 如需记忆 RAG，在 AstrBot 模型提供商中配置 Embedding 提供商。

> **联网搜索**需要在「联网搜索」配置中指定 `provider_id`；该工具不会自动改用当前会话模型。

### 管理员指令

`/执行回复` 和 `/replytest` 默认仅管理员可用。请先在 AstrBot 管理员列表中配置管理员，再使用这些指令。

---

## 这是什么

`astrbot_plugin_active_reply` 是 AstrBot 的群聊增强插件。它把「机器人只能被动回答」变成「机器人可以自己判断该不该说话」，并且在说话时能像真人一样：

- 在句子的任意位置 **@ 某个群成员**（一条消息可以 @ 多个人）；
- **引用**某条具体的群聊消息再回应；
- 不回复时保持安静，不刷屏。

此外还提供两个可选的模型工具：**联网搜索** 与 **长期记忆（RAG）**，以及一个可以直接在 AstrBot 面板里打开的插件页面（按群配置 + 记忆管理）。

## 主要功能

| 功能 | 说明 |
|---|---|
| 消息主动回复 | 概率触发或模型判定触发，累计 N 条消息后强制触发一次；支持黑/白名单 |
| 群聊上下文增强 | 以「昵称/QQ/时间/消息号」的格式记录群聊，注入到模型上下文 |
| 智能回复 | 解析 `<mention id="QQ号"/>` 与 `<quote id="消息号"/>`，转换成真实的 @ 与引用 |
| 图片转述 | 历史里先记 `[Image]`，模型需要时再调用工具看图并把描述回填历史 |
| 联网搜索 | `grok_web_search` 工具，按需检索实时信息 |
| 记忆 RAG | `enhance_memory_rag_write` / `enhance_memory_rag_read`，可跨群检索长期记忆 |
| 插件页面 | 汉堡菜单导航；每个群独立配置；记忆浏览/筛选/删除；自动跟随深浅色 |
| 指令 | `/执行回复`：立即按当前方式执行一次回复或判定，并附带一份执行日志（token 用量） |
| 回复调试 | `/replytest true|false`：开启后每次回复附带 token 用量与缓存命中率 |
| 自动记忆（方案 C） | 明确说「记住…」时关键词通道自动写入；后台模型按频率抽取偏好/身份/约定类记忆，可在 RAG 页面查看统计及控制模型抽取 |

## 快速开始

**从 GitHub Release 安装：**在 AstrBot 插件页选择「从 URL 安装」，填入：

```text
https://github.com/AethenNet/astrbot_plugin_active_reply/releases/latest/download/astrbot_plugin_active_reply.zip
```

**手动安装：**

1. 下载仓库或 Release 压缩包，将插件目录放入 AstrBot 的 `data/plugins/`。
2. 重启 AstrBot，或在 WebUI 插件页重新加载插件。
3. 打开插件配置，按需要启用主动回复、群聊上下文增强和群聊回复增强。
4. 如需记忆功能，先在 AstrBot 中配置 Embedding 提供商。

> 建议关闭 AstrBot 内置的群聊上下文感知与内置主动回复，避免重复注入、重复发言。

## 指令

| 指令 | 权限 | 说明 |
|---|---|---|
| `/执行回复` | 管理员 | 立即执行一次回复或模型判定。指令消息不参与上下文；执行后另发一份含执行日志和 token 用量的合并转发 |
| `/replytest true`、`/replytest false` | 管理员 | 开启/关闭本群回复调试。开启后每次回复附带 token 用量与缓存命中率；别名：`/回复调试 开`、`/回复调试 关` |

`/replytest` 也接受 `on/off`、`1/0`、`开启/关闭` 和 `启用/禁用`。调试开关仅保存在当前进程，重启后会关闭。

## 模型工具

| 工具 | 用途 |
|---|---|
| `grok_web_search(query)` | 联网检索 |
| `enhance_use_image(message_id, image_index)` | 按需查看群聊历史里的图片 |
| `enhance_memory_rag_write(...)` | 写入一条长期记忆 |
| `enhance_memory_rag_read(...)` | 按角色 / 时间 / 群范围检索记忆 |

## 插件页面

在 AstrBot「插件」页打开本插件详情，即可进入控制台：

- **状态**：查看主动回复、上下文、智能回复、记忆、RAG 和联网搜索状态，以及记忆统计和最近活跃群。
- **群配置**：为单群覆盖主动回复、上下文和回复增强设置；关闭独立配置即可恢复跟随全局。
- **记忆 RAG**：筛选、分页查看、删除记忆，查看自动记忆统计并运行存储自检。
- **全局设置**：通过配置表单修改全局选项；群级覆盖仍优先于全局值。

## 文档

- 📘 **开发文档：[开发文档.md](./开发文档.md)** —— 插件运行流程、功能实现、配置说明、数据存储与维护指南。
- 📝 **更新日志：[CHANGELOG.md](./CHANGELOG.md)**

## 项目结构

| 路径 | 职责 |
|---|---|
| `main.py` | AstrBot 插件入口、群消息事件流程、模型提示词注入、LLM 工具与网页接口注册 |
| `config_model.py` | 配置默认值、类型转换、范围校验，以及全局/群级配置合并 |
| `runtime_state.py` | 按会话来源保存的短期状态；采用 LRU 限制内存中的来源数量 |
| `group_store.py` | 将每群覆盖项保存到 `groups.json` |
| `memory_store.py` | 长期记忆的 SQLite 表结构、时间/作用域规范化、过滤与向量相似度检索 |
| `tags.py` | 解析模型输出的 @/引用标签，并转换成 AstrBot 消息组件 |
| `page_api.py`、`web_compat.py` | 插件控制台的后端接口及 AstrBot 新旧网页请求 API 兼容 |
| `_conf_schema.json` | 全局配置表单的描述、默认值、提示文字及条件显示规则 |
| `views/console/` | 插件控制台的 HTML、CSS、JavaScript 与图标资源 |
| `metadata.yaml` | AstrBot 插件元数据与版本信息 |

运行流程、存储边界、配置项和接口清单见[开发文档](./开发文档.md)。

## 致谢

- 界面图标来自 [Morphicons](https://github.com/guillermolg00/morphicons) 的 Lucide 图标集（MIT / Lucide ISC），菜单按钮使用 Morphicons 引擎做形变动画；
- 功能设计与交互形态参考了社区插件 [`astrbot_plugin_astrbot_enhance_mode`](https://github.com/Axi404/astrbot_plugin_astrbot_enhance_mode)，并在此基础上重新组织配置结构与页面，补充按群独立配置和 @ 目标校验。感谢原作者的开源工作。

## 许可证

本项目使用 [MIT License](./LICENSE)。
