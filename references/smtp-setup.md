# 邮件发送配置（内置 SMTP / himalaya）

`email.transport` 决定发信方式：

| transport | 依赖 | 适用 |
|---|---|---|
| `command`（默认） | himalaya（或 `email.send_command` 指定的其他命令） | 已配好 himalaya 的 WSL / Linux profile |
| `smtp` | 无（Python 标准库） | 原生 Windows，或不想装 himalaya 的环境 |

两种方式发出的正文（纯文本 + HTML）与附件相同。`email.timeout_seconds`（默认 120）限制单次发送的耗时。

## Gmail 应用专用密码

Gmail 不允许直接用登录密码。需要：
1. 启用两步验证：https://myaccount.google.com/security
2. 生成应用专用密码：https://myaccount.google.com/apppasswords
3. 把密码放进只有自己可读的文件，或环境变量（见下文）

## 内置 SMTP（`transport: smtp`）

在部署副本的 `daily_email_briefing.yaml` 中（仓库里的模板保持占位符和 `send: false`）：

```json
"email": {
  "from": "REPLACE_WITH_SENDER",
  "to": "REPLACE_WITH_RECIPIENT",
  "send": true,
  "transport": "smtp",
  "smtp": {
    "host": "smtp.gmail.com",
    "port": 587,
    "security": "starttls",
    "password_file": "REPLACE_WITH_PRIVATE_PATH"
  }
}
```

- `security`：`starttls`（默认，端口默认 587）或 `ssl`（端口默认 465）。不支持明文连接。
- `username`：省略时取 `from` 里的邮箱地址。
- 密码：先读 `password_env` 指定的环境变量，为空再读 `password_file`。两者都没有时，在连接前就失败。
  - `password_file` 写**绝对路径**：Hermes profile 会重定向 `$HOME`，`~` 不一定指向你以为的目录。
  - 配置里写 `password` 字段会被拒绝（schema 和运行时都会拒），密码也不会出现在日志、
    `DeliveryResult.error` 或 runtime JSON 里；服务器回复里若带了密码，会被替换成 `***`。
- `to` 可以写多个地址，用英文逗号分隔。部分收件人被拒收时，邮件仍算已发送，被拒的地址会出现在
  「部分收件人被拒收」告警里。
- 失败时 `邮件发送失败:` 告警里会带上异常类型和服务器回复，例如
  `SMTPAuthenticationError: 535 5.7.8 Username and Password not accepted`。

## himalaya（`transport: command`）

### config.toml 模板

```toml
[accounts.personal]
email = "you@gmail.com"
display-name = "Hermes Agent"
default = true

backend.type = "imap"
backend.host = "imap.gmail.com"
backend.port = 993
backend.encryption.type = "tls"
backend.login = "you@gmail.com"
backend.auth.type = "password"
backend.auth.cmd = "cat /home/user/.config/himalaya/app_password"

message.send.backend.type = "smtp"
message.send.backend.host = "smtp.gmail.com"
message.send.backend.port = 587
message.send.backend.encryption.type = "start-tls"
message.send.backend.login = "you@gmail.com"
message.send.backend.auth.type = "password"
message.send.backend.auth.cmd = "cat /home/user/.config/himalaya/app_password"

[accounts.personal.folder.aliases]
inbox = "INBOX"
sent = "[Gmail]/已发邮件"      # 中文界面
drafts = "[Gmail]/草稿"
trash = "[Gmail]/已删除邮件"
```

密码文件：`echo '16位密码' > ~/.config/himalaya/app_password && chmod 600 ~/.config/himalaya/app_password`

### HOME 覆盖

Hermes profile 会重定向 `$HOME`。himalaya 配置文件在真实 home 下时需显式设置：
```
HOME=/home/user himalaya <command>
```

### Gmail 文件夹别名（按界面语言）

| 功能 | English | 中文 | 日本語 |
|---|---|---|---|
| Sent | `[Gmail]/Sent Mail` | `[Gmail]/已发邮件` | `[Gmail]/送信済みメール` |
| Drafts | `[Gmail]/Drafts` | `[Gmail]/草稿` | `[Gmail]/下書き` |
| Trash | `[Gmail]/Trash` | `[Gmail]/已删除邮件` | `[Gmail]/ゴミ箱` |

先用 `himalaya folder list` 确认实际文件夹名再配置。

### 安全发送

❌ `cat << EOF | himalaya template send` → 触发 Dangerous Command
✅ `write_file → himalaya template send < /tmp/file` → 安全

### 失败信息

插件会捕获发送命令的 stderr，把最后几行写进 `邮件发送失败:` 告警；命令超过 `email.timeout_seconds`
仍未结束时会被终止并报超时。
