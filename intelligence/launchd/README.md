# launchd 高频采集

`collect --due` 是确定性任务，每 30 分钟由 Mac mini 的 `launchd` 启动。分析与报告仍由 Multica Cloud Autopilot 触发。

## 前置条件

- `intelligence/.venv/bin/intelctl` 已存在且 `intelctl collect --due` 手动运行通过。
- MCP OAuth 在无交互环境可刷新，Worker API 凭据可由本机 Keychain 或受限环境读取。
- `intelligence/logs/` 已创建并被 Git 忽略。
- CLI 自身实现单实例锁；`launchd` 不提供业务幂等保证。

## 安装

模板中的 `__REPOSITORY_PATH__` 必须替换为仓库绝对路径，生成：

```text
~/Library/LaunchAgents/com.fatflowers.personal-intelligence.collect.plist
```

安装过程不得把 Token 写入 plist。生成后先校验：

```bash
plutil -lint ~/Library/LaunchAgents/com.fatflowers.personal-intelligence.collect.plist
```

然后加载当前用户的任务：

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.fatflowers.personal-intelligence.collect.plist
launchctl enable gui/$(id -u)/com.fatflowers.personal-intelligence.collect
launchctl kickstart -k gui/$(id -u)/com.fatflowers.personal-intelligence.collect
```

安装与加载属于实施任务 OPS-001；只有手动采集、重启恢复和防重入测试都通过后才能标记“已完成”。

## 状态与日志

```bash
launchctl print gui/$(id -u)/com.fatflowers.personal-intelligence.collect
tail -n 100 intelligence/logs/collect.stdout.log
tail -n 100 intelligence/logs/collect.stderr.log
```

日志只能包含 Run ID、计数、错误码和脱敏摘要，不得输出 OAuth Token、API Key 或正文全文。系统的权威运行状态在 D1 `pipeline_runs`，日志只是排障辅助。

## 卸载

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.fatflowers.personal-intelligence.collect.plist
```

卸载只停止调度，不删除配置、D1 数据、报告或 Git 历史。
