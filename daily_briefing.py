import os
import urllib.parse
import urllib.request
import feedparser
from datetime import datetime

# 从 GitHub Secrets 读配置
SERVERCHAN_SENDKEY = os.environ['SERVERCHAN_SENDKEY']
DEEPSEEK_API_KEY = os.environ['DEEPSEEK_API_KEY']

# RSS 源（你可以改）
RSS_FEEDS = [
    "https://export.arxiv.org/rss/cs.SY",  # 系统控制
    "https://export.arxiv.org/rss/cs.RO",  # 机器人
    "https://export.arxiv.org/rss/cs.AI",  # AI
]

def fetch_items():
    """抓 RSS"""
    items = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                items.append({
                    "title": entry.title,
                    "summary": entry.get("summary", "")[:300]
                })
        except Exception as e:
            print(f"抓 {url} 失败: {e}")
    return items

def push_to_wechat(title, content):
    """通过 Server 酱推送到个人微信（GET 方式，最稳）"""
    # 把标题和内容 URL 编码
    encoded_title = urllib.parse.quote(title)
    encoded_content = urllib.parse.quote(content)
    url = f"https://sctapi.ftqq.com/{SERVERCHAN_SENDKEY}.send?title={encoded_title}&desp={encoded_content}"
    
    req = urllib.request.Request(url, method='GET')
    with urllib.request.urlopen(req, timeout=30) as response:
        result = response.read().decode('utf-8')
    return result

if __name__ == "__main__":
    print(f"使用 SendKey: {SERVERCHAN_SENDKEY[:8]}...")
    print("📡 抓取 RSS...")
    items = fetch_items()
    print(f"  → 共 {len(items)} 条")
    
    today = datetime.now().strftime('%Y-%m-%d')
    title = f"每日 AI 简报 {today}"
    
    content = f"## 📰 每日 AI + 电机控制简报\n\n> {today}\n\n"
    
    for i, item in enumerate(items[:10], 1):
        content += f"**{i}. {item['title']}**\n\n"
        content += f"{item['summary']}\n\n---\n\n"
    
    print("📤 推送个人微信...")
    result = push_to_wechat(title, content)
    print(f"  → {result}")
    print("✅ 完成")
