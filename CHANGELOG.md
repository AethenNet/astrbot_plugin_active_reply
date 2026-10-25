# Changelog

## v1.0.0 (2026-10-25)

首个正式版本，基于 `astrbot_plugin_astrbot_enhance_mode` 重写，专注"主动回复 + 交互增强"。

### ✨ 基础功能

- **消息主动回复**
  - `probability` 概率触发模式：每条群消息按配置概率随机触发回复
  - `model_choice` 模型判定模式：由模型按人格面具判断是否回复
  - 回复触发积累：连续未回复达到阈值时强制回复（概率模式）或强制判定（模型模式）
  - 按 QQ 群号的黑名单 / 白名单控制生效范围
- **群聊上下文增强**
  - 结构化群聊历史记录（用户昵称 / ID / 角色 / 消息 ID）
  - 会话消息上限、用户信息开关、角色标签开关
  - 按需图片转述（`enhance_use_image` 工具）
- **群聊回复增强**
  - React Pattern 总开关
  - 用户角色显示（admin / member 注入 system_reminder）
  - 智能回复：解析 `<mention/>` 与 `<quote/>` 标签并转换为平台组件
- **联网搜索**
  - `grok_web_search` 工具，独立 provider，支持 responses / chat_completions 双模式
- **记忆 RAG**
  - `enhance_memory_rag_write` / `enhance_memory_rag_read` 长期记忆
  - Embedding 召回、时间 / 角色 / 群过滤、跨群读取
- **插件指令**
  - `/执行回复`：管理员强制触发一次回复或判定
- **PluginPages**
  - 群配置面板：汉堡菜单按群切换，每个群可保存独立配置
  - 记忆 RAG 管理面板：筛选、分页、详情、删除、旧数据清理
  - 深色模式自适应
