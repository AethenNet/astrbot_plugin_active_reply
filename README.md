<div align="center">

<img src="logo.png" width="132" alt="ActiveReply logo" />

# 主动回复（ActiveReply）

**让 Bot 自己决定何时开口、@ 谁、引用哪条消息，并且真正记住群里的事。**

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.0.0-4b8bf5)](https://docs.astrbot.app/dev/star/plugin-new.html)
[![Python](https://img.shields.io/badge/Python-%3E%3D3.10-3776ab)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-v1.0.3-12a37a)](./CHANGELOG.md)

**支持平台：OneBot（aiocqhttp）** · **插件市场分类：工具、AI 增强**

</div>

---

## 导航

[使用前检查](#使用前检查) · [功能](#功能) · [指令](#指令) · [配置](#配置) ·
[插件页面](#插件页面) · [安装](#安装) · [项目结构](#项目结构) · [致谢](#致谢)

## ⚠️ 使用前检查

建议先完成以下设置，否则容易出现「不回复 / 不 @ / 重复回复 / 记忆不可用」：

1. **关闭 AstrBot 内置群聊上下文感知**（`group_icl_enable`），避免与本插件的历史重复注入；
2. **关闭 AstrBot 内置主动回复**（`provider_settings.active_reply.enable`），否则一条消息可能回两遍；
3. **关闭 AstrBot 内置引用回复**（`reply_with_quote`），让插件按需引用；
4. **开启 AstrBot 用户识别**（`provider_settings.identifier`），历史里的用户 ID 与角色显示依赖它；
5. 想用真实 @ 与引用：在插件配置里打开「群聊回复增强 → React Pattern」和「智能回复」；
6. 想用记忆 RAG：先在 AstrBot 里配置一个 Embedding 提供商；
7. 想用联网搜索：在插件「联网搜索」里填写专用 `provider_id`（不会回退到会话模型）。

## 功能

| 功能 | 说明 |
|---|---|
| 消息主动回复 | 概率触发或模型判定，累计 N 条消息后兜底回复；支持黑白名单 |
| 群聊上下文增强 | 以「昵称/QQ/时间/消息号」记录群聊，支持图片转述 |
| 智能回复 | 解析 `<mention id="..."/>`、`<quote id="..."/>`，转换为真实 @ 与引用 |
| 自动记忆 | 关键词快速通道 + 后台模型抽取，聊天中自动形成长期记忆 |
| 记忆 RAG | 长期记忆的写入与检索，支持按角色 / 时间 / 群范围过滤、去重与自检 |
| 用户屏蔽 | 全局黑名单 + 群级屏蔽，支持原因、时长与屏蔽提示；ID 支持字母数字；被屏蔽用户的消息不进入插件 |
| 联网搜索 | `grok_web_search` 工具，按需检索实时信息 |
| 图片理解 | `enhance_use_image` 按需查看历史图片并回填描述 |
| 回复调试 | `/replytest` 开启后，每次回复附带 token 用量、缓存命中率与本轮 AstrBot 日志 |
| 插件页面 | 状态、群配置、记忆 RAG、屏蔽设置、全局设置；深浅色自动跟随 |

## 指令

| 指令 | 权限 | 说明 |
|---|---|---|
| `/执行回复` | 管理员 | 立即执行一次主动回复或模型判定；结束后发送执行日志（合并转发，含 token 用量） |
| `/replytest true\|false` | 管理员 | 开启/关闭本群回复调试；别名 `/回复调试 开\|关`。开启后每次回复附带 token 用量与 AstrBot 日志 |
| `/ban <时间> <用户ID> <原因>` | 管理员 | 在本群屏蔽用户 |
| `/ban-all <时间> <用户ID> <原因>` | 管理员 | 全局屏蔽用户（含私聊） |
| `/remove <用户ID>`、`/pass <用户ID>` | 管理员 | 解除本群屏蔽 |
| `/remove-all <用户ID>`、`/pass-all <用户ID>` | 管理员 | 解除全部屏蔽（全局 + 群级） |

时间格式：`0` 表示永久，也可写 `1d2h`、`30m`、`2h`；纯数字按分钟计算。

示例：

```
/ban 0 123456789 机器人
/ban-all 7d 987654321 广告
/remove 123456789
/pass-all 987654321
```

## 配置

插件配置分为 6 个区块：

| 区块 | 关键项 |
|---|---|
| 消息主动回复 | 启用、回复方式（概率/模型判定）、概率、触发积累、黑白名单 |
| 群聊上下文增强 | 历史开关、消息上限、图片转述 |
| 群聊回复增强 | React Pattern、角色显示、智能回复、**用户屏蔽**（启用 / 黑名单列表 / 屏蔽提示） |
| 联网搜索 | 启用、专用提供商、请求模式、来源输出 |
| 记忆 RAG | 启用、Embedding 提供商、召回数量、自动记忆（频率/间隔/置信度/作用域） |
| 调用配置 | LRU 上限、超时时间 |

> 「用户屏蔽」作用于全局，默认包含 `2854196310`、`2854197266`、`3889239358`、`3889001246` 四个账号；用户 ID 支持英文字母与数字。群级屏蔽在插件页面单独维护，与全局名单合并生效。

## 插件页面

在 AstrBot 插件详情页打开本插件视图：

- **主题按钮**：抽屉底部「全局设置」前的纯图标按钮，循环切换「跟随系统 → 日间 → 夜间」，使用 Morphicons 形变动画，选择会保存在本地；
- **按钮反馈**：保存、刷新、恢复默认、筛选、清理、自检、分页、添加/删除等操作按钮点击后锁定 1 秒，图标形变为转圈加载；导航、Tab、主题按钮不触发；
- **状态**：开关状态、记忆统计、Embedding 可用性、最近有消息的群；
- **群配置**：每个群独立覆盖主动回复 / 上下文 / 回复增强；新增「用户屏蔽」分区，可单独屏蔽本群用户；
- **记忆 RAG**：统计、筛选、分页、详情、删除、自检；顶部「自动记忆」面板可查看开关、抽取模型、频率、置信度、作用域与来源统计；
- **屏蔽设置**：添加屏蔽（用户 ID、原因、范围、时间）位于页面顶部；全局列表支持按 ID / 原因搜索、默认折叠，可展开查看与解除；
- **全局设置**：按 schema 渲染全部配置，保存后立即生效。

## 安装

把整个文件夹放进 AstrBot 的 `data/plugins/`，重启 AstrBot 或在插件页重载。

也可以从 Release 安装：

```
https://github.com/AethenNet/astrbot_plugin_active_reply/releases/latest/download/astrbot_plugin_active_reply.zip
```

要求：AstrBot `>= 4.0.0`，Python `>= 3.10`。

## 项目结构

| 路径 | 职责 |
|---|---|
| `main.py` | 插件入口、事件流程、提示词注入、工具与网页接口注册、屏蔽/记忆/调试逻辑 |
| `config_model.py` | 配置默认值、类型转换、全局 / 群级合并 |
| `runtime_state.py` | 按会话来源保存的短期状态（LRU） |
| `group_store.py` | 群级覆盖配置（`groups.json`） |
| `block_store.py` | 用户屏蔽记录（`user_blocks.json`，全局 + 群级） |
| `memory_store.py` | 记忆库 SQLite 表结构、检索、去重与统计 |
| `tags.py` | @ / 引用标签解析与消息组件转换 |
| `page_api.py`、`web_compat.py` | 控制台后端接口与 AstrBot 新旧网页 API 兼容 |
| `_conf_schema.json` | 配置表单定义 |
| `views/console/` | 控制台前端与图标资源 |

更多实现细节见[开发文档](./开发文档.md)。

## 致谢

- 界面图标来自 [Morphicons](https://github.com/guillermolg00/morphicons) 的 Lucide 图标集（MIT / Lucide ISC），菜单按钮使用 Morphicons 引擎做形变动画；
- 功能设计参考了社区插件 [`astrbot_plugin_astrbot_enhance_mode`](https://github.com/Axi404/astrbot_plugin_astrbot_enhance_mode)（其仓库未声明开源协议，本项目仅参考交互与功能思路，未复制其代码）。

## 许可证

本项目使用 [MIT License](./LICENSE)。
