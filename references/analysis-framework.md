# 经济新闻分析框架

## 核心链条

```text
新闻 -> 经济信号 -> 政策预期 -> 资产影响 -> 风险提示
```

经济新闻分析不能只总结新闻。重点是解释新闻如何改变增长、通胀、利率、流动性、风险偏好和资产定价预期。

## 结构化字段

```json
{
  "title": "新闻标题",
  "source": "来源",
  "published_at": "发布时间",
  "region": ["US", "Japan"],
  "topic": ["inflation", "central_bank", "fx"],
  "entities": ["Fed", "BOJ", "USDJPY"],
  "economic_indicators": ["CPI", "NFP", "GDP"],
  "event_type": "policy_signal",
  "sentiment": "risk_off",
  "time_horizon": "short_term"
}
```

## 结构拆解主链

参考文档里的推荐链路更适合当前 Skill 扩展后的覆盖面。统一拆解为：

```text
Source -> Category -> Event -> Asset -> Direction -> ImpactScore -> Risk
```

其中：

- `Source`：来源类型与可信度层级
- `Category`：宏观数据、央行、财报、监管、商品、汇率资本流等
- `Event`：具体事件与实体
- `Asset`：受影响资产与行业
- `Direction`：偏利多 / 偏利空 / 混合 / 不明确
- `ImpactScore`：影响强度与可交易关注度
- `Risk`：反向风险、假设和失效条件

## 来源层级

```text
official_release > news_wire > media_summary > social_signal
```

解释：

- `official_release`：决定事实基准
- `news_wire`：决定速度和突发提醒
- `media_summary`：负责解释和背景补充
- `social_signal`：只做情绪和热度参考

## 分类维度

地区：美国、日本、中国、欧元区、全球。

主题：通胀、利率、就业、GDP、财政、贸易、地缘政治、企业盈利、能源、房地产、金融稳定、信用风险、科技产业、供应链、监管政策、灾害突发、资本流、市场结构。

资产：股票、债券、汇率、黄金、原油、加密资产。

事件类型：数据发布、央行讲话、政策变动、战争冲突、企业财报、市场波动、监管变化、金融风险、产业事件、灾害事故。

建议优先覆盖的主分类：

```text
CENTRAL_BANK
MACRO_DATA
EARNINGS
REGULATION
COMMODITY
FX_CAPITAL_FLOW
FISCAL_POLICY
GEOPOLITICS
FINANCIAL_RISK
TECH_INDUSTRY
DISASTER_EVENT
MARKET_STRUCTURE
```

## 建议的来源与事件字段

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

```yaml
NewsItem:
  source_name: string
  source_type: SourceType
  country_region: string
  asset_scope: list[string]
  news_category: string
  event_time: datetime
  release_time: datetime
  importance_score: float
  surprise_score: float
  affected_assets: list[string]
  direction: bullish | bearish | mixed | unclear
  confidence: float
  official_url: string | null
  summary_cn: string
  raw_title: string
  raw_text: string
```

这些字段是结构拆解建议，不是当前 Runtime 已强制落地的 schema。

## 常用影响路径

### 通胀高于预期

```text
CPI/PCE/PPI 高于预期
-> 通胀粘性上升
-> 降息预期下降或加息预期上升
-> 债券收益率上行
-> 本币偏强
-> 黄金和高估值股票承压
```

### 就业强于预期

```text
就业/薪资强于预期
-> 需求和工资通胀韧性增强
-> 央行宽松空间下降
-> 收益率偏上行
-> 本币偏强
-> 股市反应取决于增长利好和利率压力的相对强弱
```

### 央行偏鹰

```text
央行声明/讲话偏鹰
-> 市场上调政策利率路径
-> 短端收益率上行
-> 本币偏强
-> 风险资产估值受压
```

### 央行偏鸽

```text
央行声明/讲话偏鸽
-> 市场下调政策利率路径
-> 收益率下行
-> 本币承压
-> 黄金和风险资产可能受益
```

### 日本与日元

```text
BOJ 加息或工资/通胀支持正常化
-> 日债收益率上行
-> USD/JPY 下行压力增加
-> 日股受汇率、利率和海外风险偏好共同影响
```

```text
MOF 口头或实际干预风险上升
-> USD/JPY 短线波动放大
-> 日元空头风险上升
-> 需要观察汇率水平、波动率和官方措辞
```

### 中国宏观政策

```text
降准/降息/信用支持
-> 流动性改善
-> 债券收益率偏下行
-> 人民币可能承压或受风险偏好改善支撑
-> A 股和房地产链条可能短线受益
```

```text
NBS 数据弱于预期
-> 增长压力上升
-> 政策支持预期增强
-> 商品和亚洲风险资产可能承压
-> 反向风险是政策快速加码
```

## 市场影响用语

使用概率和方向，不使用确定结论：

```text
偏利多 / 偏利空 / 中性偏多 / 中性偏空 / 影响有限 / 路径依赖 / 需要确认
```

## 专业研究口径

采用 CFA/投行研究风格组织结论：

1. 数据依据：列明实际值、预期值、前值、来源和时间范围；缺失时明确说明。
2. 逻辑推演：解释经济变量、政策预期、行业盈利与资产定价之间的传导关系。
3. 情景分析：区分基准、上行和下行情景，并写明关键假设。
4. 风险提示：指出反向风险、数据修订、政策变化和市场提前定价的可能性。
5. 配置含义：仅讨论非个性化的跨资产风险暴露、相关性和分散化含义，不给出个人仓位或交易指令。

企业财报解读必须以收入、利润率、现金流、指引、估值或可比数据为依据；
行业趋势不得仅由单条宏观新闻外推。

财报、监管、商品和资本流类事件也遵循同一原则：

- 先确认来源层级与事实基准。
- 再判断是否存在预期差。
- 再映射到资产、行业与风险暴露。
- 最后输出方向、强度与风险提示。

## 事实与推断边界

输出时明确区分：事实、市场解读、模型推断、不确定性。
