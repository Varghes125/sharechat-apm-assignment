from fastapi import FastAPI
from supabase import create_client
import feedparser
import os
import re
import datetime
import urllib.request
import requests
from bs4 import BeautifulSoup
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------
# Supabase Setup
# -------------------------------
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase = create_client(url, key) if url and key else None

# -------------------------------
# STOPWORDS & NLP Helpers
# -------------------------------
STOPWORDS = set([
    "the","is","at","which","on","and","a","an","in","to","for","of","with","by",
    "from","that","this","it","as","are","was","were", "live", "updates", "news"
])

def normalize(word):
    return word[:-1] if word.endswith("s") else word

# -------------------------------
# Tag Generator (Multi-word focus)
# -------------------------------
def generate_smart_tag(title):
    # Clean delimiters often found in news (e.g., "ABP News: Title")
    main_part = title.split('|')[0].split('-')[0].split(':')[0].strip()
    clean_title = re.sub(r'[^a-zA-Z0-9\s]', '', main_part).lower()

    words = [normalize(w) for w in clean_title.split() if w not in STOPWORDS]
    # Grab 2-4 words for a more descriptive tag
    tag_words = words[:3] if len(words) >= 3 else words

    return " ".join([w.capitalize() for w in tag_words])

# -------------------------------
# Similarity & Clustering
# -------------------------------
def similarity(tag1, tag2):
    set1 = set(tag1.lower().split())
    set2 = set(tag2.lower().split())
    return len(set1 & set2) / len(set1 | set2) if set1 and set2 else 0

def find_matching_tag(new_tag, existing_tags):
    best_match = None
    best_score = 0
    for tag in existing_tags:
        score = similarity(new_tag, tag)
        if score > best_score:
            best_score = score
            best_match = tag
    return best_match if best_score >= 0.4 else new_tag

# -------------------------------
# Category Logic
# -------------------------------
def classify(text):
    text = text.lower()
    if any(x in text for x in ["cricket", "ipl", "match", "sports", "football"]):
        return "Sports"
    elif any(x in text for x in ["movie", "ott", "trailer", "actor", "bollywood"]):
        return "Entertainment"
    elif any(x in text for x in ["rbi", "market", "stock", "sensex", "finance"]):
        return "Finance"
    elif any(x in text for x in ["election", "bjp", "modi", "congress", "politics"]):
        return "Politics"
    elif any(x in text for x in ["ai", "iphone", "google", "tech", "gadget"]):
        return "Technology"
    return "General"

# -------------------------------
# Scoring Components
# -------------------------------
def recency_score(ts):
    diff = (datetime.datetime.utcnow() - ts).total_seconds()
    return max(0, 1 - diff / 43200)  # 12-hour decay

def fetch_twitter_trends():
    trends = []
    try:
        url = "https://trends24.in/india/"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(response.text, "html.parser")
        trend_lists = soup.find_all("ol")
        for ol in trend_lists[:1]:
            for li in ol.find_all("li")[:10]:
                trend = li.text.strip()
                if trend: trends.append(trend)
    except Exception as e:
        print("Twitter scrape failed:", e)
    return trends

# -------------------------------
# Endpoints
# -------------------------------
@app.get("/")
def root():
    return {"message": "Trend Engine Active", "supabase": supabase is not None}

@app.get("/update_trends")
def update_trends():
    if not supabase:
        return {"error": "Supabase not configured"}

    raw_data = []

    # 1. Google News
    google_feed = feedparser.parse("https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en")
    for entry in google_feed.entries[:10]:
        ts = datetime.datetime(*entry.published_parsed[:6]) if "published_parsed" in entry else datetime.datetime.utcnow()
        raw_data.append({"title": entry.title, "source": "Google News", "timestamp": ts})

    # 2. Reddit India
    reddit_feed = feedparser.parse("https://www.reddit.com/r/india/hot/.rss")
    for entry in reddit_feed.entries[:10]:
        raw_data.append({"title": entry.title, "source": "Reddit", "timestamp": datetime.datetime.utcnow()})

    # 3. Google Trends
    trends_feed = feedparser.parse("https://trends.google.com/trending/rss?geo=IN")
    for i, entry in enumerate(trends_feed.entries[:10]):
        raw_data.append({"title": entry.title, "source": "Google Trends", "timestamp": datetime.datetime.utcnow(), "rank": i + 1})

    # 4. Twitter Trends
    twitter_trends = fetch_twitter_trends()
    for trend in twitter_trends[:10]:
        raw_data.append({"title": trend, "source": "Twitter/X", "timestamp": datetime.datetime.utcnow()})

    # Aggregation
    trend_map = {}
    for item in raw_data:
        new_tag = generate_smart_tag(item["title"])
        matched_tag = find_matching_tag(new_tag, trend_map.keys())

        # Scoring Logic
        rec = recency_score(item["timestamp"])
        if item["source"] == "Google Trends":
            volume_score = 100 # High authority
        elif item["source"] == "Twitter/X":
            volume_score = 80
        else:
            volume_score = 40

        if matched_tag not in trend_map:
            trend_map[matched_tag] = {
                "tag_name": matched_tag,
                "description": item["title"],
                "category": classify(item["title"]),
                "score": 0,
                "mentions": 0
            }

        trend_map[matched_tag]["score"] += (volume_score + (rec * 50))
        trend_map[matched_tag]["mentions"] += 1

    # Final Ranking & Multi-Source Spike
    final_output = []
    for data in trend_map.values():
        total_score = data["score"]
        # Multi-source validation boost
        if data["mentions"] >= 3:
            total_score *= 2.0
        elif data["mentions"] == 2:
            total_score *= 1.5

        final_output.append({
            "tag_name": data["tag_name"],
            "description": data["description"],
            "category": data["category"],
            "heat_score": int(total_score)
        })

    # Sort and strictly take the Top 10
    final_sorted_top_10 = sorted(final_output, key=lambda x: x["heat_score"], reverse=True)[:10]

    # Store in Supabase
    try:
        # Clear existing
        supabase.table("trending_tags").delete().neq("tag_name", "placeholder").execute()
        # Insert only top 10
        if final_sorted_top_10:
            supabase.table("trending_tags").insert(final_sorted_top_10).execute()

        return {
            "status": "success",
            "count": len(final_sorted_top_10),
            "trends": final_sorted_top_10
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/get_trends")
def get_trends():
    if not supabase: return []
    # Always ordered by heat_score, limited by the update logic
    res = supabase.table("trending_tags").select("*").order("heat_score", desc=True).execute()
    return res.data
