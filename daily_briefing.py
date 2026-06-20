"""
每日 AI + 电机控制 简报
- arXiv 抓取 1-2 年内的高质量论文（AI+电机 / 纯电机）
- DeepSeek 评分筛选（看作者机构、GitHub、会议背书）
- AI 圈新闻抓取 + DeepSeek 过滤垃圾信息
- Server 酱推送到个人微信
"""
import os
import re
import json
import time
import urllib.request
import urllib.parse
import feedparser
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

# ============== 配置 ==============
SERVERCHAN_SENDKEY = os.environ.get('SERVERCHAN_SENDKEY', '')
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY', '')

# arXiv 分类
ARXIV_CATEGORIES = {
    'cs.SY': 'Systems & Control',
    'cs.RO': 'Robotics',
    'cs.AI': 'Artificial Intelligence',
}

# arXiv API（免费，无需 key）
ARXIV_API = 'http://export.arxiv.org/api/query'

# AI + 科技圈新闻源
NEWS_FEEDS = [
    # AI 垂直
    ('Hacker News 最佳', 'https://hnrss.org/best?count=30'),
    ('量子位', 'https://www.qbitai.com/feed'),
    ('机器之心', 'https://www.jiqizhixin.com/rss'),
    # 科技圈
    ('36氪', 'https://36kr.com/feed'),
    ('极客公园', 'https://www.geekpark.net/rss'),
    ('智东西', 'https://zhidx.com/feed'),
]

# 电机控制关键词（更严格，要求直接相关）
MOTOR_KEYWORDS = [
    # 核心控制算法
    'FOC', 'field-oriented', 'field oriented', 'vector control',
    'SMO', 'sliding mode observer', 'sliding-mode observer',
    'sensorless control', 'sensorless drive', 'sensorless PMSM',
    'PMSM', 'permanent magnet synchronous', 'permanent-magnet synchronous',
    'BLDC', 'brushless DC', 'brushless direct',
    # 电机控制基础
    'motor control', 'motor drive', 'motor drives', 'motor speed',
    'sensorless', 'sensor-less', 'position sensorless',
    'PWM', 'SVPWM', 'space vector modulation', 'space vector pulse width',
    'inverter', 'voltage source inverter', 'current control',
    'MPPT', 'torque ripple', 'torque control',
    'rotor position', 'speed regulation', 'speed control',
    'MTPA', 'field weakening', 'field oriented',
    'high frequency injection', 'HF injection', 'pulsating HF',
    'rotor speed', 'rotor angle', 'back-EMF', 'back EMF',
    # 观测器和控制理论
    'sliding mode control', 'terminal sliding mode', 'super-twisting',
    'adaptive sliding', 'extended sliding mode',
    'disturbance observer', 'extended state observer', 'ESO',
    'model predictive control', 'MPC', 'finite control set',
    'adaptive control', 'robust control', 'backstepping',
    # AI 应用于电机
    'neural network motor', 'neural network PMSM', 'neural network SMO',
    'deep learning motor', 'deep learning PMSM', 'deep learning SMO',
    'reinforcement learning motor', 'RL motor control',
    'LSTM motor', 'LSTM PMSM', 'LSTM SMO',
    'fuzzy control motor', 'fuzzy logic motor', 'fuzzy PID',
    'fault diagnosis motor', 'motor fault', 'PMSM fault',
    'IGBT', 'SiC inverter', 'GaN inverter', 'three-level inverter',
    # 电机相关术语
    'stator', 'rotor', 'winding', 'reluctance', 'salient pole',
    'synchronous motor', 'asynchronous motor', 'induction motor',
    'PMSM drive', 'PMSM control', 'motor estimation',
    'd-q axis', 'dq-axis', 'alpha-beta', 'clarke transformation', 'park transformation',
    'current loop', 'speed loop', 'cascaded control',
]

# AI 过滤关键词
AI_KEYWORDS = [
    'AI', 'LLM', 'large language model', 'GPT', 'Claude', 'Gemini',
    'agent', 'agentic', 'multimodal', 'multimodality',
    'open source', 'open-source', 'open source model',
    'NVIDIA', 'GPU', 'Jetson', 'CUDA', 'edge AI', 'edge inference',
    'TensorRT', 'ONNX', 'quantization', 'quantization',
    'robot', 'humanoid', 'embodied AI', 'embodied',
    'autonomous', 'autonomous driving',
    'Ollama', 'Llama', 'DeepSeek', 'Qwen', 'Mistral', 'Gemma',
    'transformer', 'diffusion model', 'RAG', 'retrieval augmented',
    'embedding', 'fine-tuning', 'fine tuning',
    'reasoning', 'chain of thought', 'CoT',
    'cursor', 'copilot', 'claude code', 'cline',
    'voice', 'speech', 'TTS', 'ASR', 'whisper',
    'computer vision', 'image generation', 'video generation',
    'sora', 'veo', 'midjourney',
    'figure', 'optimus', 'boston dynamics',
]


# ============== arXiv 抓取 ==============
def fetch_arxiv_papers(category, max_results=50):
    """用 arXiv API 抓论文"""
    query = f'cat:{category}'
    url = f'{ARXIV_API}?search_query={query}&start=0&max_results={max_results}&sortBy=submittedDate&sortOrder=descending'

    print(f'  抓取 {category} ...')
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read().decode('utf-8')
    except Exception as e:
        print(f'  抓 {category} 失败: {e}')
        return []

    # 解析 arXiv API 的 XML（Atom 格式）
    ns = {'atom': 'http://www.w3.org/2005/Atom', 'arxiv': 'http://arxiv.org/schemas/atom'}
    root = ET.fromstring(data)

    papers = []
    for entry in root.findall('atom:entry', ns):
        try:
            title = entry.find('atom:title', ns).text.strip().replace('\n', ' ').replace('  ', ' ')
            summary = entry.find('atom:summary', ns).text.strip().replace('\n', ' ').replace('  ', ' ')

            # 作者
            authors = []
            affiliations = []
            for author in entry.findall('atom:author', ns):
                name = author.find('atom:name', ns)
                if name is not None:
                    authors.append(name.text.strip())
                aff = author.find('arxiv:affiliation', ns)
                if aff is not None and aff.text:
                    affiliations.append(aff.text.strip())

            # 链接
            link_el = entry.find('atom:id', ns)
            link = link_el.text.strip() if link_el is not None else ''

            # arxiv id
            arxiv_id = link.split('/')[-1] if link else ''

            # 发布时间
            published = entry.find('atom:published', ns).text.strip()
            pub_date = datetime.strptime(published, '%Y-%m-%dT%H:%M:%SZ')

            papers.append({
                'title': title,
                'summary': summary,
                'authors': authors,
                'affiliations': affiliations,
                'link': link,
                'arxiv_id': arxiv_id,
                'published': pub_date,
                'category': category,
            })
        except Exception as e:
            print(f'  解析论文失败: {e}')
            continue

    print(f'  → {len(papers)} 篇')
    return papers


def fetch_all_arxiv():
    """抓所有分类"""
    all_papers = []
    seen_ids = set()
    for cat in ARXIV_CATEGORIES:
        papers = fetch_arxiv_papers(cat, max_results=50)
        for p in papers:
            if p['arxiv_id'] not in seen_ids:
                seen_ids.add(p['arxiv_id'])
                all_papers.append(p)
    return all_papers


# ============== 关键词初筛 ==============
def filter_by_keywords(papers, keywords, require_match=True):
    """关键词筛选"""
    matched = []
    for p in papers:
        text = (p['title'] + ' ' + p['summary']).lower()
        if any(kw.lower() in text for kw in keywords):
            matched.append(p)
    return matched


# ============== 阿里百炼 DeepSeek 调用 ==============
def call_deepseek(prompt, max_tokens=2000):
    """调用阿里百炼的 DeepSeek 模型（OpenAI 兼容接口）"""
    url = 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
    data = {
        'model': 'deepseek-v3',  # 阿里百炼上的 DeepSeek-V3 模型
        'messages': [
            {'role': 'user', 'content': prompt}
        ],
        'max_tokens': max_tokens,
        'temperature': 0.3,
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
            'User-Agent': 'Mozilla/5.0',
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            return result['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f'  DeepSeek 调用失败: {e}')
        return ''


def evaluate_paper(paper):
    """DeepSeek 评估论文质量"""
    authors_str = ', '.join(paper['authors'][:5])
    if len(paper['authors']) > 5:
        authors_str += f' 等 {len(paper["authors"])} 人'
    affs_str = ', '.join(set(paper['affiliations'])) if paper['affiliations'] else '未知'

    # 检测摘要里有没有 GitHub/会议接收信息
    has_github = 'github.com' in paper['summary'].lower() or 'github.com' in paper['title'].lower()
    venue_match = re.search(r'(Accepted to|Accepted at|To appear in|published in)\s+([A-Z][A-Za-z\.\s]+)', paper['summary'])
    venue = venue_match.group(0) if venue_match else '未提及'

    prompt = f"""你是电机控制领域 + AI 论文质量审查专家。**严格**评估这篇论文是否跟【电机控制/FOC/SMO/PMSM】直接相关。

【论文信息】
标题：{paper['title']}
作者：{authors_str}
作者机构：{affs_str}
发表分类：{paper['category']}
发表时间：{paper['published'].strftime('%Y-%m-%d')}
GitHub 代码：{'有' if has_github else '无'}
接收会议/期刊：{venue}

摘要：
{paper['summary'][:1500]}

【关键问题】（必须明确回答）
这篇论文是不是**直接**研究以下之一：
- FOC（磁场定向控制）/ 矢量控制
- SMO（滑模观测器）/ 无位置传感器控制
- PMSM（永磁同步电机）/ BLDC / 异步电机 的控制/驱动
- AI/深度学习/神经网络 应用于电机控制
- 模型预测控制（MPC）应用于电机
- 电机故障诊断、参数辨识

如果只是用到了"状态估计"、"观测器"但跟电机无关（如 SLAM、UWB 定位、机器人感知），**不相关**！

【评分维度】（每项 0-10）

1. **直接相关性**（50%）⭐⭐⭐ 最重要
   - 完全不相关（如 SLAM、UWB、机器人感知用状态估计）= 0-2 分
   - 间接相关（如通用控制理论、通用机器学习）= 3-5 分
   - 直接相关（明确研究 FOC/SMO/PMSM 控制）= 7-10 分

2. 质量信号（25%）
   - 作者机构：顶校/顶企业（清华、北航、浙大、华科、哈工大、IEEE Fellow 所在组、MIT/Stanford/CMU/Caltech、ABB/西门子/施耐德）= 高分
   - 有 GitHub 代码 = +2
   - 有明确实验验证（电机实验台数据）= +1
   - 被接收会议/期刊（IEEE Trans、ACC、CDC、IFAC）= +2
   - 摘要详实（不是 1-2 句水摘要）= +1

3. 可实现性（15%）
   - 电机控制研究生能否复现
   - 是否依赖稀缺硬件

4. 新颖性（10%）
   - 方法是否新颖，不是简单套用神经网络

【可信度红旗】
- 摘要只有 1-2 句明显 AI 生成 → -3
- 作者机构全是野鸡大学 → -2
- 无任何实验/数据 → -2
- 标题党 → -2

严格按 JSON 输出（不要其他文字）：
{{"score": 8.5, "reasons": "评分理由（中文，30 字内）"}}
"""
    result = call_deepseek(prompt, max_tokens=300)
    # 解析 JSON
    try:
        # 尝试提取 JSON
        match = re.search(r'\{[^}]+\}', result, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
            return float(data.get('score', 0)), data.get('reasons', '')
    except Exception as e:
        print(f'  解析评分失败: {e}')
    return 0, ''


def generate_paper_summary(paper, paper_type):
    """DeepSeek 生成科普级中文摘要"""
    prompt = f"""你是一个帮助"普通电机控制研究生"理解论文的专家。请把论文摘要改写为"科普级"中文摘要。

【硬性规则】

1. **不要直接搬英文术语和函数名**
   - 第一次出现的专有名词，必须**括号内加一句通俗解释**
   - 例如："M-estimation（一种能过滤异常值的统计方法）"
   - 例如："IRLS 迭代重加权（多次调整数据权重的拟合算法）"

2. **白话优先**
   - "本文提出" → "研究者提出了" 或 "科学家尝试了"
   - "实验证明" → "实际测试发现"
   - 避免堆砌"新颖"、"优越"、"显著"等空话

3. **4 段结构**（每段 30-50 字）
   - 段 1【研究背景】：为什么要做这件事？解决什么实际问题/痛点？
   - 段 2【之前方法的不足】：传统方法有什么局限？
   - 段 3【本文做了什么】：核心思路是什么（用白话讲清楚）
   - 段 4【效果如何】：实测/仿真结果怎样

4. **关键要点**用 3 句话总结，每句用白话

5. **避免**：
   - 直接翻译英文摘要
   - 堆专业术语不解释
   - 重复"本文"开头

【论文信息】
英文标题：{paper['title']}
英文摘要：{paper['summary']}

请严格按以下 JSON 输出（不要其他文字）：
{{
  "title_zh": "中文标题（20 字内）",
  "summary_zh": "科普级中文摘要（150-200 字，4 段结构）",
  "key_points": ["白话要点1", "白话要点2", "白话要点3"]
}}
"""
    result = call_deepseek(prompt, max_tokens=800)
    try:
        match = re.search(r'\{[\s\S]+\}', result)
        if match:
            return json.loads(match.group(0))
    except Exception as e:
        print(f'  解析摘要失败: {e}')
    return {
        'title_zh': paper['title'][:30],
        'summary_zh': paper['summary'][:300],
        'key_points': []
    }


def evaluate_news(title, summary):
    """评估新闻价值（科技圈 + AI）"""
    prompt = f"""这是一个给"电机控制方向研究生 + 关注科技圈"的 AI 简报。判断这条新闻值不值得推送。

标题：{title}
摘要：{summary[:500]}

值得推送（5-10 分）：
- AI 技术/工具更新（开源模型、AI Agent、新框架）
- 机器人/嵌入式 AI/具身智能 新进展
- 硬件突破（Jetson、新芯片、嵌入式开发板）
- 科技公司重大技术发布
- 改变行业的新产品/新范式

不值得推送（0-3 分）：
- 公司融资/估值/上市/IPO
- 创始人故事/八卦
- 政策/法规
- 营销软文/带货
- 烂大街概念（"AI 取代人工"）
- 完全不相关的（如娱乐圈、社会新闻）

【特别注意】
- 如果是 AI/机器人/嵌入式/新能源/芯片 相关 → 加分
- 如果是 36氪/极客公园/智东西 的科技内容 → 多给分
- 如果是 量子位/机器之心 的 AI 硬核技术 → 优先

严格按 JSON 输出：
{{"score": 8.0, "reason": "一句话理由（中文，20 字内）"}}
"""
    result = call_deepseek(prompt, max_tokens=200)
    try:
        match = re.search(r'\{[^}]+\}', result, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
            return float(data.get('score', 0)), data.get('reason', '')
    except Exception as e:
        print(f'  解析新闻评分失败: {e}')
    return 0, ''


# ============== 新闻抓取 ==============
def fetch_news():
    """抓 AI 新闻"""
    all_news = []
    for source_name, url in NEWS_FEEDS:
        print(f'  抓取 {source_name}...')
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:15]:
                all_news.append({
                    'source': source_name,
                    'title': entry.title,
                    'summary': entry.get('summary', entry.get('description', ''))[:500],
                    'link': entry.get('link', ''),
                })
        except Exception as e:
            print(f'  抓 {source_name} 失败: {e}')
    print(f'  → 共 {len(all_news)} 条')
    return all_news


def filter_news_keywords(news_list):
    """AI 关键词初筛"""
    matched = []
    for n in news_list:
        text = (n['title'] + ' ' + n['summary']).lower()
        if any(kw.lower() in text for kw in AI_KEYWORDS):
            matched.append(n)
    return matched


# ============== Server 酱推送 ==============
def push_to_wechat(content):
    """Server 酱推送"""
    encoded_title = urllib.parse.quote('每日 AI 简报')
    encoded_content = urllib.parse.quote(content)
    url = f'https://sctapi.ftqq.com/{SERVERCHAN_SENDKEY}.send?title={encoded_title}&desp={encoded_content}'

    req = urllib.request.Request(url, method='GET')
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode('utf-8')


# ============== 主流程 ==============
def main():
    print('=' * 50)
    print(f'开始生成每日 AI 简报 {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print('=' * 50)

    today = datetime.now().strftime('%Y-%m-%d')
    sections = []

    # ====== 1. 抓 arXiv 论文 ======
    print('\n[1/5] 抓取 arXiv 论文...')
    all_papers = fetch_all_arxiv()
    print(f'  总计 {len(all_papers)} 篇候选')

    # 关键词初筛（电机相关）
    print('\n[2/5] 关键词初筛...')
    motor_papers = filter_by_keywords(all_papers, MOTOR_KEYWORDS)
    print(f'  电机相关: {len(motor_papers)} 篇')

    # DeepSeek 评分（电机相关 + AI 结合的论文）
    print('\n[3/5] DeepSeek 评分论文（这一步骤较慢）...')
    paper_scores = []
    eval_count = min(15, len(motor_papers))  # 限制每次最多评 15 篇，省钱
    for i, p in enumerate(motor_papers[:eval_count]):
        if i % 5 == 0:
            print(f'  进度 {i}/{eval_count}...')
        score, reason = evaluate_paper(p)
        if score >= 6:  # 只保留 6 分以上
            paper_scores.append((score, p, reason))

    paper_scores.sort(key=lambda x: -x[0])

    # 选 1 篇 AI+电机（来自 cs.AI）+ 1 篇纯电机（cs.SY/cs.RO）
    ai_motor_paper = None
    pure_motor_paper = None

    # 优先从 cs.AI 中选 AI+电机
    for score, p, reason in paper_scores:
        if p['category'] == 'cs.AI' and not ai_motor_paper:
            ai_motor_paper = (score, p, reason)
            break

    # 再从 cs.SY/cs.RO 中选纯电机
    for score, p, reason in paper_scores:
        if p['category'] in ('cs.SY', 'cs.RO') and not pure_motor_paper:
            pure_motor_paper = (score, p, reason)
            break

    # 兜底机制：保证有 2 篇
    if not ai_motor_paper and not pure_motor_paper and len(paper_scores) >= 2:
        ai_motor_paper = paper_scores[0]
        pure_motor_paper = paper_scores[1]
    elif not ai_motor_paper and paper_scores:
        ai_motor_paper = paper_scores[0]
    elif not pure_motor_paper and len(paper_scores) >= 2:
        pure_motor_paper = paper_scores[1] if paper_scores[0] == ai_motor_paper else paper_scores[0]

    print(f'  选中: AI+电机={ai_motor_paper is not None}, 纯电机={pure_motor_paper is not None}')
    if ai_motor_paper:
        print(f'    AI+电机: {ai_motor_paper[1]["title"][:50]} (cat={ai_motor_paper[1]["category"]}, score={ai_motor_paper[0]})')
    if pure_motor_paper:
        print(f'    纯电机: {pure_motor_paper[1]["title"][:50]} (cat={pure_motor_paper[1]["category"]}, score={pure_motor_paper[0]})')

    # ====== 2. 生成论文简报 ======
    print('\n[4/5] 生成论文摘要...')
    final_content = f'📰 **每日 AI 简报** | {today}\n\n'

    if ai_motor_paper:
        score, p, reason = ai_motor_paper
        print(f'  AI+电机: {p["title"][:50]}... (score={score})')
        summary_data = generate_paper_summary(p, 'AI+电机控制')
        final_content += '─' * 30 + '\n'
        final_content += f'【🔬 论文 1/2】AI + 电机控制 [{score:.1f}/10]\n\n'
        final_content += f'**Title (EN)**: {p["title"]}\n\n'
        final_content += f'**中文标题**: {summary_data.get("title_zh", "")}\n\n'
        final_content += f'📝 **中文摘要（白话版）**：\n{summary_data.get("summary_zh", "")}\n\n'
        if summary_data.get('key_points'):
            final_content += '**核心要点**：\n'
            for kp in summary_data['key_points']:
                final_content += f'• {kp}\n'
            final_content += '\n'
        final_content += f'📄 **Original Abstract**:\n{p["summary"][:500]}\n\n'
        final_content += f'🔗 [arXiv: {p["arxiv_id"]}]({p["link"]})\n\n'

    if pure_motor_paper and pure_motor_paper != ai_motor_paper:
        score, p, reason = pure_motor_paper
        print(f'  纯电机: {p["title"][:50]}... (score={score})')
        summary_data = generate_paper_summary(p, '电机控制')
        final_content += '─' * 30 + '\n'
        final_content += f'【⚙️ 论文 2/2】电机控制 [{score:.1f}/10]\n\n'
        final_content += f'**Title (EN)**: {p["title"]}\n\n'
        final_content += f'**中文标题**: {summary_data.get("title_zh", "")}\n\n'
        final_content += f'📝 **中文摘要（白话版）**：\n{summary_data.get("summary_zh", "")}\n\n'
        if summary_data.get('key_points'):
            final_content += '**核心要点**：\n'
            for kp in summary_data['key_points']:
                final_content += f'• {kp}\n'
            final_content += '\n'
        final_content += f'📄 **Original Abstract**:\n{p["summary"][:500]}\n\n'
        final_content += f'🔗 [arXiv: {p["arxiv_id"]}]({p["link"]})\n\n'

    # ====== 3. AI 圈新闻 ======
    print('\n[5/5] 抓取 AI 新闻 + DeepSeek 过滤...')
    all_news = fetch_news()
    ai_news = filter_news_keywords(all_news)
    print(f'  AI 相关: {len(ai_news)} 条')

    # 评分
    news_scores = []
    news_eval_count = min(30, len(ai_news))  # 多评一些，从里面选
    for i, n in enumerate(ai_news[:news_eval_count]):
        if i % 5 == 0:
            print(f'  新闻评分 {i}/{news_eval_count}...')
        score, reason = evaluate_news(n['title'], n['summary'])
        if score >= 5:  # 阈值降到 5，确保能选 3 条
            news_scores.append((score, n, reason))

    news_scores.sort(key=lambda x: -x[0])
    top_news = news_scores[:3]  # 取 3 条

    if not top_news:
        # 兜底：取任何 ai_news 中的前 3 条
        print('  ⚠️ 没有高分新闻，兜底取前 3 条')
        for n in ai_news[:3]:
            top_news.append((5.0, n, '兜底推荐'))

    if top_news:
        final_content += '─' * 30 + '\n'
        final_content += '【🆕 科技圈新动态】\n\n'
        for i, (score, n, reason) in enumerate(top_news, 1):
            final_content += f'**{i}. [{score:.1f}/10] {n["title"]}**\n'
            final_content += f'   📍 来源：{n["source"]}\n'
            if reason:
                final_content += f'   💡 {reason}\n'
            if n['link']:
                final_content += f'   🔗 [查看]({n["link"]})\n'
            final_content += '\n'

    # ====== 4. 推送 ======
    print('\n[推送] 发送到微信...')
    try:
        result = push_to_wechat(final_content)
        print(f'  → {result}')
    except Exception as e:
        print(f'  推送失败: {e}')

    print('\n' + '=' * 50)
    print('✅ 完成')


if __name__ == '__main__':
    main()
