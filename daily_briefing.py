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

# AI 新闻源
NEWS_FEEDS = [
    ('Hacker News 最佳', 'https://hnrss.org/best?count=30'),
    ('量子位', 'https://www.qbitai.com/feed'),
    ('机器之心', 'https://www.jiqizhixin.com/rss'),
]

# 电机控制关键词
MOTOR_KEYWORDS = [
    'FOC', 'field-oriented', 'field oriented', 'vector control',
    'SMO', 'sliding mode observer', 'sliding-mode',
    'PMSM', 'permanent magnet synchronous', 'permanent-magnet',
    'BLDC', 'brushless DC', 'brushless direct',
    'motor control', 'motor drive', 'motor drives',
    'sensorless', 'sensor-less', 'position sensorless',
    'PWM', 'SVPWM', 'space vector',
    'inverter', 'MPPT', 'torque ripple',
    'rotor position', 'speed regulation', 'speed control',
    'MTPA', 'field weakening',
    'high frequency injection', 'HF injection',
    'observer-based', 'disturbance observer',
    'adaptive control', 'robust control',
    'neural network motor', 'deep learning motor',
    'reinforcement learning motor', 'MPC motor',
    'fault diagnosis motor', 'motor fault',
    'IGBT', 'SiC', 'GaN',
    'PMSM drive', 'PMSM control', 'motor estimation',
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

    prompt = f"""你是电机控制领域 + AI 论文质量审查专家。综合评估这篇论文。

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

【评分维度】（每项 0-10）

1. 相关性（30%）
   - 跟 FOC/SMO/PMSM/无感控制/电机驱动 的契合度
   - 是否 AI+电机控制的结合

2. 质量信号（30%）
   - 作者机构：顶校/顶企业（清华、北航、浙大、华科、哈工大、IEEE Fellow 所在组、MIT/Stanford/CMU/Caltech、ABB/西门子/施耐德）= 高分
   - 有 GitHub 代码 = +2
   - 有明确实验验证 = +1
   - 被接收会议/期刊（IEEE Trans、ACC、CDC、IFAC）= +2
   - 摘要详实（不是 1-2 句水摘要）= +1

3. 可实现性（20%）
   - 电机控制研究生能否复现
   - 是否依赖稀缺硬件

4. 新颖性（20%）
   - 方法是否新颖，不是简单套用神经网络

【可信度红旗】
- 摘要只有 1-2 句明显 AI 生成 → -3
- 作者机构全是野鸡大学 → -2
- 无任何实验/数据 → -2
- 标题党（标题很炸但内容空洞）→ -2

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
    """DeepSeek 生成中英文摘要"""
    prompt = f"""你是一个电机控制领域专家。这是一篇{paper_type}论文，请输出格式化的内容。

【英文标题】
{paper['title']}

【英文摘要】
{paper['summary']}

请严格按以下 JSON 输出（不要其他文字）：
{{
  "title_zh": "中文标题（20 字内）",
  "summary_zh": "中文摘要（100-150 字，技术细节要准确）",
  "key_points": ["要点1", "要点2", "要点3"]
}}
"""
    result = call_deepseek(prompt, max_tokens=600)
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
    """评估新闻价值"""
    prompt = f"""这是一个给"电机控制方向研究生 + 关注 AI 行业"的 AI 简报。判断这条新闻值不值得推送。

标题：{title}
摘要：{summary[:500]}

值得推送（7-10 分）：
- AI 工具/技术更新，能直接提升学习或工作效率（如新开源模型、新框架）
- 机器人/嵌入式 AI 领域新进展（Figure、Optimus、Jetson 新硬件）
- 新的开源大模型/Agent 框架
- 行业重大技术突破

不值得推送（0-3 分）：
- 公司融资/估值/上市/IPO
- 创始人故事/八卦
- 政策/法规/合规
- 营销软文/带货
- 已经是烂大街概念（"AI 取代人工"、"AI 改变世界"）
- 跟电机控制/AI 工具完全无关

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

    # 选 1 篇 AI+电机（来自 cs.AI 分类）+ 1 篇纯电机（来自 cs.SY/cs.RO）
    ai_motor_paper = None
    pure_motor_paper = None

    for score, p, reason in paper_scores:
        if not ai_motor_paper and p['category'] == 'cs.AI':
            ai_motor_paper = (score, p, reason)
        elif not pure_motor_paper and p['category'] in ('cs.SY', 'cs.RO'):
            pure_motor_paper = (score, p, reason)
        if ai_motor_paper and pure_motor_paper:
            break

    # 兜底：分类里没合适的就从 Top 里取
    if not ai_motor_paper and paper_scores:
        ai_motor_paper = paper_scores[0]
    if not pure_motor_paper and len(paper_scores) > 1:
        pure_motor_paper = paper_scores[1]
    elif not pure_motor_paper and paper_scores:
        pure_motor_paper = paper_scores[0]

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
        final_content += f'📝 **中文摘要**：\n{summary_data.get("summary_zh", "")}\n\n'
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
        final_content += f'📝 **中文摘要**：\n{summary_data.get("summary_zh", "")}\n\n'
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
    news_eval_count = min(20, len(ai_news))  # 限制新闻评分篇数，省钱
    for i, n in enumerate(ai_news[:news_eval_count]):
        if i % 5 == 0:
            print(f'  新闻评分 {i}/{news_eval_count}...')
        score, reason = evaluate_news(n['title'], n['summary'])
        if score >= 7:
            news_scores.append((score, n, reason))

    news_scores.sort(key=lambda x: -x[0])
    top_news = news_scores[:3]  # 取 3 条

    if top_news:
        final_content += '─' * 30 + '\n'
        final_content += '【🆕 AI 圈新动态】\n\n'
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
