"""
每日 AI + 电机控制 简报（优化版）
- arXiv 抓取 1-2 年内的高质量论文（AI+电机 / 纯电机）
- DeepSeek 评分筛选（看作者机构、GitHub、会议背书）
- AI 圈新闻抓取 + DeepSeek 过滤垃圾信息
- Server 酱推送到个人微信

优化点：
1. 合并评分+摘要+7要点为单次调用（节省50% token）
2. 批量处理（5篇/批，节省70% token）
3. 分层处理（先规则筛选，再API分析）
4. 缓存机制（重复运行节省100% token）
5. 配置文件驱动（所有阈值可调，无需改代码）
6. 结构化日志（替换print，支持文件轮转）
7. SQLite去重（记录已推送内容，跨天不重复）
"""
import os
import re
import sys
import json
import time
import hashlib
import sqlite3
import logging
import logging.handlers
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

import feedparser
import xml.etree.ElementTree as ET


def _http_request(url, data=None, headers=None, timeout=60, retries=3):
    """带重试的 HTTP 请求（指数退避，尊重 429 Retry-After）"""
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers or {})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429:
                retry_after = e.headers.get('Retry-After', '')
                wait = int(retry_after) if retry_after.isdigit() else 30
                logger.warning('429 限流，等待 %ds 后重试', wait)
                time.sleep(wait)
                continue
            if attempt < retries - 1:
                wait = 3 * (attempt + 1)
                logger.warning('请求失败 (第%d次)，%ds后重试: %s', attempt + 1, wait, e)
                time.sleep(wait)
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                wait = 3 * (attempt + 1)
                logger.warning('请求失败 (第%d次)，%ds后重试: %s', attempt + 1, wait, e)
                time.sleep(wait)
    logger.error('请求最终失败 (%d次): %s', retries, last_err)
    raise last_err

# ============== 配置加载 ==============
CONFIG_PATH = Path(__file__).parent / 'config.yaml'


def load_config():
    """加载 config.yaml，不存在则用内置默认值"""
    defaults = {
        'arxiv': {
            'max_results_per_query': 30,
            'cutoff_days': 730,
            'eval_max_papers': 15,
            'final_papers': 2,
        },
        'paper': {
            'high_priority_min_score': 6,
            'medium_priority_min_score': 4,
            'batch_size': 5,
        },
        'news': {
            'max_per_source': 15,
            'eval_max_news': 30,
            'min_score': 6,
            'final_count': 3,
            'funding_keywords': [
                '融资', '融了', '数亿元', '亿元融资', '千万元', '获投', '完成融资',
                '种子轮', '天使轮', 'A轮', 'B轮', 'C轮', 'D轮', 'Pre-IPO', 'IPO',
                '上市', '估值', 'funding', 'raised', 'series a', 'series b',
                'series c', 'seed round', 'venture', 'valuation', 'went public',
            ],
        },
        'github': {
            'languages': ['python', 'cpp', 'typescript', 'rust'],
            'max_per_lang': 3,
            'min_score': 5,
            'final_count': 3,
        },
        'ai_keywords': [
            'AI', 'LLM', 'large language model', 'GPT', 'Claude', 'Gemini',
            'agent', 'agentic', 'multimodal', 'open source', 'open-source',
            'NVIDIA', 'GPU', 'Jetson', 'CUDA', 'edge AI', 'TensorRT', 'ONNX',
            'robot', 'humanoid', 'embodied AI', 'autonomous', 'autonomous driving',
            'Ollama', 'Llama', 'DeepSeek', 'Qwen', 'Mistral', 'Gemma',
            'transformer', 'diffusion model', 'RAG', 'embedding', 'fine-tuning',
            'reasoning', 'chain of thought', 'cursor', 'copilot', 'claude code',
            'voice', 'speech', 'TTS', 'ASR', 'whisper',
            'computer vision', 'image generation', 'video generation',
            'sora', 'veo', 'midjourney', 'figure', 'optimus', 'boston dynamics',
        ],
        'motor_high_priority': [
            'FOC', 'PMSM', 'BLDC', 'SMO', 'sensorless control',
            'sliding mode observer', 'field-oriented control',
            'motor control', 'motor drive', 'vector control',
            'model predictive control', 'MPC',
            'neural network motor', 'deep learning motor',
        ],
        'motor_relevant': [
            'inverter', 'PWM', 'SVPWM', 'torque control',
            'speed control', 'current control', 'rotor position',
            'adaptive control', 'robust control', 'backstepping',
            'observer', 'estimation', 'identification',
        ],
        'foreign_institutions': [
            'mit', 'stanford', 'caltech', 'cmu', 'carnegie mellon', 'berkeley',
            'uc berkeley', 'cornell', 'princeton', 'harvard', 'oxford', 'cambridge',
            'eth zurich', 'epfl', 'imperial', 'kth', 'tu munich', 'tum',
            'university of california', 'university of michigan', 'ucla',
            'purdue', 'georgia tech', 'tokyo', 'kaist', 'nus', 'ntu',
            'samsung', 'toyota', 'bosch', 'siemens', 'abb', 'schneider',
            'nvidia', 'google', 'meta', 'apple', 'microsoft',
            'wayve', 'waymo', 'tesla', 'figure ai', 'boston dynamics',
        ],
        'chinese_institutions': [
            'tsinghua', 'beihang', 'zhejiang', 'huazhong', 'shanghai jiao',
            'xjtu', "xi'an jiaotong", 'southeast', 'ustc',
            'chinese academy of sciences', 'peking', 'fudan', 'nanjing',
            'tongji', 'beijing institute', 'northwestern polytechnical',
            'national university of defense',
        ],
        'cache': {'enabled': True, 'dir': 'paper_cache'},
        'logging': {
            'level': 'INFO',
            'file': 'briefing.log',
            'max_bytes': 5242880,
            'backup_count': 3,
        },
    }
    if CONFIG_PATH.exists():
        try:
            import yaml
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                user_cfg = yaml.safe_load(f)
            if user_cfg:
                _deep_merge(defaults, user_cfg)
        except ImportError:
            print('[WARN] pyyaml 未安装，使用内置默认配置。pip install pyyaml')
        except Exception as e:
            print(f'[WARN] 配置加载失败: {e}，使用内置默认配置')
    return defaults


def _deep_merge(base, override):
    """递归合并字典"""
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


CFG = load_config()

# ============== 环境变量 ==============
SERVERCHAN_SENDKEY = os.environ.get('SERVERCHAN_SENDKEY', '')
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY', '')

# ============== 日志 ==============
LOG_CFG = CFG['logging']
LOG_FILE = Path(__file__).parent / LOG_CFG['file']

logger = logging.getLogger('briefing')
logger.setLevel(getattr(logging, LOG_CFG['level'].upper(), logging.INFO))

_fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

_console = logging.StreamHandler(sys.stdout)
_console.setFormatter(_fmt)
logger.addHandler(_console)

if LOG_CFG.get('file'):
    _file = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_CFG.get('max_bytes', 5 * 1024 * 1024),
        backupCount=LOG_CFG.get('backup_count', 3),
        encoding='utf-8',
    )
    _file.setFormatter(_fmt)
    logger.addHandler(_file)

# ============== 常量 ==============
ARXIV_API = 'https://export.arxiv.org/api/query'

NEWS_FEEDS = [
    ('Hacker News 最佳', 'https://hnrss.org/best?count=30'),
    ('量子位', 'https://www.qbitai.com/feed'),
    ('机器之心', 'https://www.jiqizhixin.com/rss'),
    ('36氪', 'https://36kr.com/feed'),
    ('极客公园', 'https://www.geekpark.net/rss'),
    ('智东西', 'https://zhidx.com/feed'),
]

ARXIV_SEARCH_QUERIES = [
    ('foc_core',
     '(abs:"FOC" OR abs:"field-oriented control" OR abs:"field oriented control" OR '
     'abs:"current loop" OR abs:"current control" OR '
     'abs:"PI controller" OR abs:"anti-windup" OR '
     'abs:"PMSM inductance" OR abs:"inductance saturation" OR '
     'abs:"encoder alignment" OR abs:"encoder calibration" OR '
     'abs:"SiC inverter" OR abs:"silicon carbide" OR '
     'abs:"MTPA" OR abs:"field weakening" OR '
     'abs:"SVPWM" OR abs:"space vector modulation" OR '
     'abs:"torque ripple" OR abs:"harmonic") '
     'AND (abs:"motor" OR abs:"PMSM" OR abs:"BLDC" OR abs:"inverter" OR '
     'abs:"permanent magnet" OR abs:"synchronous")'),
    ('observer_math',
     '(abs:"sliding mode observer" OR abs:"SMO" OR '
     'abs:"sensorless control" OR abs:"sensorless drive" OR '
     'abs:"Lyapunov" OR abs:"stability analysis" OR '
     'abs:"fault diagnosis" OR abs:"fault detection" OR '
     'abs:"impedance control" OR abs:"robot joint" OR '
     'abs:"extended state observer" OR abs:"Kalman filter" OR '
     'abs:"disturbance observer" OR abs:"adaptive observer") '
     'AND (abs:"motor" OR abs:"PMSM" OR abs:"BLDC" OR abs:"inverter" OR '
     'abs:"permanent magnet" OR abs:"drive")'),
]

# ============== 缓存 ==============
CACHE_DIR = Path(__file__).parent / CFG['cache']['dir']


def get_cache_key(paper):
    content = f"{paper['title']}:{paper.get('summary', '')[:200]}"
    return hashlib.md5(content.encode()).hexdigest()


def load_from_cache(cache_key):
    if not CACHE_DIR.exists():
        return None
    cache_file = CACHE_DIR / f"{cache_key}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding='utf-8'))
        except Exception:
            pass
    return None


def save_to_cache(cache_key, result):
    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / f"{cache_key}.json"
    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


# ============== SQLite 去重 ==============
DB_PATH = Path(__file__).parent / 'briefing_history.db'


class DedupDB:
    """记录已推送的论文/新闻/GitHub，跨天不重复"""

    def __init__(self, db_path=DB_PATH):
        self.conn = sqlite3.connect(str(db_path))
        self._init_tables()

    def _init_tables(self):
        c = self.conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS pushed_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            push_date TEXT NOT NULL,
            item_type TEXT NOT NULL,
            item_key TEXT NOT NULL,
            title TEXT,
            score REAL,
            pushed_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_pushed_date ON pushed_items(push_date)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_pushed_key ON pushed_items(item_type, item_key)')
        self.conn.commit()

    def is_pushed(self, item_type, item_key, within_days=3):
        """检查是否已推送过（默认3天内）"""
        cutoff = (datetime.utcnow() - timedelta(days=within_days)).strftime('%Y-%m-%d')
        c = self.conn.cursor()
        c.execute(
            'SELECT 1 FROM pushed_items WHERE item_type=? AND item_key=? AND push_date>=? LIMIT 1',
            (item_type, item_key, cutoff)
        )
        return c.fetchone() is not None

    def mark_pushed(self, push_date, item_type, item_key, title='', score=0):
        """记录已推送"""
        c = self.conn.cursor()
        c.execute(
            'INSERT INTO pushed_items (push_date, item_type, item_key, title, score) VALUES (?, ?, ?, ?, ?)',
            (push_date, item_type, item_key, title, score)
        )
        self.conn.commit()

    def cleanup(self, keep_days=30):
        """清理旧记录"""
        cutoff = (datetime.utcnow() - timedelta(days=keep_days)).strftime('%Y-%m-%d')
        c = self.conn.cursor()
        c.execute('DELETE FROM pushed_items WHERE push_date < ?', (cutoff,))
        self.conn.commit()

    def stats(self):
        """统计推送记录"""
        c = self.conn.cursor()
        c.execute('SELECT push_date, item_type, COUNT(*) FROM pushed_items GROUP BY push_date, item_type ORDER BY push_date DESC LIMIT 10')
        return c.fetchall()

    def close(self):
        self.conn.close()


# ============== arXiv 抓取 ==============
def fetch_arxiv_by_search(query_name, query, max_results=30):
    from urllib.parse import quote
    encoded_query = quote(query, safe=':()/')
    url = (
        f'{ARXIV_API}?search_query={encoded_query}'
        f'&start=0&max_results={max_results}'
        f'&sortBy=submittedDate&sortOrder=descending'
    )
    logger.info('搜索 [%s]: %s...', query_name, query[:60])

    try:
        data = _http_request(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
    except Exception as e:
        logger.error('搜索失败: %s', e)
        return []

    ns = {'atom': 'http://www.w3.org/2005/Atom', 'arxiv': 'http://arxiv.org/schemas/atom'}
    try:
        root = ET.fromstring(data)
    except Exception as e:
        logger.error('XML解析失败: %s', e)
        return []

    papers = []
    for entry in root.findall('atom:entry', ns):
        try:
            title = entry.find('atom:title', ns).text.strip().replace('\n', ' ').replace('  ', ' ')
            summary = entry.find('atom:summary', ns).text.strip().replace('\n', ' ').replace('  ', ' ')

            authors, affiliations = [], []
            for author in entry.findall('atom:author', ns):
                name = author.find('atom:name', ns)
                if name is not None:
                    authors.append(name.text.strip())
                aff = author.find('arxiv:affiliation', ns)
                if aff is not None and aff.text:
                    affiliations.append(aff.text.strip())

            link_el = entry.find('atom:id', ns)
            link = link_el.text.strip() if link_el is not None else ''
            arxiv_id = link.split('/')[-1] if link else ''

            published = entry.find('atom:published', ns).text.strip()
            pub_date = datetime.strptime(published, '%Y-%m-%dT%H:%M:%SZ')

            journal_ref = entry.find('arxiv:journal_ref', ns)
            journal = journal_ref.text.strip() if journal_ref is not None and journal_ref.text else ''

            comment_el = entry.find('arxiv:comment', ns)
            comment = comment_el.text.strip() if comment_el is not None and comment_el.text else ''

            primary_cat = entry.find('arxiv:primary_category', ns)
            primary_category = primary_cat.get('term', '') if primary_cat is not None else ''

            papers.append({
                'title': title, 'summary': summary,
                'authors': authors, 'affiliations': affiliations,
                'link': link, 'arxiv_id': arxiv_id,
                'published': pub_date, 'category': query_name,
                'search_source': query_name,
                'journal_ref': journal, 'comment': comment,
                'primary_category': primary_category,
            })
        except Exception:
            continue

    logger.info('  → %d 篇', len(papers))
    return papers


def fetch_all_arxiv():
    cfg = CFG['arxiv']
    all_papers, seen_ids = [], set()
    cutoff_date = datetime.utcnow() - timedelta(days=cfg['cutoff_days'])

    for i, (query_name, query) in enumerate(ARXIV_SEARCH_QUERIES):
        papers = fetch_arxiv_by_search(query_name, query, max_results=cfg['max_results_per_query'])
        for p in papers:
            if p['arxiv_id'] not in seen_ids and p['published'].replace(tzinfo=None) >= cutoff_date:
                seen_ids.add(p['arxiv_id'])
                all_papers.append(p)
        # arXiv 限流：每次查询间隔 5 秒
        if i < len(ARXIV_SEARCH_QUERIES) - 1:
            time.sleep(5)
    logger.info('时间过滤后（近 %d 天）: %d 篇', cfg['cutoff_days'], len(all_papers))
    return all_papers


# ============== 机构判断 ==============
def is_foreign_institution(paper):
    affs_text = ' '.join(paper.get('affiliations', [])).lower()
    has_cn = any(kw in affs_text for kw in CFG['chinese_institutions'])
    if has_cn:
        return False
    return any(kw in affs_text for kw in CFG['foreign_institutions'])


# ============== 关键词筛选 ==============
def smart_filter(papers):
    high_kw = CFG['motor_high_priority']
    rel_kw = CFG['motor_relevant']
    high_priority, medium_priority = [], []

    for p in papers:
        text = (p['title'] + ' ' + p['summary']).lower()
        if any(kw.lower() in text for kw in high_kw):
            p['priority'] = 'high'
            high_priority.append(p)
        elif any(kw.lower() in text for kw in rel_kw):
            p['priority'] = 'medium'
            medium_priority.append(p)

    logger.info('高优先级: %d 篇, 中优先级: %d 篇', len(high_priority), len(medium_priority))
    return high_priority, medium_priority


def filter_news_keywords(news_list):
    """关键词初筛：匹配 AI 关键词 + 排除营销类"""
    exclude_kw = [
        '新品发布', '正式发布', '重磅发布', '全球首发', '首发上市',
        '开售', '预售', '上市', '上市售价', '价格公布',
        '评测', '体验', '上手', '开箱', '导购', '推荐购买',
        '销量', '市场份额', '出货量', '出货',
        '融资', '估值', 'IPO', '上市',
    ]
    matched = []
    for n in news_list:
        text = (n['title'] + ' ' + n['summary']).lower()
        # 排除营销类
        if any(kw in n['title'] for kw in exclude_kw):
            continue
        if any(kw.lower() in text for kw in CFG['ai_keywords']):
            matched.append(n)
    return matched


# ============== DeepSeek 调用 ==============
def call_deepseek(prompt, max_tokens=2000):
    url = 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
    data = {
        'model': 'deepseek-v3',
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': max_tokens,
        'temperature': 0.3,
    }
    try:
        result = _http_request(
            url,
            data=json.dumps(data).encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
                'User-Agent': 'Mozilla/5.0',
            },
            timeout=60,
        )
        result_json = json.loads(result)
        return result_json['choices'][0]['message']['content'].strip()
    except Exception as e:
        logger.error('DeepSeek 调用失败: %s', e)
        return ''


# ============== 论文分析 ==============
PAPER_ANALYSIS_PROMPT = """你是电机控制领域研究生导师。请对以下论文进行深度分析。

**重要：全部用中文回答，不要输出任何英文。**

论文英文标题：{title}
作者：{authors}
机构：{affiliations}
来源：{journal_ref}
分类：{category}
发表日期：{published}

完整摘要（请仔细阅读）：
{summary}

请严格按以下 JSON 格式输出（全部用中文）：
{{
  "title_zh": "中文标题（准确翻译，20字内）",
  "score": 0-10,
  "pain_point": "一句话痛点：这篇论文要解决电机控制领域的什么具体毛病？",
  "method": "核心方法：用白话描述技术路线（不用公式），说清楚创新点在哪里",
  "result": "关键实验数字：转矩脉动降低了多少？效率提升了几点？响应快了多少？如果摘要里没写数字就写'需阅读全文'",
  "benchmark": "对比对象：跟谁比的？传统PI？滑模？其他方法？如果没写就写'需阅读全文'",
  "limitation": "局限性：这个方法有什么缺点？成本高？算力要求大？只能用于特定电机？",
  "reproducibility": "复现成本：需要什么硬件？代码是否开源？大概需要多久复现？",
  "action": "行动建议：强烈建议精读 / 建议快速浏览 / 可跳过",
  "summary_zh": "中文摘要（150字，用白话讲清楚这篇论文做了什么、效果如何）",
  "need_full_text": true或false
}}

评分标准：
- 8-10分：直接研究FOC/SMO/PMSM/BLDC控制、无传感器、电机驱动拓扑
- 6-7分：间接相关（通用控制理论应用到电机、嵌入式AI推理加速）
- 4-5分：边缘相关
- 0-3分：不相关

注意：
1. 如果摘要中没有明确的实验数字，result字段必须写"需阅读全文"
2. 如果摘要中没有对比实验，benchmark字段必须写"需阅读全文"
3. limitation字段必须给出至少一个具体的局限性，不能写"未提及"
4. 所有字段必须用中文，绝对不能输出英文"""


def analyze_paper(paper):
    cache_key = get_cache_key(paper)
    cached = load_from_cache(cache_key)
    if cached:
        logger.debug('[缓存命中] %s', paper['title'][:30])
        return cached

    authors_str = ', '.join(paper['authors'][:3])
    if len(paper['authors']) > 3:
        authors_str += f' 等{len(paper["authors"])}人'
    affs_str = ', '.join(set(paper['affiliations'])) if paper['affiliations'] else '未知'
    journal_str = paper.get('journal_ref', '') or paper.get('comment', '') or '未发表'

    prompt = PAPER_ANALYSIS_PROMPT.format(
        title=paper['title'], authors=authors_str,
        affiliations=affs_str, journal_ref=journal_str,
        category=paper['category'],
        published=paper['published'].strftime('%Y-%m-%d'),
        summary=paper['summary'][:1500]
    )

    result = call_deepseek(prompt, max_tokens=800)
    try:
        match = re.search(r'\{[\s\S]+\}', result)
        if match:
            data = json.loads(match.group(0))
            save_to_cache(cache_key, data)
            return data
    except Exception as e:
        logger.warning('JSON解析失败: %s', e)

    return {
        'score': 5.0, 'pain_point': '分析失败', 'method': '需阅读全文',
        'result': '需阅读全文', 'benchmark': '未提及', 'limitation': '未提及',
        'reproducibility': '需阅读全文确认', 'action': '建议快速浏览',
        'title_zh': paper['title'][:20], 'summary_zh': paper['summary'][:100],
        'need_full_text': True,
    }


# ============== 批量处理 ==============
def batch_analyze_papers(papers, batch_size=5):
    """批量粗筛: 只评分+一句话理由, 不生成摘要(省 token, 为单篇精读留空间)"""
    results = []
    for i in range(0, len(papers), batch_size):
        batch = papers[i:i + batch_size]
        logger.info('  批次 %d: 粗筛 %d 篇...', i // batch_size + 1, len(batch))

        papers_info = []
        for j, p in enumerate(batch, 1):
            authors_str = ', '.join(p['authors'][:2])
            if len(p['authors']) > 2:
                authors_str += f' 等{len(p["authors"])}人'
            # 摘要截取 800 字（足够判断相关性）
            papers_info.append(
                f"论文{j}：\n标题：{p['title']}\n作者：{authors_str}\n摘要：{p['summary'][:800]}"
            )

        batch_prompt = f"""你是电机控制领域导师。为以下{len(batch)}篇论文打分。

{chr(10).join(papers_info)}

严格按以下 JSON 数组格式输出，不要输出其他内容：
[
  {{"id":1,"score":0-10,"reason_zh":"一句话中文理由"}},
  ...
]

评分标准（全部用中文回答）：
- 8-10分：直接研究FOC/SMO/PMSM/BLDC控制、无传感器、电机驱动
- 6-7分：间接相关（通用控制理论应用到电机、嵌入式AI推理）
- 4-5分：边缘相关
- 0-3分：不相关（纯机器人、纯深度学习应用、时间序列预测等）
- 如果文章主体不是技术内容（如产品营销、市场分析），直接给0分"""

        result = call_deepseek(batch_prompt, max_tokens=600)
        try:
            match = re.search(r'\[[\s\S]+\]', result)
            if match:
                batch_results = json.loads(match.group(0))
                for j, p in enumerate(batch):
                    if j < len(batch_results):
                        r = batch_results[j]
                        r['paper'] = p
                        results.append(r)
                    else:
                        results.append(_fallback_result(p))
        except Exception as e:
            logger.error('批量解析失败: %s', e)
            for p in batch:
                results.append(_fallback_result(p))

        if i + batch_size < len(papers):
            time.sleep(1)
    return results


def _fallback_result(p):
    return {
        'score': 5.0, 'reason_zh': '分析失败',
        'paper': p,
    }


# ============== 新闻评估 ==============
def batch_evaluate_news(news_list):
    if not news_list:
        return []
    results = []
    funding_kw = CFG['news']['funding_keywords']

    for i in range(0, len(news_list), 5):
        batch = news_list[i:i + 5]
        # 规则预筛融资类，直接给0分，不进DeepSeek
        non_funding_batch = []
        for n in batch:
            text = (n['title'] + ' ' + n['summary']).lower()
            if any(kw.lower() in text for kw in funding_kw):
                results.append((0, n, '融资/估值类'))
            else:
                non_funding_batch.append(n)

        if not non_funding_batch:
            continue

        news_info = [f"新闻{j}：标题：{n['title']}\n摘要：{n['summary'][:300]}" for j, n in enumerate(non_funding_batch, 1)]
        batch_prompt = f"""你是电机控制领域的严格编辑。评估以下{len(non_funding_batch)}条新闻的价值。

{chr(10).join(news_info)}

严格按以下 JSON 数组输出：
[
  {{"id":1,"score":0-10,"reason_zh":"一句话中文理由"}},
  ...
]

评分标准（全部用中文回答）：
- 8-10分：直接报道电机控制/FOC/SMO/无传感器/电机驱动的技术突破
- 6-7分：嵌入式AI推理加速、边缘计算在电机控制中的应用、SiC/GaN功率器件
- 5分及以下：不推

**一票否决（直接0分）：**
1. 文章主体是产品营销/新品发布/市场分析（哪怕提到了AI/电机关键词）
2. 只是提了一嘴AI/电机，但核心内容是其他领域
3. 融资/上市/估值类新闻
4. 电动车产品评测/导购（不是技术内容）
5. 标题含"发布""上市""开售""预售""评测""体验"等营销词汇

判断方法：看文章标题和摘要的核心主题是什么，而不是有没有出现关键词"""

        result = call_deepseek(batch_prompt, max_tokens=400)
        try:
            match = re.search(r'\[[\s\S]+\]', result)
            if match:
                batch_results = json.loads(match.group(0))
                for j, n in enumerate(non_funding_batch):
                    if j < len(batch_results):
                        r = batch_results[j]
                        results.append((r.get('score', 0), n, r.get('reason_zh', '')))
                    else:
                        results.append((0, n, '分析失败'))
        except Exception as e:
            logger.error('批量新闻解析失败: %s', e)
            for n in non_funding_batch:
                results.append((0, n, '分析失败'))

        if i + 5 < len(news_list):
            time.sleep(0.5)
    return results


def generate_news_summary(news_item):
    prompt = f"""你是一个科技新闻编辑。请把下面这条新闻改写为"对电机控制研究生有用"的中文简报。

【新闻标题】
{news_item['title']}

【新闻摘要】
{news_item['summary']}

【要求】
1. 中文摘要（60-100 字）
2. "对你的价值"（30-50 字）

严格按 JSON 输出：
{{
  "summary_zh": "中文摘要（60-100 字）",
  "value_zh": "对你的价值（30-50 字）"
}}"""
    result = call_deepseek(prompt, max_tokens=400)
    try:
        match = re.search(r'\{[\s\S]+\}', result)
        if match:
            return json.loads(match.group(0))
    except Exception as e:
        logger.warning('解析新闻摘要失败: %s', e)
    return {'summary_zh': news_item.get('summary', '')[:200], 'value_zh': '暂未分析'}


# ============== GitHub ==============
def fetch_github_trending():
    trending = []
    for lang in CFG['github']['languages']:
        try:
            html = _http_request(
                f'https://github.com/trending/{lang}?since=daily',
                headers={'User-Agent': 'Mozilla/5.0'},
                timeout=20,
            )

            articles = re.findall(r'<article[^>]*class="Box-row"[^>]*>(.*?)</article>', html, re.DOTALL)
            logger.info('GitHub Trending (%s): 找到 %d 个仓库', lang, len(articles))

            for article in articles[:CFG['github']['max_per_lang']]:
                path_match = re.search(r'<h2[^>]*>.*?<a[^>]*href="/([^"]+)"', article, re.DOTALL)
                if not path_match:
                    continue
                repo_path = path_match.group(1).strip()

                name_match = re.search(
                    r'<h2[^>]*>.*?<a[^>]*href="/' + re.escape(repo_path) + r'"[^>]*>(.*?)</a>',
                    article, re.DOTALL
                )
                repo_name = re.sub(r'<[^>]+>', '', name_match.group(1)).strip() if name_match else repo_path
                repo_name = re.sub(r'\s+', ' ', repo_name)

                desc_match = re.search(r'<p[^>]*class="col-9[^"]*"[^>]*>(.*?)</p>', article, re.DOTALL)
                desc = re.sub(r'<[^>]+>', '', desc_match.group(1)).strip() if desc_match else ''
                desc = re.sub(r'\s+', ' ', desc)

                star_match = re.search(
                    r'href="/' + re.escape(repo_path) + r'/stargazers"[^>]*>.*?>([\d,]+)<',
                    article, re.DOTALL
                )
                stars = star_match.group(1) if star_match else '?'

                trending.append({
                    'source': f'GitHub Trending ({lang})',
                    'title': f'{stars} {repo_name}',
                    'summary': desc[:400] if desc else f'一个 {lang} 项目，最近获得关注',
                    'link': f'https://github.com/{repo_path}',
                    'language': lang, 'repo_path': repo_path,
                })
        except Exception as e:
            logger.error('GitHub Trending (%s) 抓取失败: %s', lang, e)

    logger.info('GitHub Trending 总计: %d 个仓库', len(trending))
    return trending


def batch_evaluate_github(repos):
    if not repos:
        return []
    results = []
    for i in range(0, len(repos), 5):
        batch = repos[i:i + 5]
        repos_info = [f"仓库{j}：{r['title']}\n描述：{r['summary'][:200]}" for j, r in enumerate(batch, 1)]
        batch_prompt = f"""评估以下{len(batch)}个GitHub仓库。核心问题：这个项目是否代表了新的工作方式或能力？

{chr(10).join(repos_info)}

严格按以下 JSON 数组输出：
[
  {{"id":1,"score":0-10,"reason_zh":"一句话中文理由"}},
  ...
]

评分标准（全部用中文回答）：
- 8-10分：颠覆性项目，代表新范式（如 ai agent 框架、本地大模型运行、AI 编程工具）
- 6-7分：有潜力的新工具，可能改变部分工作方式
- 5分及以下：不推

**一票否决（直接0分）：**
1. 纯 UI 框架/CSS 框架/设计系统
2. 纯模板/脚手架/admin 后台
3. 纯 CLI 工具/开发辅助（除非跟 AI 深度结合）
4. 只是已有工具的替代品，没有新意

关键判断: 不是有用, 而是是不是新东西, 会不会改变大家的工作方式"""

        result = call_deepseek(batch_prompt, max_tokens=400)
        try:
            match = re.search(r'\[[\s\S]+\]', result)
            if match:
                batch_results = json.loads(match.group(0))
                for j, r in enumerate(batch):
                    if j < len(batch_results):
                        res = batch_results[j]
                        results.append((res.get('score', 0), r, res.get('reason_zh', '')))
                    else:
                        results.append((0, r, '分析失败'))
        except Exception as e:
            logger.error('批量GitHub解析失败: %s', e)
            for r in batch:
                results.append((0, r, '分析失败'))

        if i + 5 < len(repos):
            time.sleep(0.5)
    return results


def generate_github_summary(repo):
    prompt = f"""请把下面这个 GitHub 仓库改写为"对电机控制研究生有用"的中文介绍。

【仓库名】{repo['title']}
【仓库描述（英文）】{repo['summary']}

【要求】
1. 中文介绍（80-120 字）
2. "对你价值"（30-50 字）

严格按 JSON 输出：
{{
  "summary_zh": "中文介绍（80-120 字）",
  "value_zh": "对你价值（30-50 字）"
}}"""
    result = call_deepseek(prompt, max_tokens=400)
    try:
        match = re.search(r'\{[\s\S]+\}', result)
        if match:
            return json.loads(match.group(0))
    except Exception as e:
        logger.warning('解析 GitHub 摘要失败: %s', e)
    return {'summary_zh': repo.get('summary', '')[:200], 'value_zh': '暂未分析'}


# ============== 新闻抓取 ==============
def fetch_news():
    all_news = []
    max_per = CFG['news']['max_per_source']
    for source_name, url in NEWS_FEEDS:
        logger.info('抓取 %s...', source_name)
        try:
            # 先用带超时的 HTTP 请求获取内容，再解析
            raw = _http_request(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20, retries=2)
            feed = feedparser.parse(raw)
            for entry in feed.entries[:max_per]:
                all_news.append({
                    'source': source_name,
                    'title': entry.title,
                    'summary': entry.get('summary', entry.get('description', ''))[:500],
                    'link': entry.get('link', ''),
                })
        except Exception as e:
            logger.warning('抓 %s 失败（跳过）: %s', source_name, e)
    logger.info('共 %d 条', len(all_news))
    return all_news


# ============== 推送 ==============
def push_to_wechat(content):
    encoded_title = urllib.parse.quote('每日 AI 简报')
    encoded_content = urllib.parse.quote(content)
    url = f'https://sctapi.ftqq.com/{SERVERCHAN_SENDKEY}.send?title={encoded_title}&desp={encoded_content}'
    return _http_request(url, timeout=30)


# ============== 格式化输出 ==============
def format_paper_section(paper_data, index, total):
    paper = paper_data.get('paper', {})
    score = paper_data.get('score', 0)

    if score >= 8:
        read_level = '强烈建议精读'
    elif score >= 6:
        read_level = '建议阅读'
    else:
        read_level = '可选阅读'

    source_parts = []
    if paper.get('journal_ref'):
        source_parts.append(paper['journal_ref'])
    if paper.get('comment'):
        source_parts.append(paper['comment'][:50])
    source_str = ' | '.join(source_parts) if source_parts else 'arXiv预印本'

    title_zh = paper_data.get('title_zh', '')
    title_en = paper.get('title', '')
    title_line = f'{title_zh}（{title_en}）' if title_zh else title_en

    section = f"""** [{index}/{total}] [{score:.1f}/10] {read_level}**

**来源**: {source_str}
**标题**: {title_line}

**[一句话痛点]**
{paper_data.get('pain_point', '需阅读全文')}

**[核心方法]**
{paper_data.get('method', '需阅读全文')}

**[关键数字]**
{paper_data.get('result', '需阅读全文')}

**[对比对象]**
{paper_data.get('benchmark', '需阅读全文')}

**[局限性]**
{paper_data.get('limitation', '需阅读全文')}

**[复现成本]**
{paper_data.get('reproducibility', '需阅读全文')}

**[行动建议]**
{paper_data.get('action', '建议快速浏览')}

{paper_data.get('summary_zh', '')}

[arXiv: {paper.get('arxiv_id', '')}]({paper.get('link', '')})

"""
    return section


# ============== 主流程 ==============
def main():
    logger.info('=' * 50)
    logger.info('开始生成每日 AI 简报 %s', datetime.now().strftime('%Y-%m-%d %H:%M'))
    logger.info('=' * 50)

    today = datetime.now().strftime('%Y-%m-%d')
    db = DedupDB()

    # 清理旧记录
    db.cleanup(keep_days=30)

    # ====== 1. 抓 arXiv 论文 ======
    logger.info('[1/6] 抓取 arXiv 论文...')
    all_papers = fetch_all_arxiv()
    logger.info('总计 %d 篇候选', len(all_papers))

    # ====== 2. 分层筛选 ======
    logger.info('[2/6] 分层筛选...')
    high_priority, medium_priority = smart_filter(all_papers)
    cfg_paper = CFG['paper']
    candidate_papers = high_priority[:8] + medium_priority[:7]

    # 去重：过滤已推送的论文
    candidate_papers = [
        p for p in candidate_papers
        if not db.is_pushed('paper', p['arxiv_id'], within_days=3)
    ]
    logger.info('去重后候选: %d 篇', len(candidate_papers))

    # ====== 3. 批量分析 ======
    logger.info('[3/6] 批量分析论文...')
    analyzed_papers = batch_analyze_papers(candidate_papers, batch_size=cfg_paper['batch_size'])
    analyzed_papers.sort(key=lambda x: -x.get('score', 0))

    # 按搜索方向选论文：FOC核心 1 篇 + 观测器数学 1 篇
    foc_paper, observer_paper = None, None
    for p_data in analyzed_papers:
        p = p_data.get('paper', {})
        score = p_data.get('score', 0)
        direction = p.get('search_source', '')
        if score < cfg_paper['high_priority_min_score']:
            continue
        if not foc_paper and direction == 'foc_core':
            foc_paper = p_data
        elif not observer_paper and direction == 'observer_math':
            observer_paper = p_data

    # 兜底：如果某个方向没找到高分论文，取该方向最高分
    if not foc_paper:
        for p_data in analyzed_papers:
            if p_data.get('paper', {}).get('search_source') == 'foc_core':
                foc_paper = p_data
                break
    if not observer_paper:
        for p_data in analyzed_papers:
            if p_data.get('paper', {}).get('search_source') == 'observer_math':
                observer_paper = p_data
                break

    logger.info('选中: FOC核心=%s, 观测器数学=%s', foc_paper is not None, observer_paper is not None)

    # ====== 4. 单篇精读 ======
    logger.info('[4/6] 单篇精读（Top 2）...')
    final_content = f'**每日 AI 简报** | {today}\n\n'

    # 对选中的论文做单篇深度分析
    deep_analyzed = []
    for label, paper_data in [('FOC核心', foc_paper), ('观测器/数学', observer_paper)]:
        if not paper_data:
            continue
        p = paper_data.get('paper', {})
        logger.info('  精读 [%s]: %s', label, p.get('title', '')[:50])
        deep_result = analyze_paper(p)
        deep_result['paper'] = p
        deep_result['direction'] = label
        deep_result['coarse_score'] = paper_data.get('score', 0)
        deep_analyzed.append(deep_result)

    # 格式化输出
    for i, paper_data in enumerate(deep_analyzed, 1):
        final_content += '---\n'
        final_content += f'**[论文 {i}/{len(deep_analyzed)}] {paper_data["direction"]}**\n\n'
        final_content += format_paper_section(paper_data, i, len(deep_analyzed))
        p = paper_data.get('paper', {})
        db.mark_pushed(today, 'paper', p.get('arxiv_id', ''), p.get('title', ''), paper_data.get('score', 0))

    # ====== 5. AI 新闻 ======
    logger.info('[5/6] 抓取 AI 新闻...')
    all_news = fetch_news()
    ai_news = filter_news_keywords(all_news)
    logger.info('AI 相关: %d 条', len(ai_news))

    news_scores = batch_evaluate_news(ai_news[:CFG['news']['eval_max_news']])
    news_scores.sort(key=lambda x: -x[0])
    min_score = CFG['news']['min_score']
    final_count = CFG['news']['final_count']
    top_news = [(s, n, r) for s, n, r in news_scores if s >= min_score][:final_count]

    if top_news:
        final_content += '---\n'
        final_content += '**[科技圈新动态]**\n\n'
        for i, (score, n, reason) in enumerate(top_news, 1):
            logger.info('  生成新闻摘要 %d/%d...', i, len(top_news))
            news_data = generate_news_summary(n)
            final_content += f'**{i}. [{score:.1f}/10] {n["title"]}**\n'
            final_content += f'   来源：{n["source"]}\n'
            if reason:
                final_content += f'   推荐理由：{reason}\n'
            final_content += f'   {news_data.get("summary_zh", "")}\n'
            final_content += f'   对你价值：{news_data.get("value_zh", "")}\n'
            if n['link']:
                final_content += f'   [查看原文]({n["link"]})\n'
            final_content += '\n'
            # 记录已推送
            news_key = hashlib.md5(n['title'].encode()).hexdigest()[:12]
            db.mark_pushed(today, 'news', news_key, n['title'], score)

    # ====== 6. GitHub Trending ======
    logger.info('[6/6] 抓取 GitHub Trending...')
    github_trending = fetch_github_trending()

    if github_trending:
        logger.info('  批量评估 GitHub 仓库...')
        gh_scores = batch_evaluate_github(github_trending)
        gh_scores.sort(key=lambda x: -x[0])
        gh_min = CFG['github']['min_score']
        gh_final = CFG['github']['final_count']
        top_gh = [(s, r, reason) for s, r, reason in gh_scores if s >= gh_min][:gh_final]

        if top_gh:
            final_content += '---\n'
            final_content += '**[GitHub 热门项目]**\n\n'
            for i, (score, repo, reason) in enumerate(top_gh, 1):
                logger.info('  生成 GitHub 中文介绍 %d/%d...', i, len(top_gh))
                gh_data = generate_github_summary(repo)
                final_content += f'**{i}. [{score:.1f}/10] {repo["title"]}**\n'
                final_content += f'   {repo["source"]}\n'
                if reason:
                    final_content += f'   {reason}\n'
                final_content += f'   {gh_data.get("summary_zh", repo.get("summary", ""))[:300]}\n'
                final_content += f'   对你价值：{gh_data.get("value_zh", "")}\n'
                if repo.get('link'):
                    final_content += f'   [查看仓库]({repo["link"]})\n'
                final_content += '\n'
                # 记录已推送
                db.mark_pushed(today, 'github', repo.get('repo_path', ''), repo.get('title', ''), score)

    # ====== 7. 推送 ======
    logger.info('[推送] 发送到微信...')
    if not SERVERCHAN_SENDKEY:
        logger.warning('SERVERCHAN_SENDKEY 未设置，跳过推送。简报已生成但未发送。')
        # 保存到本地文件
        output_path = Path(__file__).parent / f'briefing_{today}.md'
        output_path.write_text(final_content, encoding='utf-8')
        logger.info('简报已保存到: %s', output_path)
    else:
        try:
            result = push_to_wechat(final_content)
            logger.info('推送结果: %s', result)
        except Exception as e:
            logger.error('推送失败: %s', e)
            # 推送失败也保存到本地
            output_path = Path(__file__).parent / f'briefing_{today}.md'
            output_path.write_text(final_content, encoding='utf-8')
            logger.info('简报已备份到: %s', output_path)

    # 打印统计
    logger.info('-' * 50)
    logger.info('完成')
    stats = db.stats()
    if stats:
        logger.info('推送记录统计（最近）:')
        for date, itype, count in stats:
            logger.info('  %s | %s | %d 条', date, itype, count)

    db.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info('用户中断')
    except Exception as e:
        logger.critical('致命错误: %s', e, exc_info=True)
        sys.exit(1)
