<div align="center">

<img src="logo.png" width="132" alt="ActiveReply logo" />

# 主动回复（ActiveReply）

**让 Bot 自己决定何时开口、@ 谁、引用哪条消息。**

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.0.0-4b8bf5)](https://docs.astrbot.app/dev/star/plugin-new.html)
[![Python](https://img.shields.io/badge/Python-%3E%3D3.10-3776ab)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-v1.0.1-12a37a)](./CHANGELOG.md)

</div>

---

## ⚠️ 重要设置（使用前必看）

不完成下面几项，很容易出现「不回复 / 不 @ / 引用不对 / 记忆不可用」：

| # | 必须做的事 | 在哪里改 | 为什么 |
|---|---|---|---|
| 1 | 关闭内置「群聊上下文感知」 | AstrBot 配置 `group_icl_enable` | 与本插件的群聊历史重复注入，token 翻倍 |
| 2 | 关闭内置主动回复 | AstrBot 配置 `provider_settings.active_reply.enable` | 否则一条消息可能回两遍 |
| 3 | 关闭内置引用回复 | AstrBot 配置 `reply_with_quote` | 内置会强制每条都引用，干扰「按需引用」 |
| 4 | 开启用户识别 | AstrBot 配置 `provider_settings.identifier` | 历史里的用户 ID、角色显示依赖它 |
| 5 | 打开「群聊回复增强 → React Pattern」与「智能回复」 | 插件配置 | **只有打开它，模型才会被提示去 @ / 引用**；插件另有兜底：模型自己模仿出的标签也会被解析，且群消息一律走非流式以避免标签漏出 |
| 6 | 配置一个 Embedding 提供商 | AstrBot 模型提供商 | 记忆写入/检索需要 |
| 7 | 填写联网搜索的 `provider_id` | 插件配置 → 联网搜索 | 该工具不会回退到会话模型 |
| 8 | 用管理员账号执行 `/执行回复` | AstrBot 管理员列表 | 该指令默认仅管理员可用 |

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
| 指令 | `/执行回复`：立即按当前方式执行一次回复或判定 |

## 快速开始

> 从 GitHub 安装（Release 版）：在 AstrBot 插件页选择「从 URL 安装」，填入
> `https://github.com/AethenNet/astrbot_plugin_active_reply/releases/latest/download/astrbot_plugin_active_reply.zip`


1. 下载本仓库（或 Release 里的 zip），把整个文件夹放进 AstrBot 的 `data/plugins/`；
2. 重启 AstrBot（或在 WebUI 插件页重载）；
3. 打开插件配置，按需开启「消息主动回复」「群聊上下文增强」「群聊回复增强」；
4. 想用 @ 与引用，请同时开启 **群聊回复增强 → React Pattern**；
5. 想用记忆功能，请先在 AstrBot 里配置一个 **Embedding 提供商**。

> 建议关闭 AstrBot 内置的群聊上下文感知与内置主动回复，避免重复注入、重复发言。

## 指令

| 指令 | 权限 | 说明 |
|---|---|---|
| `/执行回复` | 管理员 | 立即执行一次主动回复（概率模式）或模型判定（model_choice 模式），该指令消息不参与上下文 |

## 模型工具

| 工具 | 用途 |
|---|---|
| `grok_web_search(query)` | 联网检索 |
| `enhance_use_image(message_id, image_index)` | 按需查看群聊历史里的图片 |
| `enhance_memory_rag_write(...)` | 写入一条长期记忆 |
| `enhance_memory_rag_read(...)` | 按角色 / 时间 / 群范围检索记忆 |

## 插件页面

在 AstrBot「插件」页打开本插件详情，即可进入控制台：

- **状态**：六个状态卡（主动回复 / 上下文 / 智能回复 / 记忆条目 / 记忆 RAG / 联网搜索）、记忆统计、最近活跃的群；
- **群配置**：汉堡菜单里直接列出每个群，点开即可为单个群覆盖主动回复 / 上下文 / 回复增强设置（可一键恢复跟随全局）；
- **记忆 RAG**：关键词与群范围筛选、分页、详情、删除、清理。

## 文档

- 📘 **详细说明：[插件说明.md](./插件说明.md)** —— 运行逻辑、每次回复的判定过程、标签语法、配置项含义、常见问题。
- 📝 **更新日志：[CHANGELOG.md](./CHANGELOG.md)**

## 许可证

本项目使用 [MIT License](./LICENSE)。

## 灵感来源

本插件的功能设计与交互形态参考了社区插件
[`astrbot_plugin_astrbot_enhance_mode`](https://github.com/Axi404/astrbot_plugin_astrbot_enhance_mode)
（React 群聊上下文、主动回复、标签解析），在此基础上重新组织了配置结构与页面，并补充了按群独立配置、@ 目标校验等实现。感谢原作者的开源工作。
