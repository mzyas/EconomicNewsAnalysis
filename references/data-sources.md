# RSS / API 数据源策略

## 总体原则

新闻来源按三层使用，不把所有来源混成同一种可信度：

1. 官方源：确认事实基准。
2. 交易级聚合源：发现事件、补充市场解读。
3. 情绪源：观察热度与噪音，不作为事实基准。

稳定链路优先于来源数量：

```text
Source -> Category -> Event -> Asset -> Direction -> ImpactScore
```

除新闻流外，项目还应允许引入“背景数据层”：

```text
BackgroundData -> Regime -> Context -> Risk
```

这类来源用于补充利率路径、波动率、资金流和监管背景，不作为新闻事件主入口，也不与 RSS 新闻混排。

## 当前已接入来源

可执行配置以 `sources/rss_sources.yaml` 为准。现阶段已接入的来源主要覆盖：

| 层级 | 来源组 | 当前状态 | 用途 |
|---|---|---|---|
| 官方源 | Federal Reserve / ECB / BOJ / PBoC / NBS / Japan FSA | 已接入 | 央行、统计、监管事实确认 |
| 官方/API | World Bank News API | 已接入 | 全球宏观新闻补充 |
| 聚合源 | Central Banking | 已接入 | 央行与金融稳定主题补充 |
| 聚合源 | Google News RSS（中/英/日） | 已接入 | BOJ、Fed、ECB、黄金、美债、房地产等事件发现 |
| 事件监测 | GDELT API | 已接入但默认跳过 | 全局事件与趋势监控 |
| 官方页面 | 日本财务省 MOF | 已接入为 `official_page` | 外汇干预、JGB、财政页面，需手动核验 |
| 科技媒体 | TechCrunch / The Verge / Ars Technica / MIT Tech Review / VentureBeat AI | 已接入（`tech_media`） | 科技与AI 板块 |
| 科技搜索 | Google News：artificial intelligence / 人工智能 大模型 / semiconductor | 已接入（`tech_search`） | 科技与AI 板块事件发现 |
| 综合头条 | Google News Top Stories（EN / ZH） | 已接入（`hot_search`） | 热点速览板块（财经/科技/AI 以外的高热条目） |

> 首次真实运行后需留意上述新增 feed 是否有拉空的；管线对单源失败「跳过即可」，不影响其他源。

## 板块与 category 映射

简报板块由来源 `category` 决定（在 `tools/briefing_builder.py:_item_category` 判定，先于 Google-News 兜底）：

| category | 归入板块 | 说明 |
|---|---|---|
| `central_bank` / `statistics` / 政府 / 国际组织 等 | 🌏 官媒宏观背景（中美日） | 官方/政府/央行条目按国别归并为中美日三段叙述，不再逐条铺表 |
| `news_media` | 🌐 财经媒体新闻 | 财经媒体逐条表格 |
| `tech_media` / `tech_search` | 💡 科技与AI | 专门科技源 + Google 科技/AI 关键词 |
| `search_rss`（其它 Google 关键词） | 🔍 Google News 快讯 | 宏观/财经关键词发现 |
| `hot_search` | 🔥 热点速览 | Google 综合头条 trending；渲染时按关键词排除财经/科技/AI 条目，只留「其它高热」 |

## 当前优先覆盖的来源域

### 1. 宏观数据：`MACRO_DATA`

已覆盖：

- 美国：Fed 相关发布
- 日本：BOJ、Japan FSA
- 中国：NBS、PBoC
- 全球：World Bank、IMF、Central Banking

待补强但**未在本次直接写入配置**：

- 美国：BLS、BEA、FRED、New York Fed Economic Calendar
- 日本：e-Stat、Cabinet Office、METI
- 中国：财政部、海关总署

这些来源在参考文档中价值很高，但本仓库当前没有经过逐条 RSS/API 端点确认。确认后再进入 `sources/rss_sources.yaml`。

### 2. 央行政策：`CENTRAL_BANK`

已覆盖：

- Federal Reserve：Press All、FOMC、Speeches
- ECB：Press Releases
- BOJ：What's New
- PBoC：News Releases
- Central Banking：Monetary / Economics / Financial Stability

待补强：

- New York Fed
- 各地区联储
- BIS 结构化利率与金融稳定数据
- SAFE 外汇与资本流数据

### 3. 财政政策：`FISCAL_POLICY`

已覆盖有限：

- 日本财务省 MOF（页面型来源）

待确认后接入：

- U.S. Treasury Press Releases
- U.S. Treasury FiscalData API
- Congress.gov
- White House
- CBO
- 中国财政部
- 中国发改委 NDRC
- 国务院 / 中国政府网

其中：

- `U.S. Treasury Press Releases` 更适合作为页面型或 RSS 候选的政策/公告来源。
- `U.S. Treasury FiscalData API` 是结构化数据源，适合汇率、国债、收支等数据抓取，不应与新闻流混用。

### 4. 地缘政治：`GEOPOLITICS`

当前主要通过：

- Google News RSS
- Central Banking
- GDELT

参考文档建议的 Reuters、Bloomberg、Financial Times、NHK、Al Jazeera、BBC 等更适合作为第二层聚合源，但 RSS 或授权方式需要逐一确认。

### 5. 监管政策：`REGULATION`

已覆盖：

- Japan FSA

待确认后接入：

- SEC
- CFTC
- FDA
- FTC / DOJ
- Commerce Department / BIS
- CSRC
- SSE / SZSE / BSE
- HKEX
- 中国国家金融监督管理总局
- CAC
- SAMR

### 6. 企业财报与公告：`EARNINGS`

当前未作为一等来源正式接入。

建议优先候选：

- 美股：SEC EDGAR、公司 IR、Nasdaq / NYSE Earnings Calendar
- 日股：TDnet、EDINET、JPX
- A股 / 港股：上交所、深交所、巨潮资讯、HKEX 披露易

这些来源非常关键，但链接、RSS 或抓取模式需要单独确认，尤其是公告站点常见 HTML、PDF 或 JS 页面。

### 7. 商品供需：`COMMODITY`

当前未正式接入。

建议优先候选：

- EIA
- OPEC / OPEC+
- IEA
- Baker Hughes
- USDA
- LME / COMEX / CME / SHFE / DCE / CZCE

### 8. 汇率与资本流：`FX_CAPITAL_FLOW`

已部分覆盖：

- PBoC
- BOJ
- MOF
- Google News 中的 USD/JPY、Fed、Treasury、Gold 主题搜索

待确认后接入：

- SAFE
- 港交所南北向资金
- 中债登 / 上清所
- TIC
- FRED FX 数据

### 9. 金融风险：`FINANCIAL_RISK`

已部分覆盖：

- Federal Reserve / ECB / BOJ 金融稳定相关发布
- Central Banking 金融稳定 RSS
- Japan FSA
- Google News 中的 Treasury、房地产、黄金和风险偏好主题搜索

待确认后接入：

- FDIC / OCC / FSOC
- BIS / FSB / IOSCO
- 中国国家金融监督管理总局
- 交易所风险提示和信用违约公告
- 回购、货币基金、商业地产和银行资产质量数据

### 10. 科技与产业趋势：`TECH_INDUSTRY`

已接入（独立成「💡 科技与AI」板块，按来源分类）：

- 科技媒体：TechCrunch、The Verge、Ars Technica、MIT Technology Review、VentureBeat AI（`tech_media`）
- 科技搜索：Google News「artificial intelligence」「人工智能 大模型」「semiconductor chip Nvidia」（`tech_search`）

已部分覆盖（仍走宏观/财经板块）：

- METI Statistics RSS
- SEC EDGAR / CNINFO / JPX 相关企业公告候选源
- Google News 中的中国经济、日本经济和行业相关关键词

待确认后接入：

- Commerce Department / BIS 出口管制
- MIIT / NDRC / 科技部 / 网信办
- FDA / DOE / NIST / USPTO
- 半导体、汽车、机器人、能源和医药行业协会
- 企业 IR、资本开支、订单和产能公告

### 11. 灾害与突发事件：`DISASTER_EVENT`

已部分覆盖：

- Cabinet Office RSS 中的日本政府突发公告
- Google News 中的能源、地缘和供应链主题搜索

待确认后接入：

- NOAA / USGS / FEMA / NHC / CDC / FAA / NTSB
- Japan Meteorological Agency / Fire and Disaster Management Agency
- WHO / UN OCHA / GDACS / ReliefWeb
- 港口、航运、电力、矿难、工厂事故和网络攻击官方通报

### 12. 市场结构与资金流：`MARKET_STRUCTURE`

已部分覆盖：

- New York Fed Markets API 候选背景源
- FINRA API 候选背景源
- Google News 中的 Treasury、Gold、USD/JPY 和市场波动主题搜索

待确认后接入：

- CFTC COT
- CME / CBOE
- NYSE / Nasdaq / JPX / HKEX 交易与披露数据
- ETF flow、期权持仓、卖空、融资融券、指数调仓和回购窗口数据

### 13. 市场背景数据：`MARKET_BACKGROUND`

这类来源不以“新闻”进入抓取主链，而是作为分析背景输入：

- 利率预期/概率：`CME FedWatch`
- 波动率/风险偏好：`CBOE VIX`
- 监管/市场结构数据：`FINRA`
- 日本披露系统/结构化公告接口：`EDINET`
- 能源供需与产量背景：`OPEC`

使用原则：

- 不与新闻源共用 `max_items_per_source` 和新闻排序逻辑。
- 默认作为背景上下文，用于解释市场定价、风险偏好和制度约束。
- 只有当其自身发布明确公告或报告时，才可作为独立事件来源。

## 待用户确认的来源

下列来源在参考文档中优先级高，但我没有在本地直接确认稳定的 RSS/API/抓取入口，因此本次不写入运行配置：

- `BLS`
- `BEA`
- `FRED / St. Louis Fed`：参考文档建议使用，但项目历史记录里 RSS 已确认失效；若改用 API，应单独设计
- `New York Fed Economic Calendar`
- `Cabinet Office`
- `METI`
- `中国财政部`
- `海关总署`
- `SAFE`
- `U.S. Treasury Press Releases`
- `U.S. Treasury FiscalData API`
- `Congress.gov`
- `White House`
- `CBO`
- `SEC EDGAR`
- `TDnet`
- `EDINET`
- `JPX`
- `巨潮资讯`
- `HKEX 披露易`
- `EIA`
- `OPEC`
- `USDA`
- `CME FedWatch`
- `CBOE VIX`
- `FINRA`

其中下列来源即使后续确认可访问，也应优先按“背景数据层”接入，而不是新闻 RSS：

- `CME FedWatch`
- `CBOE VIX`
- `FINRA`
- `EDINET`
- `OPEC`

## 来源类型建议

参考文档里的抽象很适合纳入 Skill 规则。建议在项目内部统一使用：

```yaml
SourceType:
  OFFICIAL_DATA: 官方数据
  CENTRAL_BANK: 央行
  REGULATOR: 监管机构
  EXCHANGE_DISCLOSURE: 交易所 / 上市公司公告
  NEWS_WIRE: 通讯社
  MEDIA: 财经媒体
  MARKET_DATA: 市场行情 / 衍生品
  SOCIAL: 社交情绪
  RESEARCH: 机构研究 / 产业数据
```

当前 `rss_sources.yaml` 仍沿用已有 `category` 字段，不在本次强行改动运行配置。等链接确认完成后，再统一映射或升级 schema。

## 标签与用途

当前项目仍以主题标签驱动分类与排序。按参考文档，后续标签可以按六大重点域组织：

```text
CENTRAL_BANK
MACRO_DATA
EARNINGS
REGULATION
COMMODITY
FX_CAPITAL_FLOW
```

并继续保留现有细粒度标签，例如：

```text
monetary_policy
inflation
employment
gdp_growth
financial_stability
fx_intervention
real_estate
commodities
risk_sentiment
```

## 使用策略

- 官方源决定事实。
- 通讯社和聚合源决定速度与事件发现。
- 财经媒体负责解释、背景和市场反应。
- 市场数据源负责补充定价背景、风险状态和制度环境，不替代新闻事实来源。
- 情绪源只做热度和主题扩散参考，不做事实基准。

第一版或当前版本不追求“全来源覆盖”，而是优先保证：

1. 来源稳定。
2. 分类稳定。
3. 事件时间准确。
4. 资产映射可解释。
5. 排序与 ImpactScore 可回看。
