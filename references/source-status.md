# 数据源状态与诊断日志

版本: 0.5.7
最后验证：2026-06-08。27 源 0 失败。修复 MML filename 引号、附件改为 cp 重命名方案。

## 变更记录

### 0.5.7 (2026-06-21)
- 日报主线采用“结论标题 — 数据、传导与必要限定”格式；邮件与 Markdown 会将结论标题加粗，避免主线过度省略。
- 新闻内容改为“加粗标题；来源摘要；来源与发布页”，邮件表格将更多列宽分配给新闻内容，其余字段保持紧凑。

### 0.5.6 (2026-06-21)
- 日报主线 headline 最低长度提升至 20 字，要求说明宏观含义而非仅拼接标题。
- 新增可选 `daily.weekly_watchlist`；缺失时从已核验新闻的核心信号自动生成本周验证项，移除空占位文本。

### 0.5.5 (2026-06-21)
- enrichment 补丁新增受限 `daily.mainlines`；邮件主线改为渲染 Agent 提供、并绑定前五已核验候选新闻的完整宏观判断。
- MML 质量门槛新增日报主线存在性与引用范围校验，禁止发送标题拼接式的过度省略主线。

### 0.5.4 (2026-06-21)
- MML 生成新增质量门槛：已核验新闻的核心信号、前五主线候选的主题确认及日报级市场影响地图不完整时，拒绝生成可发送邮件。
- enrichment 补丁新增受限的 `daily.market_impact` 五字段结构；日报市场影响地图改为渲染该结构化内容。

### 0.5.3 (2026-06-21)
- 修复关注资产字段：全局 `focus_assets` 仅参与排序，不再复制到每条新闻；Agent 可通过受限 enrichment 补丁按条目补充资产和主题。
- 未核验 Google News 长链接改标为“Google News 发现链接”；纯文本邮件移除 Markdown 链接目标，避免显示长 URL。
- 新增 `references/daily_email_agent_workflow.yaml`，规定 Runtime 草稿、Agent 富化、合并渲染和 MML 发送的强制顺序。

### 0.5.2 (2026-06-21)
- 撤销长正文摘要与标题超链接方案；日报摘要保持 180 字上限，标题仅显示文本。
- 新闻内容统一为单行分隔格式，不使用 `<br>`；发布页仅以独立“发布页”链接呈现。
- 地区字段统一映射为简体中文，新增英国地区映射。

### 0.5.1 (2026-06-21)
- 每日简报改为由固定报告模型同步渲染 Markdown 附件与 Email HTML；新闻标题使用可点击链接，表格增加固定列宽与长文本换行规则。
- 今日宏观主线从最多 5 条高重要性新闻按“地区＋主题”聚合后动态生成，不再固定三条。
- Google News 发现条目会尝试跟随链接并抽取发布页摘要；失败条目保留在快讯区并明确标记“原文未核验”。
- 新增受限结构化 enrichment 补丁，仅允许按 `news_item_id` 更新翻译标题、摘要、核心信号和关注资产；来源字段不可被外部补丁覆盖。

### 0.5.0 (2026-06-21)
- 新增固定 `tools/enrichment.py`，通过 JSON 翻译负载按 `news_item_id` 应用 `translated_title`。
- enrichment 脚本可重建已有每日简报，并支持单独输出 Markdown；不执行翻译文本或临时生成 Python 代码。
- 未知 `news_item_id` 会显式失败，防止将翻译应用到错误新闻。

### 0.4.3 (2026-06-21)
- 在 Skill 流程中新增结构化文本传递规则：`translated_title` 和其他外部文本必须通过 JSON 或变量传递。
- 禁止将外部文本直接拼接进 Python 赋值源码；生成 JSON 或字符串字面量时使用 `json.dumps(..., ensure_ascii=False)`。

### 0.4.2 (2026-06-21)
- 为标题级 Google News 条目新增可选 `translated_title` 契约。
- Google News 快讯优先显示 Agent 提供的忠实中文标题；缺失时保留原文并标记“待 Agent 忠实翻译”，不基于训练数据补全标题事实。

### 0.4.1 (2026-06-21)
- 每日简报将 `google_news_` 发现来源从主新闻表中分离到“Google News 快讯”专区，仅展示地区、标题和原始来源链接。
- Google News 快讯明确标记为标题级、来源不足且未经核验；不展示来源摘要、核心信号或资产分析。
- 新增美国通胀、就业、GDP，日本通胀、GDP，欧元区及英国的 7 个 Google News 搜索源。

### 0.4.0 (2026-06-17)
- 新增 Market Data Provider 架构：统一模型、Provider 接口、registry、loader 和 `yfinance` provider。
- 新增 `sources/market_sources.yaml` 与 `schemas/market_sources.schema.json`。
- Runtime 新增默认关闭的 `market_data` 配置，并在结果中输出 `market_context`。
- 市场数据仅作为背景上下文，不进入新闻抓取、新闻排序或事实来源判断；provider 失败不阻断主流程。

### 0.3.12 (2026-06-16)
- 新增 `pyproject.toml` 作为项目依赖管理入口。
- 将 `pandas` 与 `yfinance` 登记为项目依赖，为后续 Market Data Provider 实现做准备。

### 0.3.11 (2026-06-16)
- 对照 `asset_impact_primary_sources.md` 补齐影响因子缺口：`FINANCIAL_RISK`、`TECH_INDUSTRY`、`DISASTER_EVENT`、`MARKET_STRUCTURE`。
- 每日简报的“市场影响地图”新增“金融风险与市场结构”维度，覆盖信用、流动性、杠杆、持仓、波动率、资金流和指数调仓。
- 周报、月报、季度报模板补充金融风险、市场结构、资金流和灾害突发事件的长线传导观察。

### 0.3.10 (2026-06-16)
- 在 `references/output-templates.md` 中新增周报、月报、季度报和年报模板。
- 各周期模板统一加入“观察因子的长线影响”板块，用于跟踪增长、通胀、政策、行业、地缘和供应链等因素对未来多个季度或年度的传导。

### 0.3.9 (2026-06-16)
- 每日宏观简报的“市场影响地图”新增“非经济成分与市场传导”维度。
- 明确要求分析政治、监管、地缘、安全、供应链、自然灾害和公共卫生等非经济事件对政策预期、风险偏好、行业盈利和资产定价的间接影响。

### 0.3.8 (2026-06-16)
- 将 `Cabinet Office`、`METI`、`CBO`、`USDA` 系列 RSS 加入 `sources/rss_sources.yaml`，纳入自动抓取来源。
- 将 `New York Fed Markets API` 作为 `api` 类型背景源加入配置清单，当前不参与自动 RSS 抓取。
- 每日简报默认覆盖的自动源数量随之从 27 提升到 33。

### 0.3.7 (2026-06-16)
- 调整 API 来源配置说明：`BLS`、`FRED API`、`Congress.gov` 保留在可执行配置中作为候选 `api` 源，但本轮不做抓取实现。
- 当前登记的候选 API 源包括 `BLS`、`BEA`、`FRED API`、`Congress.gov`、`SEC EDGAR`、`EIA`、`FINRA`、`巨潮资讯`。

### 0.3.6 (2026-06-16)
- 将 `BLS`、`BEA`、`FRED API`、`Congress.gov`、`SEC EDGAR`、`EIA`、`FINRA` 和 `巨潮资讯` 作为 `api` 类型来源加入 `sources/rss_sources.yaml`。
- 这些来源当前仅进入配置清单与来源库存，默认不参与 RSS 自动抓取；后续需单独实现 API 拉取或背景数据接入逻辑。

### 0.3.5 (2026-06-16)
- Runtime 新增 `env_file` 配置入口，用于 WSL、Hermes 和 cron 从仓库外部 env 文件加载密钥。
- env 文件支持 `KEY=value` 和 `export KEY=value`，且不会覆盖已存在的进程环境变量。

### 0.3.4 (2026-06-16)
- 按资产影响新闻来源参考文档重写来源分层说明，明确官方源、交易级聚合源和情绪源的职责。
- 扩展结构拆解方式，加入 `Source -> Category -> Event -> Asset -> Direction -> ImpactScore -> Risk` 主链。
- 在数据源文档中新增待确认来源清单；未核实的 RSS/API/页面入口暂不写入运行配置。
- 将 `U.S. Treasury` 拆分为 `Press Releases` 与 `FiscalData API` 两类候选来源，区分页面型公告与结构化数据接口。

### 0.3.3 (2026-06-13)
- 每日简报默认排名上限从 12 条提高到 20 条，并为达到最低相关性门槛的 Google News 市场发现源保留最多 4 个席位。
- 新增来源上下文质量判断，薄弱摘要降权并标记为 `insufficient_source_context`。
- 恢复 001/002 issue，并记录 003/004 的修复状态和回归测试。

### 0.3.2 (2026-06-13)
- 将每日简报的市场影响地图升级为 CFA/投行研究口径。
- 增加利率曲线、行业与企业盈利、商品供需、跨资产风险暴露和情景分析要求。
- 明确资产配置仅讨论非个性化配置含义，不提供仓位比例、交易指令或收益承诺。

### 0.3.1 (2026-06-13)
- 修复回顾性官方稿件因标签和来源加分挤占当期宏观数据的问题。
- 排序增加当期数据/政策信号加分，以及回顾性、总结性内容降权。
- 每日简报默认不再使用主题 tags 过滤来源，覆盖全部 27 个自动源和 13 个 Google News 搜索源。

### 0.3.0 (2026-06-13)
- 新增 `workflows.yaml`，集中登记用户可见的 Workflow、触发方式和 Runtime mode。
- 区分本地每日简报、Email 每日简报、主动新闻解读、仅抓取和来源检查流程。
- 新增 Workflow Schema、邮件简报与来源检查示例配置及注册表测试。

### 0.2.2 (2026-06-12)
- 在 `SKILL.md` 增加来源依据护栏。
- 要求最终事实可追溯至 `source_context` 或任务中明确核验的来源。
- 来源不可访问或内容不足时，明确披露限制并删除无依据的事件、数字和市场结论。

### 0.2.1 (2026-06-12)
- 修复 briefing enrichment 来源上下文丢失问题。
- 保留完整 RSS 来源摘要，并在分析结果中新增 `source_context` 和 `grounding_status`。
- 未经核实的来源摘要不再写入 `facts`，简报新闻列附带标题、来源摘要、来源和链接。

### 0.2.0 (2026-06-10)
- 重构为 Runtime Skill：新增统一 `tools/runtime.py` 入口和多运行模式。
- 新增配置、标准新闻对象、分析结果和运行结果 Schema。
- 新增离线测试、示例配置、相关性排序、简报渲染和可选邮件投递模块。
- 默认输出语言设为简体中文，专有名词无可靠中文译名时保留英文。
- 新增 `skill.yaml`，统一管理 Skill 版本和默认输出配置。

### 0.1.3 (2026-06-06)
- 新增 6 个 Google News 搜索源（伊朗战争/能源、ECB、美债、黄金、中国房地产、ドル円為替介入），总计 13 个
- 新增 `scripts/check_log.py`：抓取日志检查脚本，支持按天窗口判断是否需要抓取
- `fetch_feeds.py` 新增 `--log` 参数，每次抓取追加 JSONL 日志
- 新增 `web_extract` DuckDuckGo 后端限制的 pitfall 文档
- 配置了两个 cron（10:00 抓取+简报+邮件，14:00 检查补抓）
- himalaya 邮件发送集成

### 0.1.2 (2026-06-06)
- BOJ/PBoC/NBS×2/FSA 从 `official_page` 升级为 RSS
- 新增 World Bank JSON API 自定义抓取器
- gzip 魔数检测、`pubTime` 字段支持、PBoC URL 标准化
- 移除 St. Louis Fed / World Bank RSS / Nikkei Asia（无法修复）
- Google News 表格扩展：每行标注关键词、时间范围、关注内容

## 活跃数据源（自动抓取）

### 央行官方

| ID | 中文名称 | 来源 | 类型 | URL | 备注 |
|---|---|---|---|---|---|
| fed_press_all | 美联储 - 所有新闻稿 | Federal Reserve | RSS | `federalreserve.gov/feeds/press_all.xml` | |
| fed_fomc | 美联储 - FOMC 声明 | Federal Reserve | RSS | `federalreserve.gov/feeds/press_monetary.xml` | 利率决议 + 会议纪要 |
| fed_speeches | 美联储 - 官员讲话 | Federal Reserve | RSS | `federalreserve.gov/feeds/speeches.xml` | Powell、Bowman 等讲话原文 |
| ecb_rss | 欧洲央行 - 新闻稿 | ECB | RSS | `ecb.europa.eu/rss/press.html` | 原 URL `home/html/rss.en.html` 为 HTML 页面，已修正 |
| boj_rss | 日本央行 - 最新信息 | BOJ | RSS | `boj.or.jp/en/rss/whatsnew.xml` | 含统计数据（Call Market、消费活动指数等） |
| pboc_rss | 中国人民银行 - 新闻发布 | PBoC | RSS | `pbc.gov.cn/goutongjiaoliu/113456/2986536/index.html` | 中文。链接缺 `https://`（脚本自动补全）。日期格式 `4 Jun 2026 16:00:00 GMT` |
| nbs_releases | 国家统计局 - 数据发布 | NBS | RSS | `stats.gov.cn/sj/zxfb/rss.xml` | 中文。日期字段为 `<pubTime>`（非标准 `<pubDate>`）。含 PMI、工业企业利润、生产资料价格等 |
| nbs_analysis | 国家统计局 - 数据解读 | NBS | RSS | `stats.gov.cn/sj/sjjd/rss.xml` | 中文。含"十四五"系列报告、居民收支、固定资产投资等深度解读 |
| fsa_rss | 日本金融厅 - 新闻 | FSA | RSS | `fsa.go.jp/fsaEnNewsList_rss2.xml` | 含金融担当大臣记者会见、讲话等 |
| central_banking_monetary | Central Banking - 货币政策 | Central Banking | RSS | `centralbanking.com/feeds/rss/category/central-banks/monetary-policy` | Gzip 压缩。全球央行货币政策动态 |
| central_banking_economics | Central Banking - 经济学 | Central Banking | RSS | `centralbanking.com/feeds/rss/category/central-banks/economics` | Gzip 压缩。宏观经济、数据、建模 |
| central_banking_finstab | Central Banking - 金融稳定 | Central Banking | RSS | `centralbanking.com/feeds/rss/category/central-banks/financial-stability` | Gzip 压缩。宏观审慎、微观监管 |

### 国际组织

| ID | 中文名称 | 来源 | 类型 | URL | 备注 |
|---|---|---|---|---|---|
| imf_blog | 国际货币基金组织 - 博客 | IMF | RSS | `imf.org/en/News/RSS` | 全球宏观、财政、债务分析 |
| world_bank_news | 世界银行 - 新闻 | World Bank | worldbank_api | `search.worldbank.org/api/v2/news` | 自定义 JSON→RSS 转换。过滤：仅英语、`/news/` 含于 URL、从 URL 路径提取日期 |

### Google News 关键词搜索 (7 组)

| ID | 中文名称 | 语言 | 搜索关键词 | 时间范围 | 关注内容 |
|---|---|---|---|---|---|
| google_news_china_economy | 中国经济动态 | EN | `China economy` | 过去 1 天 | 英文媒体对中国经济的报道：贸易、GDP、政策 |
| google_news_japan_inflation_boj | 日本通胀与央行 | EN | `Japan inflation BOJ` | 过去 7 天 | 英文媒体对日本通胀、BOJ 政策路径的报道 |
| google_news_fed_rate | 美联储利率与通胀 | EN | `Fed rate cut inflation` | 过去 1 天 | 美联储降息/加息预期、通胀数据相关英文报道 |
| google_news_cn_econ | 中国经济央行 | ZH | `中国经济 央行` | 过去 1 天 | 中文媒体对央行政策、经济形势的报道 |
| google_news_cn_cpi_ppi_gdp | 中国宏观数据 | ZH | `中国 CPI PPI GDP` | 过去 7 天 | 中文媒体对通胀、增长等核心指标的报道与分析 |
| google_news_jp_econ_boj | 日本经济日银 | JA | `日本経済 日銀` | 过去 1 天 | 日文媒体对日本经济、BOJ 政策的第一手报道 |
| google_news_jp_inflation_wages | 日本通胀工资日银 | JA | `日本 インフレ 賃金 日銀` | 过去 7 天 | 日文媒体对通胀、工资增长、BOJ 加息预期的深度分析 |
| google_news_iran_oil | 伊朗战争能源冲击 | EN | `Iran war oil energy impact economy` | 过去 7 天 | 伊朗战争对全球能源供给、油价、经济的冲击分析 |
| google_news_ecb | 欧洲央行加息与通胀 | EN | `ECB rate hike inflation eurozone economy` | 过去 7 天 | 欧元区通胀数据、ECB 加息路径的英文媒体报道 |
| google_news_treasury | 美债收益率与债市 | EN | `treasury yields bond market US debt` | 过去 7 天 | 美国国债收益率、债市走势、财政债务动态 |
| google_news_gold | 黄金避险需求 | EN | `gold price safe haven` | 过去 7 天 | 黄金价格、避险需求、实际利率影响分析 |
| google_news_cn_property | 中国房地产楼市 | ZH | `中国 房地产 楼市` | 过去 7 天 | 中国房地产市场动态、政策调控、房企风险 |
| google_news_usdjpy_intervention | 美元日元汇率干预 | JA | `ドル円 為替介入` | 过去 7 天 | USDJPY 汇率走势、日本汇市干预风险、日银政策影响 |

## 手动数据源（默认跳过）

| ID | 中文名称 | 来源 | 类型 | 原因 |
|---|---|---|---|---|
| mof_japan | 日本财务省 | MOF | official_page | 页面为 JS 动态渲染，无 RSS 端点。需手动访问 `mof.go.jp/english/public_relations/whats_new/202606.html` |
| gdelt_economic | GDELT 全球事件监测 | GDELT | api | 需 API key 和专用集成 |

## 已移除的数据源（无法修复）

| 来源 | 原因 |
|---|---|
| St. Louis Fed Research | `/rss` 为 HTML 目录页，无 RSS XML feed |
| World Bank News (RSS) | 所有 RSS 端点返回 404，已用 JSON API 替代 |
| Nikkei Asia RSS | 全路径 403，需付费订阅 |

## 脚本修复记录

### Gzip 解压
服务器发送 `Content-Encoding: gzip`，但 `urllib.request` 不会自动解压。修复：检测 gzip 魔数（`1f 8b`）而非信赖 `Content-Encoding` 头——部分服务器不发送该头或在传输中被剥离。

### 日期字段兼容
标准 RSS 使用 `<pubDate>`，NBS 使用 `<pubTime>`。脚本同时检查两者，外加 Atom 的 `<published>` 和 `<updated>`。

### 日期格式补充
新增 `%d %b %Y %H:%M:%S %Z` 格式，支持 PBoC 的 `4 Jun 2026 16:00:00 GMT`。

### URL 标准化
PBoC 链接缺 `https://` 前缀（如 `www.pbc.gov.cn/...`）。脚本在链接以 `www.` 开头时自动补全。

### World Bank JSON API 噪音过滤
API 返回约 60% 非新闻条目（招聘页、静态页面、非英语内容）。过滤策略：要求 `/news/` 含于 URL 且 `/en/` 含于 cqpath 或 URL。每次抓取 `max(max_items*5, 30)` 行以确保有足够新闻条目幸存。

### World Bank URL 日期提取
World Bank URL 内嵌日期如 `/news/press-release/2026/06/05/...`。正则需用 `[\\w-]+` 而非 `\\w+`，因为内容类型含连字符（如 `press-release`）。

## 故障排查清单

当数据源抓取失败时：
1. `curl -sI` 检查 HTTP 状态码（404=URL 错误，403=认证/付费墙，200=检查 Content-Type）
2. `curl -sL | head -20` 检查内容是 XML 还是 HTML
3. 如果是 HTML：在页面中搜索真实 RSS 端点
4. 如果是 XML 但解析失败：检查 gzip、CDATA、非标准字段名
5. 如果是 JS 渲染：保留为 `official_page` 类型
