import os
import feedparser
import requests
from datetime import datetime

# 从 GitHub Secrets 读配置
WECHAT_WEBHOOK = os.environ['WECHAT_WEBHOOK']
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

def push_to_wechat(content):
    """推送到企业微信"""
    resp = requests.post(WECHAT_WEBHOOK, json={
        "msgtype": "markdown",
        "markdown": {"content": content}
    })
    return resp.json()

if __name__ == "__main__":
    print("📡 抓取 RSS...")
    items = fetch_items()
    print(f"  → 共 {len(items)} 条")
    
    today = datetime.now().strftime('%Y-%m-%d')
    content = f"## 📰 每日 AI + 电机控制简报\n\n> {today}\n\n"
    
    for i, item in enumerate(items[:10], 1):
        content += f"**{i}. {item['title']}**\n\n"
        content += f"{item['summary']}\n\n---\n\n"
    
    print("📤 推送企业微信...")
    result = push_to_wechat(content)
    print(f"  → {result}")
    print("✅ 完成")
