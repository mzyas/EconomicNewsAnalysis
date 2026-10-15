"""Prompt templates for LLM-powered economic news analysis.

The prompts only reference data already available in the graph state
(``source_context`` and ``evidence``); they never ask the model to invent
sources or facts. They explicitly distinguish fact / inference /
uncertainty and contain a non-personalised risk notice.
"""

from __future__ import annotations

RESEARCH_PROMPT = """你是一位严谨的经济新闻研究员。请仅基于以下"来源上下文"与"证据"中明确出现的信息，整理结构化事实。

【来源上下文】
{source_context}

【证据】
{evidence}

请按下面三个分类输出：
1. **关键事实** — 证据中直接出现、可被引用的陈述。
2. **市场推断** — 标注为"推断"，说明市场可能如何解读这些事实。
3. **不确定性** — 证据中缺失或仍待核实的关键信息。

要求：
- 不要编造证据之外的事实。
- 对每条事实标注证据来源 URL。
- 输出语言：{language}（默认简体中文）。
"""


ANALYSIS_PROMPT = """你是一位宏观经济学家。请仅基于下列"来源上下文"与"证据"撰写专业分析。

【新闻标题】
{title}

【来源上下文】
{source_context}

【证据】
{evidence}

【重要性分数】
{relevance_score}

请按以下链条分析：
新闻事件 → 经济信号 → 政策预期 → 资产影响 → 风险与关注。

要求：
- 只引用 source_context 与 evidence 中明确存在的事实。
- 明确区分：事实（事实）/市场推断（推断）/不确定因素（不确定）。
- 每条结论标注证据来源 URL。
- 使用专业但清晰的语言。
- 输出语言：{language}（默认简体中文）。

**重要：你必须输出严格的 JSON 格式，不要包含任何额外的文字或 markdown 标记。格式如下：**
```json
{{
  "translated_title": "新闻标题的简体中文忠实翻译（若标题已是中文则原样保留）",
  "translated_summary": "新闻摘要/正文的简体中文翻译或一句话概述（{language}，60-100 字）",
  "analysis": "你的完整分析文本，包含所有上述分析",
  "signals": [
    {{"signal": "具体信号描述1", "type": "事实/推断/不确定", "impact": "对市场的影响", "assets": ["资产1", "资产2"]}},
    {{"signal": "具体信号描述2", "type": "事实/推断/不确定", "impact": "对市场的影响", "assets": ["资产1", "资产2"]}}
  ],
  "focus_assets": ["资产1", "资产2"]
}}
```

translated_title 必须是对原标题的忠实翻译，不得改写含义或添加原文没有的信息。
translated_summary 用简体中文概括本条新闻要点，仅基于 source_context / evidence，不得编造。
signals 数组至少包含 2-4 条核心信号，每条 signal 必须有 signal/type/impact/assets 字段。
每条 signal 文本控制在 60 字以内，一句话说清核心观点，不要堆砌细节。细节放入 impact 字段。
focus_assets 列出本条新闻直接影响的主要资产，**用简体中文资产名**（如 黄金、原油、美国国债、
A股、港股、伊朗里亚尔）；仅当是通用货币代码/符号时才保留原样（如 USD、CNY、EUR、JPY、GBP）。
不得输出未翻译的英文资产名——例如 Treasuries 写成「美国国债」、Iranian Rial 写成「伊朗里亚尔」、
Equities 写成「股票」、Bonds 写成「债券」、Securities Sector 写成「券商板块」。
signals 数组里每条的 assets 字段同样遵守上述资产命名规则。

风险提示：本分析仅供研究参考，不构成个性化投资建议。

只输出 JSON，不要加任何其他文字。"""


MARKET_IMPACT_PROMPT = """你是一位宏观策略师。下面是今日多条经济新闻的核心信号汇总。
请据此撰写"市场影响地图"，覆盖五个维度。每个维度 2-3 句、不超过 80 字，
只做非个性化的方向性判断，不得给出仓位比例或买卖指令。

【今日核心信号汇总】
{signals_digest}

要求：
- 仅基于上述信号做合理推断，不要编造信号之外的事实或数字。
- 风险偏好维度只说明非个性化的风险暴露变化，不得出现仓位比例或买卖指令。
- 输出语言：{language}（默认简体中文）。

仅输出严格 JSON，不要任何额外文字或 markdown：
{{
  "rates_bonds": "利率与债券方向判断",
  "fx": "汇率方向判断",
  "equities": "股票与行业方向判断",
  "commodities": "商品方向判断",
  "risk_appetite": "风险偏好与资产配置含义"
}}

只输出 JSON。"""


QUALITY_GATE_PROMPT = """请作为质量审查员，仅基于给定"分析"与"证据"判断下列分析是否合格。

【新闻标题】
{title}

【分析内容】
{analysis}

请逐项检查：
1. 关键结论是否有"证据"或"来源上下文"中的事实支撑？
2. 是否清晰区分了"事实"与"推断"？
3. 是否明确给出"不确定性"或未核实事项？
4. 是否遗漏了核心信号（如通胀、政策、流动性、风险偏好）？

仅输出 JSON：{{"verdict": "PASS" 或 "FAIL", "reason": "简要原因"}}
若证据不足或未做事实/推断区分，必须判 FAIL。
"""


MACRO_BACKGROUND_PROMPT = """你是一位宏观经济学家。下面是今日中国、美国、日本的官方/政府/央行新闻条目。
请据此撰写「中美日宏观背景」综述，分别为中国、美国、日本各写一小段，概括各自近期宏观经济态势。

【各国官方条目】
{official_digest}

要求：
- 中、美、日三段都要写，篇幅大致均衡，每段 2-3 句、不超过 80 字。
- 仅基于上述条目做合理概括，不要编造条目之外的事实或数字。
- 若某国条目缺失，则写一句该国暂无最新官方动态的中性说明。
- 只做客观态势综述，不得给出仓位比例或买卖指令。
- 输出语言：{language}（默认简体中文）。

仅输出严格 JSON，不要任何额外文字或 markdown：
{{
  "CN": "中国宏观背景综述",
  "US": "美国宏观背景综述",
  "JP": "日本宏观背景综述"
}}

只输出 JSON。"""
