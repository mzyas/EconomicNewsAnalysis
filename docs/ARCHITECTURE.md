# Economic News Analysis — 项目架构与 LangGraph Harness 机制

> 基于 GitNexus 方法论 + langgraph-development skill 整理

---

## 1. 项目概览

```
EconomicNewsAnalysis  v0.1.0
├── tools/              → 55 个 Python 文件（含 graph/ 与 graph/nodes/ 子包、market_data/ 子包）
├── tests/              → 27 个测试文件
├── schemas/            → 8 个 JSON Schema
├── references/         → 7 个文档（6 个 Markdown + 1 个邮件 workflow 指南）
├── sources/            → 3 个源配置（RSS；市场数据，16 个资产；个股 watchlist）
├── examples/           → 10 个示例（6 个配置 + 4 个 JSON 样例）
├── skill.yaml          → 技能入口配置（默认参数；版本号取自 pyproject.toml）
├── workflows.yaml      → 工作流注册（6 个）
├── daily_email_briefing.yaml → 邮件投递模板（插件按固定名读取，不含个人信息）
├── plugin.yaml, __init__.py  → Hermes 原生插件（工具 run_langgraph_workflow）
├── pyproject.toml      → Python 项目元数据与 ruff 配置
└── uv.lock             → 依赖锁定
```

**功能定位**：多源经济新闻抓取 → 归一化 → LLM 分析 → 质量门控 → 简报生成 → 邮件投递的自动化管道，由 LangGraph 的 StateGraph 编排（`tools/runtime.py` 只是命令行入口，运行的就是这张图）。支持 7 种运行模式，其中 `trend` 是**无 LLM 的独立趋势表**，用于每日低成本的确定性市场数据输出。简报包含**排序国家均衡**、**官媒宏观背景（中美日）LLM 合成**、**💡 科技与AI / 🔥 热点速览板块**（详见 §5b）和**行情异动新闻**（`movers_news` 节点，详见 §5b.4）。每次运行有**时间预算**，超时会降级而不是中断（详见 §12.5）。

---

## 2. LangGraph Harness 机制

核心思想：**用 LangGraph 的 StateGraph 取代传统线性脚本**，把每个处理阶段建模为独立的图节点，通过有向边和条件路由编排。

### 2.1 状态定义 — `tools/graph/state.py`

```python
class NewsAnalysisState(TypedDict, total=False):
    run_id: str                    # 运行标识
    mode: str                      # 运行模式（briefing/fetch/analyze/deliver/trend...）
    config: dict                   # 运行时配置（来自 skill.yaml + CLI 覆盖）
    items: list[dict]              # 归一化后的新闻条目
    analysis: list[dict]           # LLM 分析结果
    briefing: dict                 # 简报数据（markdown + html）
    trend_table: dict              # 趋势表数据（仅 trend mode）
    delivery: dict                 # 投递状态
    pipeline_status: str           # 业务流水线状态
    output_status: str             # 业务产物状态
    delivery_status: str           # 邮件投递状态
    delivery_kind: str             # 附件/无附件/失败通知/无投递
    _phase: str                    # 内部阶段标记（私有字段，不输出）
    _quality_gate: dict            # 质量门控结果
    _retry_count: int              # 重试计数
    # ... 其他字段
```

**设计原则**：
- 公开字段（`items`, `analysis`, `briefing` 等）直接映射到 `runtime_result.schema.json`
- 私有字段（`_phase`, `_quality_gate`, `_retry_count`）带 `_` 前缀，驱动图执行但不进入最终输出
- `initial_state()` 工厂函数统一初始化

### 2.2 节点拓扑 — `tools/graph/nodes/`

16 个图节点，每个是一个纯函数 `(state) → state_update`：

#### 完整拓扑（16 个节点，所有边均列出）

```text
                                ENTRY
                               (入口)
                                  │
                             load_config
                            (加载配置)
                                  │
                    ──────────────┼──────────────
                    │             │              │
               check    list-sources     briefing / deliver /
              (检查)      (源)          analyze / fetch / trend
                    │             │     (简报/投递/分析/抓取/趋势)
                    ▼             ▼              │
              check_log     list_sources         ▼
             (检查日志)     (列出源)    fetch_market_data
                                        (获取市场数据)
                    │             │              │
                    │             │              │ [_after_market_data]
                    │             │              │ → 始终走趋势表
                    │             │              ▼
                    │             │       ┌──────────────┐
                    │             │       │ trend_table  │
                    │             │       │   (趋势表)    │
                    │             │       └──────┬───────┘
                    │             │              │ [_after_trend_table]
                    │             │              │ → trend→输出
                    │             │              │   其他→抓取
                    │             │         ─────┴──────
                    │             │         │           │
                    │             │     trend       other
                    │             │    (趋势)      (其他)
                    │             │         │           │
                    │             ▼         ▼           ▼
                    │           END    write_outputs  fetch_feeds
                    │         (结束)    (写入输出)    (抓取RSS)
                    │                                         │
                    │                                         ▼
                    │                                    movers_news
                    │                                   (异动新闻)
                    │                                         │
                    │                                         ▼
                    │                                  google_news_resolve
                    │                                   (解析谷歌新闻)
                    │                                         │
                    │                                         ▼
                    │                                     rank_items
                    │                                    (排序条目)
                    │                                         │
                    │                                         │ [_after_rank]
                    │                                         │ → fetch→结束
                    │                                       ──┴──
                    │                                      │    │
                    │                                    fetch other
                    │                                  (抓取) (其他)
                    │                                      │    │
                    │                                      ▼    ▼
                    │                                    END  collect_evidence
                    │                                  (结束)  (收集证据)
                    │                                            │
                    │                                            ▼
                    │                                      analyze_with_llm
                    │                                       (LLM分析)
                    │                                    [_after_analyze]
                    │                                    → analyze→结束
                    │                                         │
                    │                                         ▼
                    │                                     analyze other
                    │                                     (分析)  (其他)
                    │                                         │    │
                    │                                         ▼    │
                    │                                        END   │
                    │                                      (结束)  │
                    │                                             ▼
                    │                                       quality_gate
                    │                                       (质量门控)
                    │                                            │
                    │                              [_should_retry_or_downgrade]
                    │                                    (重试/降级路由)
                    │                                          ──┼──
                    │                                         │  │  │
                    │                                     passed │  downgrade
                    │                                    (通过)  │  (降级)
                    │                                         │  │  │
                    │                                         ▼  │  │
                    │                                   build_briefing
                    │                                    (构建简报)
                    │                                         ▲
                    │                                         │ retry
                    │                                         │ (可重试)
                    │                                         └─ collect_evidence
                    │                                             (收集证据)
                    │                                         │
                    │                                         ▼
                    │                                   write_outputs
                    │                                    (写入输出)
                    │                                         │
                    │                                         │ [_should_deliver]
                    │                                         │   (投递路由)
                    │                                       ──┴──
                    │                                      │    │
                    │                                  deliver other
                    │                               (投递/趋势) (其他)
                    │                                      │    │
                    │                                      ▼    │
                    │                                deliver_email
                    │                                 (投递邮件)
                    │                                      │    │
                    │                                      ▼    │
                    │                                write_run_log
                    │                                (写入运行日志)◄┘
                    │                                      │
                    │                                      ▼
                    │                                     END
                    │                                   (结束)
                    └─────────────────────────────────────────
```

#### 趋势表链路（trend mode — 完全不经过 RSS/LLM）

```
load_config → fetch_market_data
                      │
                      │ [_after_market_data] → 始终
                      ▼
                 trend_table
                      │ [_after_trend_table] → trend→write_outputs
                      ▼
                write_outputs
                      │ [_should_deliver] → trend+deliver_email→deliver_email
                      │                   其他→write_run_log
                      ├── deliver_email → write_run_log → END
                      └── write_run_log → END
```

**节点模式**（每个节点遵循的范式）：

```python
def my_node(state: dict[str, Any]) -> dict[str, Any]:
    config = state.get("config", {}) or {}
    try:
        result = do_work(config)
        return {"result_key": result, "_phase": "phase_done"}
    except Exception as exc:
        logger.exception("my_node failed")
        return {"errors": [{"type": type(exc).__name__, "message": str(exc)}]}
```

### 2.3 图装配 — `tools/graph/workflow.py`

构建流程：

```python
def build_workflow(checkpoint_path=None, saver=None):
    workflow = StateGraph(NewsAnalysisState)

    # 注册所有节点
    workflow.add_node("load_config", load_config_node)
    workflow.add_node("fetch_feeds", fetch_feeds_node)
    # ... 其余节点

    # 入口
    workflow.set_entry_point("load_config")

    # 条件路由：根据 mode 选择首节点
    workflow.add_conditional_edges("load_config", _after_load_config, {...})

    # 条件路由：市场数据 → 趋势表（所有模式均先过趋势表）
    workflow.add_conditional_edges("fetch_market_data", _after_market_data, {...})
    # 条件路由：趋势表 → 分流（trend 模式→输出，其他→新闻链路）
    workflow.add_conditional_edges("trend_table", _after_trend_table, {...})

    # 条件路由：fetch 模式提前结束
    workflow.add_conditional_edges("rank_items", _after_rank, {...})

    # 质量门控：重试/降级路由
    workflow.add_conditional_edges("quality_gate", _should_retry_or_downgrade, {...})

    # 编译
    return workflow.compile(checkpointer=saver)
```

### 2.4 条件路由函数

| 函数 | 决策依据 | 返回值 → 下一节点 |
|------|----------|-------------------|
| `_after_load_config` | `state.mode` | `check` → check_log / `list-sources` → list_sources / 默认 → fetch_market_data |
| `_after_market_data` | 始终 | 始终 → trend_table（所有模式均先过趋势表）|
| `_after_trend_table` | `state.mode` | `trend` → write_outputs / 其他 → fetch_feeds |
| `_after_rank` | `state.mode` | `fetch` → END / 其他 → collect_evidence |
| `_after_analyze` | `state.mode` | `analyze` → END / 其他 → quality_gate |
| `_should_retry_or_downgrade` | `_run_budget.degraded` + `_quality_gate.passed` + `_retry_count` | 预算已降级 → build_briefing（降级）/ 通过 → build_briefing / 可重试 → collect_evidence / 超限 → build_briefing（降级） |
| `_should_deliver` | `state.mode` + `config.deliver_email` | `deliver` → deliver_email / `trend`+deliver_email → deliver_email / 其他 → write_run_log |

---

## 3. 运行时入口 — `tools/runtime.py`

**支持 7 种运行模式**：`briefing`（完整简报）、`deliver`（简报+邮件）、`analyze`（纯LLM分析）、`fetch`（仅抓取）、`check`（日志检查）、`list-sources`（列出源）、`trend`（独立趋势表，v0.7.0 新增）。

```bash
# 新运行（在项目根目录下执行）
python -m tools.runtime --config examples/daily_briefing.yaml

# 恢复模式（需要同一个 --run-id 的 checkpoint）
python -m tools.runtime --config examples/daily_briefing.yaml --run-id <id> --resume
```

```
runtime.py
  ├── load_runtime_config()       # 合并 skill.yaml 默认值 + 用户配置
  ├── initial_state(config)       # 初始化 NewsAnalysisState
  ├── build_workflow()            # 装配图
  ├── graph.invoke(state)         # 执行
  └── state → JSON 输出           # 投影回 runtime_result.schema.json 格式
```

### 3.1 配置加载链

```
skill.yaml (defaults)
     ↓ 深度合并
CLI --config (user config)
     ↓ setdefault()
config dict (connect_timeout=10, read_timeout=40 ...)
     ↓
config["_skill"] = {name, version}
```

---

## 4. 产出物布局 — 文件持久化

`write_outputs_node` 只持久化业务 Markdown。最终 Runtime JSON 在邮件投递和运行日志完成后由 `run_workflow()` 原子写入，确保磁盘内容包含最终投递状态：

```
output_dir/
  ├── {topic}_{YYYYMMDDTHHMMSSZ}.md     # 简报/趋势表 Markdown
  └── {run_id}_runtime.json              # 最终运行结果
```

- `{topic}` 自动 slug 化（Unicode-safe），取自 `state.topic` → `state.mode` → `config.topic` → `"briefing"` 的优先级
- **JSON** 在全工作流结束后写入，包含 `pipeline_status`、`output_status`、`delivery_status`、`delivery_kind` 和最终邮件错误
- **Markdown** 仅在正文通过实质内容检查后写入；路径还会通过普通文件、非空和正文检查
- **MML** 是投递内部中间文件，不属于 `output_files`，无附件邮件的临时 MML 由 `send_email()` 创建并清理

运行时日志（`write_run_log_node`）追加写入 `config['log_path']`（`skill.yaml` 默认 `.hermes/logs/daily-workflow.jsonl`，相对插件/项目根目录解析），每行为一条 JSONL 记录；未配置 `log_path` 时不写日志。`check_log_node` 通过 `evaluate_fetch_window()` 读取同一份日志判断当日是否已抓取，避免重复运行；`log_path` 缺失时回退到项目根目录的 `logs/runtime.jsonl`。

| 产出物 | 文件类型 | 触发条件 | 内容 |
|--------|----------|----------|------|
| JSON 快照 | `.json` | `output_dir` 已设置 | 完整运行结果（含 analyses、market_context、statistics） |
| Markdown 简报 | `.md` | `output_dir` + briefing/trend 模式 | 人类可读的渲染文本 |
| 运行日志 | `.jsonl`（追加） | `log_path` 已设置 | 每次运行的统计摘要 |

### 4.1 状态解耦与交付降级

节点只维护 `pipeline_status`；最终投影令兼容字段 `status == pipeline_status`。`output_status` 和 `delivery_status` 不会反向修改业务流水线状态。

| 场景 | pipeline | output | delivery | email_sent | delivery_kind |
|------|----------|--------|----------|------------|---------------|
| 完整报告和附件发送成功 | success | success | success | true | report_with_attachment |
| 部分来源失败但报告可用 | partial_success | success | success | true | report_with_attachment |
| 正文有效但附件不可用 | success/partial_success | failed/partial_success | success | true | report_without_attachment |
| 无业务正文，失败通知成功 | failed | skipped | success | true | failure_notification |
| 邮件未启用 | 任意 | 任意 | skipped | false | none |

正文判定同时检查 Markdown 和 HTML 实际内容。Markdown 只有 BOM、空白、标题或占位符时视为无内容；附件必须是存在且非空的普通文件。投递按“有效附件报告 → 无附件正文 → 失败通知”降级，禁止静默结束。

---

## 5. 质量门控机制 — `tools/graph/nodes/quality.py`

`quality_gate_node` 在生成简报前补全缺失的产物，再给出通过/不通过的结论：

```
1. 补全信号      对缺少 signals 的条目（最多 graph.enrich_max_items，默认 25 条）内联调用一次 LLM
2. 推导兜底产物  今日资讯主线（_derive_mainlines）
                 市场影响地图（_llm_market_impact，失败回退 _market_impact_from_items）
                 中美日官媒宏观背景（_llm_macro_background → ResearchAdapter.macro_background）
3. 门控检查      见下表
```

| 检查 | 不满足时写入 `issues` |
|------|----------------------|
| 前 5 条分析都有 `signals` | `missing_signals:<id,…>` |
| 前 5 条重要条目的标题已是中文（未核验的 Google News 发现链接除外） | `untranslated_titles:<id,…>` |
| 主线数量在 1–5 之间 | `mainlines_invalid` |
| 市场影响地图 5 项（`rates_bonds`、`fx`、`equities`、`commodities`、`risk_appetite`）都非空 | `market_impact_map_incomplete` |

重要条目缺少已抓取的正文证据只记日志，不阻断（补全步骤可以只靠标题合成信号）。

结果写入 `_quality_gate`（`passed`、`issues`、`missing_evidence`、`untranslated_titles`、`checked_at`）。
合成产物写入 `daily.*`，`build_briefing_node` 复用门控批准的同一份值；任一合成失败都回退到确定性版本。

**不通过不会阻断投递**：`_retry_count` 加 1，`_should_retry_or_downgrade` 在 `max_quality_retries`
（`skill.yaml` 默认 1，每日邮件模板为 2）内回到 `collect_evidence` 重试；超限（或运行预算已降级，见 §12.5）
则继续生成简报。此时 `pipeline_status` 为 `partial_success`，简报里带「部分成功」提示，邮件照常发送。

> 旧版离线工具 `tools/enrichment.py`（`python -m tools.enrichment`）仍保留 `validate_email_readiness()`，
> 用于校验外部提供的富化补丁，不在 LangGraph 链路里：
>
> ```
> 已核验条目 → signals 必须填写？           → 缺失则拒绝
> 前5候选    → topics 必须显式确认？        → 缺失则拒绝
> market_impact → 5字段全部填写？           → 缺失则拒绝
> mainlines  → supporting_item_ids 在前5候选内？→ 越界则拒绝
> Google News → translated_title 已提供？    → 缺失则拒绝
> ```

---

## 5b. 简报板块、排序与国家均衡

### 5b.1 板块结构（`tools/briefing_builder.py`）

简报固定六个内容区（另有条件区「📉 异动解读」，仅在出现异动时插入，见 §5b.4），顺序为：

```
今日资讯主线（逐条，≤5）
📉 异动解读                ← 条件区：行情异动标的的定向新闻（§5b.4）
🌏 官媒宏观背景（中美日）   ← 官方条目按国别归并的三段叙述，优先 LLM 合成
🌐 财经媒体新闻             ← news_media 表格
💡 科技与AI                ← tech_media / tech_search 表格
🔍 Google News 快讯        ← 其它 Google 关键词发现
🔥 热点速览                ← hot_search 综合头条，排除财经/科技/AI
```

`_item_category(item)` 按来源 `category` 分类（先于 Google-News 兜底）：`market_mover`→`movers`，`tech_media`/`tech_search`→`tech`，`hot_search`→`hot`，`news_media`→`media`，其余官方/政府/央行→`official`。热点板块再用 `_is_finance_or_tech()` 关键词剔除财经/科技/AI 条目，只留「其它高热」。

### 5b.2 国家均衡（`rank_items_node`）

历史问题：rank 阶段只按板块切配额、组内纯按宏观相关分排序，导致中国官方条目（中文源多 + 中文当期信号正则加分）挤占美日。解决：

- `relevance_ranker.balance_by_country(ranked, quota, limit)`：按国别配额重选，名额不足时用高分项回填以保住总条数。
- `rank_items_node` 对 official / media 两组开启均衡（**先全量排序、再按配额截断**——否则均衡前就被截掉的美日条目无从恢复）。
- 配置 `graph.country_balance`（默认 `{CN:4, US:4, JP:4, default:99}`；缺省关闭，向后兼容）。
- 同时给日文补了当期信号正则、并把日本官方源（mof/cabinet/meti）纳入官方加分名单，抹平「日文天然低分」。

### 5b.3 每板块配额（`graph` 块）

`official_max_items` / `media_max_items` / `tech_max_items`(默认6) / `google_max_items`(6) / `hot_max_items`(5) / `movers_max_items`(6)；schema 见 `runtime_input.schema.json`。

### 5b.4 行情异动新闻（`tools/movers.py`）

趋势表只说明「涨跌了」，不解释原因；宏观 RSS 也覆盖不到个股事件。`movers_news` 把行情和新闻接起来：

```
fetch_market_data → trend_table → fetch_feeds → movers_news → google_news_resolve → rank_items
```

- **选取**（`select_movers`）：仅看 `status=success`、`asset_type=price`、`asset_class` 在 `movers.asset_classes`（默认 `equity_stock` + `equity_index`）内的快照；日涨跌 ≥ `day_threshold_pct`（默认 4）或周涨跌 ≥ `week_threshold_pct`（默认 8）即命中，按超阈值倍数从大到小取 `max_symbols`（默认 5）个。
- **检索**：每个命中标的构造 `"<name>" stock when:2d` 的 Google News RSS 查询，取 `items_per_symbol`（默认 3）条；来源 id 为 `google_news_mover_<symbol_id>`，类别 `market_mover`，因此照常经 `google_news_resolve` 解析发布页。
- **标注**：每条新闻带 `mover.trigger`（如 `NVIDIA 日 -6.2% / 周 -9.1%`），渲染时写入表格「主题」列。
- **板块**：`_item_category` 返回 `movers`，`rank_items_node` 给独立配额 `movers_max_items`，排在其它板块之后；不会混入官方/Google 板块。
- **个股只触发、不入表**：个股放在独立的 `sources/stock_watchlist.yaml`（`market_data.watchlist_config`），由 `load_stock_watchlist` 补默认值（`asset_class: equity_stock`、`asset_type: price`、`display_precision: 2`）后追加到 `market_sources.yaml` 的标的之后（id 重复的跳过）；`trend_table_node` 过滤 `equity_stock`，趋势表仍是原来的 16 个资产。watchlist 读取或校验失败只记 `WatchlistConfigError`，不影响核心行情。
- **插件安装后可改**：Hermes 下 `anchor_profile_paths` 首次运行时把内置 watchlist 复制到 `<HERMES_HOME>/plugin-data/EconomicNewsAnalysis/stock_watchlist.yaml` 并改读该副本；已存在的副本不会被覆盖，重装插件也不丢；指向插件目录之外的显式路径保持不变。
- **降级**：单个查询失败只记入 `errors`（`MoverFetchError`）；节点异常记 `MoverError`，均不中断主流程。`trend` 模式、`fetch_enabled=false`、`movers.enabled=false` 时整体跳过。
- **限制**：只能抓当前行情窗口内的异动，无法回填历史某天的大跌。

配置（均有默认值，见 `schemas/runtime_input.schema.json` 的 `movers` 块）：`enabled`、`day_threshold_pct`、`week_threshold_pct`、`max_symbols`、`items_per_symbol`、`window`、`asset_classes`。

---

## 6. 超时策略 — connect/read timeout 拆分

| 参数 | 默认值 | 作用域 |
|------|--------|--------|
| `connect_timeout` | 10s | TCP 连接建立 |
| `read_timeout` | 40s | 数据下载 |

实现方式（`feed_fetcher_core.py`）：

```python
with urllib.request.urlopen(req, timeout=connect_timeout) as resp:
    try:
        resp.fp.raw._sock.settimeout(read_timeout)  # 连接后切换为读取超时
    except AttributeError:
        pass
    raw = resp.read()
```

向后兼容：旧配置中的 `timeout_seconds` 自动拆分为 `connect_timeout = max(1, old // 3)` + `read_timeout = old`。

---

## 7. Hermes LLM 路由

`tools/hermes_plugin.py` 是图与 Hermes 的唯一 LLM 边界。它将运行时的
`ctx.llm` 交给 `run_workflow()`，并从 plugin profile 的
`settings.model_routes` 读取节点路由。图节点不创建 provider SDK client，
不读取 API key，也不保存 provider URL。

- 具备 `provider` 与 `model` 的路由会显式传给 `ctx.llm.complete(...)`。
- 未配置路由或标记 `inherit_cron_model: true` 的节点省略两项参数，继承 Cron
  主 Agent 的模型。
- 每次调用将实际 `{node, provider, model}` 写入 `llm_audit`；严格模式下，
  显式路由与实际结果不一致即报错。

权限与 allowlist 由 Hermes 的 `plugins.entries.EconomicNewsAnalysis.llm`
配置管理，不属于本 skill 的 `skill.yaml`。

---
## 8. 独立趋势表 — `tools/trend_table.py`

### 8.1 设计目标

`trend` mode 提供一个**不调用 RSS、不调用 LLM** 的每日市场趋势表，用于在低计算成本下生成可保存和可投递的确定性输出。

### 8.2 数据口径

| 指标 | 口径 |
|------|------|
| 日变化 | 最新有效收盘价相对前一个有效交易日 |
| 周变化 | 最新有效收盘价相对前第 5 个有效交易日 |
| 月变化 | 最新有效收盘价相对前第 21 个有效交易日 |

*数据来源*：`YFinanceProvider` 自动获取至少 3 个月（`_minimum_period("3mo")`）日线数据。

### 8.3 资产分类与展示

| 资产类型 | 变化单位 | 方向阈值 |
|----------|----------|----------|
| `price`（股指、商品、外汇、波动率） | % | 0.3% / 1.0% |
| `yield`（国债收益率） | bp | 3bp / 10bp |

方向符号统一为 `↗`（上行）、`→`（横盘）、`↘`（下行），HTML renderer 通过颜色区分强度（红色=强上行、橙色=弱上行、灰色=横盘、浅蓝=弱下行、深蓝=强下行）。

**中文资产名**：每个 symbol 支持 `name_zh` 字段（可选），沿 `MarketSymbol → MarketSnapshot → TrendRow` 完整数据链传递。邮件 HTML 渲染器以 Google 财经风格显示：英文名在上、中文名在下。

**单位前置**：变化值以 `% +1.44` 或 `bp +3` 格式显示（单位在前），替代传统的 `+1.44 %` 后缀格式。

### 8.4 市场源配置 — `sources/market_sources.yaml`

共 16 个资产，涵盖 6 个类别：

| 类别 | 资产 | Yahoo Ticker |
|------|------|-------------|
| 股票指数 | S&P 500, Nasdaq 100, Nikkei 225, CSI 300, Shanghai Composite, HSI | ^GSPC, ^NDX, ^N225, 000300.SS, 000001.SS, ^HSI |
| 国债收益率 | US 10Y, US 5Y | ^TNX, ^FVX |
| 外汇 | DXY, USD/JPY | DX-Y.NYB, JPY=X |
| 商品 | Gold, Crude Oil, Copper, Silver | GC=F, CL=F, HG=F, SI=F |
| 波动率 | VIX | ^VIX |
| 加密货币 | Bitcoin | BTC-USD |

每个 symbol 声明 `asset_type` 和 `display_precision`，provider 将其透传到 `MarketSnapshot`。单资产失败不影响其他资产的数据获取。

个股不在此文件，而在 `sources/stock_watchlist.yaml`（schema：`schemas/stock_watchlist.schema.json`），见 5b.4。

### 8.5 图集成

`trend_table_node` 在 `fetch_market_data_node` 之后被条件路由 `_after_market_data` 选中：

```
fetch_market_data → _after_market_data → trend_table → _after_trend_table
                                      trend → write_outputs
                                      其他 → fetch_feeds
```

`trend` mode 经此分流直接输出，不经过 RSS/LLM 链路。`briefing`/`deliver`/`analyze`/`fetch` 模式从 trend_table 继续进入新闻分析链路。

---

## 9. 关键依赖

| 依赖 | 用途 |
|------|------|
| `langgraph`, `langgraph-checkpoint-sqlite` | StateGraph 框架与 SQLite checkpoint |
| Hermes `ctx.llm` facade | 托管 provider/model 路由与调用（由 Hermes 提供，不在 `pyproject.toml` 里） |
| `yfinance`, `pandas` | 市场数据源与时间序列处理 |
| `requests` | 证据正文抓取 |
| `urllib`（标准库） | RSS / World Bank API 抓取 |
| `beautifulsoup4` | HTML 解析 |
| `PyYAML` | 配置加载 |
| `typing-extensions` | Python < 3.11 的类型标注兼容 |
| `pytest`, `ruff`（开发组） | 测试，以及 lint 与格式化 |

---

## 10. 模块地图（按 GitNexus 簇分类）

| 功能簇 | 核心文件 | 外部依赖 |
|--------|----------|----------|
| **配置加载** | `config_loader.py`, `schema_validation.py` | PyYAML |
| **RSS 抓取** | `feed_fetcher_core.py`, `feed_fetcher.py` | urllib |
| **Google News 解析** | `google_news_enrichment.py` | urllib, html.parser |
| **归一化/排序** | `normalizer.py`, `relevance_ranker.py` | — |
| **LLM 分析** | `graph/research.py`, `graph/prompts.py`, `hermes_plugin.py` | Hermes `ctx.llm` |
| **证据收集** | `evidence_fetcher.py` | requests, beautifulsoup4 |
| **简报渲染** | `briefing_builder.py`, `markdown_writer.py` | — |
| **趋势表渲染** | `trend_table.py` | —（纯 Python，无外部依赖） |
| **邮件投递** | `email_render.py`, `email_delivery.py`, `smtp_sender.py` | himalaya CLI 或 smtplib（标准库） |
| **市场数据** | `market_data/` | yfinance |
| **质量门控** | `graph/nodes/quality.py`（旧版离线补丁工具：`enrichment.py`） | — |
| **运行日志** | `run_log.py` | JSONL |
| **Graph Harness** | `graph/`：`workflow.py`、`state.py`、`nodes/` 包、`research.py`、`prompts.py`、`checkpoint.py`、`run_budget.py` 等 | langgraph |

---

## 11. 执行流程（完整链路）

### 11.1 完整链路（所有模式均先过趋势表）

```
用户输入
  │
  ▼
load_runtime_config()       ← skill.yaml + --config
  │
  ▼
initial_state()             ← NewsAnalysisState
  │
  ▼
build_workflow()            ← StateGraph 装配
  │
  ▼
graph.invoke(state)         ← 图执行（16个节点，条件路由）
  │
  ├── load_config           → 加载源列表、过滤
  ├── fetch_market_data     → yfinance 快照（所有模式均执行）
  ├── trend_table           → 趋势表渲染（所有模式均执行，纯确定性）
  │                            │
  │                            ├── trend 模式 → write_outputs（跳过 RSS/LLM）
  │                            └── 其他模式   → fetch_feeds（继续新闻链路）
  │
  │  ── 以下仅非 trend 模式执行 ──
  ├── fetch_feeds           → RSS/API 抓取（connect_timeout/read_timeout）
  ├── movers_news           → 异动标的 → Google News 定向检索（仅 briefing/deliver）
  ├── google_news_resolve   → 解析 Google News 发现链接
  ├── rank_items            → 宏观相关性排序
  ├── collect_evidence      → 证据收集（可选 LLM 辅助）
  ├── analyze_with_llm      → LLM 核心信号提取
  ├── quality_gate          → 质量门控（可重试/降级）
  ├── build_briefing        → Markdown + HTML 渲染
  │
  │  ── 以下所有模式均执行 ──
  ├── write_outputs         → 文件持久化（JSON + Markdown）
  ├── deliver_email         → himalaya（MML）或内置 SMTP 发信（trend 模式跳过质量门）
  └── write_run_log         → JSONL 日志（含 item_urls 供去重）
  │
  ▼
_state → _project_to_old_schema() → JSON stdout
```

### 11.2 趋势表链路（trend mode）

```
用户输入（mode: trend）
  │
  ▼
load_runtime_config()       ← skill.yaml + --config（建议 market_data.period: 3mo）
  │
  ▼
initial_state()             ← NewsAnalysisState
  │
  ▼
build_workflow()            ← StateGraph 装配
  │
  ▼
graph.invoke(state)         ← 图执行（跳过 RSS/LLM 节点，但经过趋势表）
  │
  ├── load_config           → 标记运行开始
  ├── fetch_market_data     → yfinance 快照（provider 自动确保 ≥3mo 数据）
  │                             返回 MarketSnapshot（含 change_1w_pct/change_1m_pct）
  │
  ├── trend_table           → 纯确定性计算（无 LLM）：
  │                           • build_trend_rows() → 方向计算（↗/→/↘）
  │                           • render_markdown() / render_html()
  │                           • price 类用 %，yield 类用 bp
  │                              ↓ 条件路由 _after_trend_table → write_outputs
  │
  ├── write_outputs         → 文件持久化（JSON + Markdown）
  ├── deliver_email         → 组合 HTML（趋势表 + 可选简报），跳过质量门
  └── write_run_log         → JSONL 日志
  │
  ▼
_state → _project_to_old_schema() → JSON stdout
```

### 11.3 趋势表数据结构

趋势表（`TrendRow`）中每个资产包含以下派生字段：

| 字段 | 价格型资产（sp500, gold…） | 收益率型资产（us10y, us5y…） |
|------|---------------------------|------------------------------|
| 日变化 | +0.50 % | +3 bp |
| 周变化 | +1.20 % | -5 bp |
| 月变化 | +2.50 % | +12 bp |
| 方向 | ↗（基于周变化） | ↗（基于周变化 bp） |
| 阈值 | ≥0.3% 弱上行 / ≥1.0% 强上行 | ≥3bp 弱上行 / ≥10bp 强上行 |

---

## 12. Checkpoint 与恢复机制 — `tools/graph/checkpoint.py`

**目标**：使 Graph 运行具备可恢复性，避免因中间失败导致全量重跑。

### 12.1 实现方式

基于 LangGraph 内置的 `SqliteSaver`，将每一步图节点完成后的状态快照持久化到 SQLite 数据库：

```python
from langgraph.checkpoint.sqlite import SqliteSaver

def create_checkpoint_saver(checkpoint_path: str) -> SqliteSaver:
    path = Path(checkpoint_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver
```

### 12.2 存储位置

`skill.yaml` 中配置：

```yaml
graph:
  checkpoint_path: ".hermes/graph_checkpoints"  # 相对项目根目录
```

每个运行对应一个 `thread_id = run_id`，同一 `run_id` 多次执行会累积多个 checkpoint 版本。每次运行结束后只保留最近 `graph.checkpoint_keep_last`（默认 20）个线程，更早的由 `prune_checkpoints()` 清理，避免 SQLite 文件无限增长；清理失败只记日志，不影响本次运行。

### 12.3 恢复流程

```python
# runtime.py / run_workflow()
if resume and run_id and checkpoint_path:
    loaded = load_latest_state(checkpoint_path, run_id)
    if loaded:
        state = dict(loaded)          # 加载上次 checkpoint
        state["config"] = config      # 覆盖运行时配置（允许变更）
        state["mode"] = effective_mode
    else:
        state = initial_state(config) # 无 checkpoint → 全新开始
```

```bash
# 新运行（指定 run_id，便于之后恢复）
python -m tools.runtime --config examples/daily_briefing.yaml --run-id my-run

# 恢复上次运行（--resume 必须配相同的 --run-id）
python -m tools.runtime --config examples/daily_briefing.yaml --run-id my-run --resume
```

### 12.4 关键约束

| 约束 | 说明 |
|------|------|
| **run_id 唯一性** | 恢复依赖 run_id，新运行时生成新 UUID |
| **配置可变更** | 恢复后 `config` 被覆盖，允许调整参数后继续 |
| **连接生命周期** | `SqliteSaver` 连接在 `run_workflow()` 返回前由 `_close_saver()` 关闭 |
| **幂等节点** | 同一状态重入时检查 `delivery.email_sent`；跨进程仅在相同 `run_id`、checkpoint 路径和 `resume=True` 时保证恢复后不重发 |
| **不跨越 mode** | 恢复时 mode 被覆盖，不会从 briefing checkpoint 恢复成 fetch |

### 12.5 运行时间预算 — `tools/graph/run_budget.py`

每次运行有一个时间预算，超时只会降级，不会中断：

- 配置 `daily_time_budget_seconds`（默认 900 秒，由 `config_loader` 设定）；预留
  `reserve = min(60, 预算的 10%)` 秒用于收尾。
- 四个可选阶段带预算检查并记录检查点：`rss`（`fetch_feeds`）、`evidence`、`analysis`、`quality`。
  进入收尾预留期（剩余时间不足 `reserve`）后，这些阶段被跳过，并记 `Budget downgrade` 警告。
- 触发后 `degraded: true`，`degradation_reason` 为 `finalization_reserve`（进入预留期）或
  `budget_exhausted`（已耗尽），并记录 `blocked_stage` / `blocked_item`。
- 降级后：质量门直接给出 `budget_limited`，`_should_retry_or_downgrade` 路由到降级，
  简报开头加「Budget-degraded briefing」提示，`write_run_log` 追加一条 `stage_checkpoint_summary`。
- 结果 JSON 带 `run_summary`（`total_seconds`、`reserve_seconds`、`remaining_seconds`、`degraded`、
  `degradation_reason`、`blocked_stage`、`blocked_item`、`checkpoints`），见 `runtime_result.schema.json`。

---

## 13. Hermes 集成

本项目的设计目标之一是作为 **Hermes Agent**（前身为 Nous Research 的 Hermes）的 skill 被调用，通过 cronjob 实现每日自动化简报/趋势表生成。

### 13.1 skill.yaml — 技能入口

`skill.yaml` 定义 Hermes 如何加载和调用本项目：

```yaml
name: EconomicNewsAnalysis
# 版本号唯一来源是 pyproject.toml，运行时由 tools/_version.py 注入，这里不单独维护
runtime:
  language: python
  entrypoint: tools.runtime        # 模块名，用 python -m 运行
defaults:
  mode: briefing
  output_language: zh-CN
  source_config: sources/rss_sources.yaml
  connect_timeout: 10
  read_timeout: 40
  log_path: .hermes/logs/daily-workflow.jsonl
  market_data:
    enabled: false
    provider: yfinance
    source_config: sources/market_sources.yaml
    period: 5d
    interval: 1d
  deliver_email: false
  graph:
    temperature: 0.2
    evidence_enabled: true
    checkpoint_path: ".hermes/graph_checkpoints"
    checkpoint_keep_last: 20
    max_quality_retries: 1
    research_max_items: 5
    country_balance: {CN: 4, US: 4, JP: 4, default: 99}
```

### 13.2 workflows.yaml — 工作流注册

`workflows.yaml` 注册 6 个工作流，每个对应一种 `mode` 和一份示例配置（`example_config`）；
`python -m tools.workflow_smoke` 用这些示例配置对每个工作流做冒烟测试：

```yaml
workflows:
  daily-briefing:
    mode: briefing
    activation: manual
    example_config: examples/daily_briefing.yaml

  daily-email-briefing:
    mode: deliver
    activation: scheduled_or_manual
    example_config: daily_email_briefing.yaml     # 仓库根目录的投递模板

  daily-trend:
    mode: trend
    activation: scheduled_or_manual
    example_config: examples/daily_trend.yaml

  # 另有 news-analysis (analyze)、fetch-only (fetch)、source-check (check)
```

### 13.3 每日自动化 — Hermes Cron

定时投递通过原生插件工具完成（部署步骤见 `docs/HERMES-FIRST-INSTALL.md`，设计见
`docs/daily-email-profile-config-design.md`）：Cron 主 Agent 调用 `run_langgraph_workflow`，
参数是一个封闭枚举，不接受路径、收件人或发送开关：

```json
{"run_mode": "deliver", "profile_config": "daily_email"}
```

- `profile_config: daily_email` 在插件内部映射到仓库根目录的 `daily_email_briefing.yaml`
  （原先的 `examples/daily_email_briefing.yaml` 已有意删除，只保留这一份规范模板）。
- 仓库里的模板不含个人信息且 `email.send: false`；真实收件人与 `send: true` 只在受控的部署副本里补齐。
- `run_mode: deliver` 必须带 `profile_config: daily_email`；其余模式保持手动传入文本的行为，不加载每日配置。
- 模型由 Cron 主 Agent 决定，节点级路由见 §7。
- 命令行入口 `python -m tools.runtime` 仍可用于本地运行，但命令行里没有 Hermes 的 `ctx.llm`，
  LLM 步骤会失败并走降级。每日邮件的编排和真发前检查见 `references/daily_email_agent_workflow.yaml`。

### 13.4 邮件投递 — himalaya CLI / 内置 SMTP

`email_render.render_email_bodies` 生成纯文本正文和完整 HTML 文档（含移动端样式与居中卡片），
两种发信方式共用，发出的邮件内容一致。`email.transport` 选择发信方式：

```python
# email_delivery.send_email
# transport: command（默认）
#   1. build_mml 把正文与附件拼成 MML（himalaya 兼容格式）
#   2. send_mml 把 MML 通过 stdin 交给 email.send_command（默认 himalaya template send），
#      捕获 stderr、受 email.timeout_seconds 限时
# transport: smtp
#   1. smtp_sender.build_message 直接构造 EmailMessage（multipart/alternative + 附件），不经过 MML
#   2. smtp_sender.send_message 用 smtplib 发出（starttls 或 ssl）；
#      密码只从 email.smtp.password_env / password_file 读取，错误信息里不含密码
```

配置细节见 `references/smtp-setup.md`。

### 13.5 邮件主题自动生成

邮件主题由 `deliver_email_node` 自动生成，无需在配置中手动设置：

```
📊 财经消息 日报 2026-06-24
```

- 日期格式 `YYYY-MM-DD`，主题带 📊 前缀（`tools/graph/nodes/delivery.py`，`deliver_email_node`）
- 投递失败通知的主题为 `[失败通知] 财经消息日报 YYYY-MM-DD`（无图标）
- `email_render.py` 的 `build_mml` 只把传入的主题原样写入 `Subject:` 头，不负责生成主题
- 时间基于 **JST（日本标准时间，UTC+9）**
- 日报/周报/月报占位已预留，当前固定为日报
- 覆盖 `email.subject` 配置项

### 13.6 组合邮件 HTML

`email_render.py` 的 `build_combined_email_html()` 函数构建邮件 HTML 正文，结构如下：

```
┌─ 趋势表 ──── 16 个资产（个股不入表），英文名在上中文名在下，单位前置；表尾标注「数据截至」
├─ 简报区 ──── 条件区域（📰 今日重点新闻），有新闻且非重复时才显示。板块顺序：
│              今日资讯主线 → 📉 异动解读（条件）→ 🌏 官媒宏观背景（中美日）→ 🌐 财经媒体 →
│              💡 科技与AI → 🔍 Google News 快讯 → 🔥 热点速览 → 市场影响地图 …
└─ 页脚 ────── 由 Economic News Analysis v{version} 自动生成
```

**质量门控**：`trend` mode 不经过质量门；`briefing`/`deliver` mode 经过质量门，不通过时先重试、超限后降级
继续——**不会阻断投递**，`pipeline_status` 记为 `partial_success`，简报里带「部分成功」提示（见 §5）。

**去重逻辑**：`write_run_log_node` 将本次新闻条目的 URL 列表写入 `item_urls` 字段。下次运行时对比，若全部重复则跳过简报区，仅发趋势表。

---

## 14. 术语表

| 缩略词 | 全称 | 说明 |
|--------|------|------|
| **MML** | Morning Market Letter | 每日市场简报邮件格式，himalaya CLI 兼容 |
| **RSS** | Really Simple Syndication | 新闻源抓取协议（本项目的核心数据源） |
| **LLM** | Large Language Model | 用于信号提取和分析；模型由 Cron 主 Agent 或 plugin 节点路由决定 |
| **LangGraph** | LangChain StateGraph | 本项目的图执行引擎，管理节点拓扑和状态流转 |
| **StateGraph** | 状态图 | LangGraph 的核心抽象，节点 + 条件路由 + 状态 TypedDict |
| **Checkpoint** | 检查点 | SqliteSaver 对图状态的快照，支持运行恢复 |
| **Quality Gate** | 质量门控 | `graph/nodes/quality.py`：先补全缺失的信号、主线、市场影响地图，仍不合格则重试或降级（见 §5） |
| **Trend Mode** | 趋势表模式 | 不调用 RSS/LLM 的纯市场数据输出模式 |
| **Yahoo Ticker** | — | yfinance 识别的资产标识符（如 `^GSPC` 对应 S&P 500） |
