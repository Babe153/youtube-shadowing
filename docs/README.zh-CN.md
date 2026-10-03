# YouTube 影子跟读

输入一个 YouTube 视频链接，在 Codex、Claude Code 或 WorkBuddy 的现有聊天里，
生成忠实原话、逐句排列的 PDF，并保存到自己的学习文件夹。

## 讲义内容

- 视频标题、频道、人工/自动字幕来源和完整 YouTube 链接。
- 每句独立排列、带编号，正文不显示时间戳。
- 点击句子编号可以跳到原视频附近，便于反复听和跟读。
- 保留缩写、口语表达和真实重复，模型只补标点、大小写与断句。
- 可搜索、可复制，支持中文标题；默认保存到 Documents 下的学习目录。

第一版只处理有英文字幕的单个视频。字幕获取复用 yt-dlp，整理使用当前助手，
无需额外配置模型 API key；仍会消耗宿主的模型用量。

## 安装

需要宿主具备本地文件与命令执行能力。推荐安装 uv；也可以使用 Python 3.11+
创建专用虚拟环境。首次运行时由 skill 配置隔离依赖，不修改全局 Python。

```text
git clone https://github.com/Babe153/youtube-shadowing.git
cd youtube-shadowing
python tools/install_skill.py --target codex
```

Claude Code 改用：

```text
python tools/install_skill.py --target claude-code
```

安装器会复制完整 skill，并拒绝覆盖已有安装。默认目录分别是
`~/.agents/skills/youtube-shadowing` 和 `~/.claude/skills/youtube-shadowing`。
宿主使用其他发现目录时，可通过 `--destination` 指定父目录。

WorkBuddy 安装包由以下命令生成，再通过其 skill 导入界面导入：

```text
python tools/build_packages.py
```

`dist/` 包含三种自包含 ZIP。WorkBuddy 的必需元数据在构建时加入，字幕逻辑和
整理规则仍来自同一份核心代码。

## 使用

> 把这个视频做成影子跟读讲义：https://www.youtube.com/watch?v=...

显式选择 skill 后可以只贴链接。需要改变保存位置时告诉助手；要求“以后都保存到
这个文件夹”时才写入持久设置。没有指定时使用系统实际 Documents 路径。

学习目录示例：

```text
YouTube Shadowing/
└── 频道名称/
    └── 视频标题 [视频ID].pdf
```

原字幕、处理中间文件和恢复进度保存在工具缓存里，学习目录只放 PDF。
相同视频可复用缓存；要求重新获取字幕时使用刷新流程。文件名冲突或用户修改
已有讲义时，保留原文件并生成带版本后缀的文件。

## 当前验证与限制

已在 Windows 上验证脚本、真实字幕下载、宿主整理、PDF 文字/链接和视觉版面，
并测试了长稿分块、中断恢复、真实重复、中文标题和未知文件保护。
Claude Code 与 WorkBuddy 的宿主运行尚未实测；提供符合文档结构的安装包装，
不把打包成功当作所有平台已验证。GitHub 的跨平台 CI 尚需推送后实际运行。

字幕下载受 YouTube 访问限制影响。没有英文字幕时不会静默翻译或转写音频。
脚本检查整理稿是否忠实于清洗后字幕，但不能证明字幕与原音一致。回听精度取决于
原字幕时间。遇到可疑识别词，保留原话并标记不确定，不由模型猜测改词。

更多开发命令和内部结构见[英文 README](../README.md)。
