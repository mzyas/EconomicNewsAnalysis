# Hermes Karl 首次安装需求

状态：仅记录部署要求；本次未向 WSL、Karl profile 或任何模型服务写入配置。

## 目标与边界

此项目供 WSL 侧 Hermes 的 `karl` profile 使用。Skill 本身不选择模型、
provider、URL 或 API key；Cron 主 Agent 的模型和插件 profile 的路由策略由
Hermes 配置负责。

Skill 的固定安装目标为：

```text
/home/mzyas/.hermes/profiles/karl/skills/economic-news-analysis/SKILL.md
```

原生工具插件需要独立安装在同一 profile 的插件根目录。预期目标为：

```text
/home/mzyas/.hermes/profiles/karl/plugins/economic-news-analysis/
```

在复制前，由 Hermes 内的 GPT 先确认 `karl` 的实际 profile root；如果 `hermes
config path` 显示了不同位置，以该命令的结果为准。

## 要部署的工件

| 来源 | Karl 目标 | 用途 |
|---|---|---|
| `skills/economic-news-analysis/SKILL.md` | `skills/economic-news-analysis/SKILL.md` | Agent 使用说明；不含模型配置。 |
| 项目根 `plugin.yaml`、`__init__.py`、`tools/`、`schemas/`、`sources/`、`workflows.yaml`、`skill.yaml` 与项目运行配置 | `plugins/economic-news-analysis/` | Hermes 原生工具 `run_langgraph_workflow` 和图运行时。 |
| `pyproject.toml`、`uv.lock` | 插件目录或可访问的源检出目录 | 用于在 Hermes 运行 Python 环境中解析依赖。 |
| 项目根 `daily_email_briefing.yaml` | `plugins/economic-news-analysis/daily_email_briefing.yaml` | `profile_config: daily_email` 的唯一固定模板；仓库版本无个人信息且 `email.send: false`。 |

不要复制 `.venv`、`.git`、测试缓存或任何包含本地凭据的文件。保持文件为 UTF-8
无 BOM。

## 依赖要求

Hermes 只加载插件；不会自动安装 Python 依赖。首次部署时，由 Hermes-GPT：

1. 确认 Hermes 实际使用的 Python 解释器与 venv，不能假设它等于仓库的 `uv` 环境。
2. 依据项目 `pyproject.toml` / `uv.lock` 安装该项目的运行依赖，而不是自行创建
   `ChatOpenAI` 或写入单独的 provider SDK 凭据。
3. 至少验证 `langgraph`、`langgraph-checkpoint-sqlite`、`PyYAML`、`pandas` 与
   `yfinance` 可由 Hermes 的解释器导入。
4. 在依赖就绪前，仅运行 `hermes plugins doctor --ci <plugin-dir>`；它验证清单和
   注册，但不会证明图节点可执行。

## 配置要求

由 Hermes-GPT 在确认 profile 的现有配置结构后完成以下最小配置：

1. 启用 `economic-news-analysis` 原生插件。
2. 在 `plugins.entries.economic-news-analysis.settings.model_routes` 设置节点路由。
   首次验收建议让 `analysis` 使用 `inherit_cron_model: true`，继承 Cron 主 Agent
   的 GPT 模型。
3. 只有需要显式节点路由时，才在
   `plugins.entries.economic-news-analysis.llm` 授予
   `allow_provider_override`、`allow_model_override`，并配置严格 allowlist。
4. 不在 `SKILL.md`、`skill.yaml`、图配置或环境变量中放置节点专用模型/密钥。
5. 日报 Cron 只调用 `{ "run_mode": "deliver", "profile_config": "daily_email" }`。
   插件只会读取项目根的固定模板并进行路径包含校验；它不接受路径、收件人或发送开关。
   真实邮件设置仅在受控的部署副本中补齐，不能回写到 Git 工作树。

将来如要测试 Mimo，先确认目标 provider 已由 Hermes 认证，再为测试节点添加精确
`{provider, model}` allowlist；测试后可恢复为继承 Cron 主模型。

## 验收顺序

1. 在 WSL / Karl profile 运行 Plugin Doctor，确认有一个
   `run_langgraph_workflow` 工具注册。
2. 用继承 Cron GPT 模型的 `analyze` 调用处理一段短文本。
3. 检查返回的 `llm_audit`，确认其中包含实际的 `node`、`provider`、`model`。
4. 只有前三项成功后，才按需要测试显式 Mimo 路由；严格模式下 provider/model
   不匹配必须失败，而不是静默回退。

## 已完成的仓库侧证据

- 原生插件清单、隔离加载和工具注册已通过 Hermes Plugin Doctor。
- 离线原生 handler 测试使用 Hermes 隔离命名空间和 fake `ctx.llm`，已实际进入
  LangGraph 并验证 `llm_audit`。
- 完整本地回归测试通过；真实 WSL/Hermes 调用仍待以上首次部署步骤完成。
