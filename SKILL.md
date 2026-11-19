---
name: manuscript-watch-cli
description: 使用纯 HTTP 命令行工具 mwatch（manuscript-watch-cli）管理原生 Editorial Manager 与无交互验证码的 eJournalPress 投稿账本，输出结构化 JSON/TSV 或执行离线状态与阶段历史查询。
---

# manuscript-watch-cli (`mwatch`)

## 定位与边界

工作目录为 `/Users/taozhe/Projects/manuscript-watch-cli`，命令入口为 `.venv/bin/mwatch`。

- 适用范围：支持原生账号密码直登的 `editorial_manager`（`www.editorialmanager.com/<journal>/`）与未设交互式人机验证的 `ejournalpress`（`<slug>.msubmit.net` 等），适合轻量管道、服务器定时脚本与 `--format json|jsonl|tsv` 组合调用。
- 浏览器会话分流：遇到 Elsevier Submission / SSO（`submit.elsevier.com`、`submit.editorialmanager.com`）、Springer Nature ORCID，或开启交互式 Cloudflare Turnstile 的自定义域名站点（返回退出码 `3` 并提示 CAPTCHA/Turnstile），切换到持久化浏览器会话的 `manuscript-watch` 或 `Manuscript Desk`。

## 常用命令

```sh
# 查看已注册 Provider 与本地健康诊断
.venv/bin/mwatch providers --format json
.venv/bin/mwatch doctor --format json

# 离线读取本地最新状态与完整阶段历史（零网络请求）
.venv/bin/mwatch status --format json
.venv/bin/mwatch history --limit 50 --format json

# 实时只读探查或落盘检查（发现变更退出码为 2）
.venv/bin/mwatch -q check --no-save --format json
.venv/bin/mwatch -q check --changed-only --exit-code-on-change --format json
```

## 凭据约定

优先使用 `password_env` 或 `password_cmd` 注入密码；`config.toml` 保持 `0600` 权限，输出与聊天全程脱敏。
