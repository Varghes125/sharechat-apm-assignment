from fastapi import FastAPI
from supabase import create_client
import feedparser
import os
import re
import datetime
import urllib.request

app = FastAPI()

# -------------------------------
# Supabase Setup
# -------------------------------
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase = create_client(url, key) if url and key else None

# -------------------------------
# STOPWORDS
# -------------------------------
STOPWORDS = set([
    "the","is","at","which","on","and","a","an","in","to","for","of","with","by",
    "from","that","this","it","as","are","was","were"
])

# -------------------------------
# Normalize words
# -------------------------------
def normalize(word):
    return word[:-1] if word.endswith("s") else word

# -------------------------------
# Tag Generator
# -------------------------------
def generate_smart_tag(title):
    title = title.lower()
    title = re.sub(r'[^a-zA-Z0-9\s]', '', title)

    words = [normalize(w) for w in title.split() if w not in STOPWORDS]
    tag_words = words[:3] if len(words) >= 3 else words

    return " ".join([w.capitalize() for w in tag_words])

# -------------------------------
# Category
# -------------------------------
def classify(text):
    text = text.lower()
    if "cricket" in text or "ipl" in text:
        return "Sports"
    elif "movie" in text or "ott" in text or "youtube" in text:
        return "Entertainment"
    elif "rbi" in text or "market" in text:
        return "Finance"
    elif "election" in text or "bjp" in text:
        return "Politics"
    return "General"

# -------------------------------
# Recency Score
# -------------------------------
def recency_score(ts):
    diff = (datetime.datetime.utcnow() - ts).total_seconds()
    return max(0, 1 - diff / 43200)  # 12 hr decay

# -------------------------------
# Root
# -------------------------------
@app.get("/")
def root():
    return {"message": "Trend Engine Active", "supabase": supabase is not None}

# -------------------------------
# Update Trends
# -------------------------------
@app.get("/update_trends")
def update_trends():
    if not supabase:
        return {"error": "Supabase not configured"}

    raw_data = []

    # -------- Google News --------
    google_feed = feedparser.parse("https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en")
    for entry in google_feed.entries[:5]:
        raw_data.append({
            "title": entry.title,
            "source": "Google News",
            "timestamp": datetime.datetime.utcnow()
        })

    # -------- Reddit --------
    reddit_feed = feedparser.parse("https://www.reddit.com/r/india/hot/.rss")
    for entry in reddit_feed.entries[:5]:
        raw_data.append({
            "title": entry.title,
            "source": "Reddit",
            "timestamp": datetime.datetime.utcnow()
        })

    # -------- Google Trends (IMPORTANT) --------
    trends_feed = feedparser.parse("https://trends.google.com/trending/rss?geo=IN")
    for i, entry in enumerate(trends_feed.entries[:5]):
        raw_data.append({
            "title": entry.title,
            "source": "Google Trends",
            "timestamp": datetime.datetime.utcnow(),
            "rank": i + 1  # lower = more popular
        })

    # -------- YouTube --------
    yt_feed = feedparser.parse(
        "https://www.youtube.com/feeds/videos.xml?search_query=trending+india"
    )
    for entry in yt_feed.entries[:5]:
        raw_data.append({
            "title": entry.title,
            "source": "YouTube",
            "timestamp": datetime.datetime.utcnow()
        })

    # -------------------------------
    # Aggregation
    # -------------------------------
    trend_map = {}

    for item in raw_data:
        tag = generate_smart_tag(item["title"])
        rec = recency_score(item["timestamp"])

        # -------------------------
        # SEARCH VOLUME SCORE
        # -------------------------
        if item["source"] == "Google Trends":
            volume_score = (6 - item["rank"]) * 20  # rank1=100, rank5=20
        else:
            volume_score = 30  # base for other sources

        # -------------------------
        # RECENCY SCORE
        # -------------------------
        rec_score = rec * 50

        # -------------------------
        # INIT
        # -------------------------
        if tag not in trend_map:
            trend_map[tag] = {
                "tag_name": tag,
                "description": item["title"],
                "category": classify(item["title"]),
                "score": 0,
                "mentions": 0
            }

        trend_map[tag]["score"] += (volume_score + rec_score)
        trend_map[tag]["mentions"] += 1

    # -------------------------------
    # SPIKE DETECTION
    # -------------------------------
    final_output = []

    for data in trend_map.values():
        total_score = data["score"]

        # spike bonus
        if data["mentions"] >= 3:
            total_score *= 1.5
        elif data["mentions"] == 2:
            total_score *= 1.2

        final_output.append({
            "tag_name": data["tag_name"],
            "description": data["description"],
            "category": data["category"],
            "heat_score": int(total_score),
            "mentions": data["mentions"]
        })

    # sort
    final_sorted = sorted(final_output, key=lambda x: x["heat_score"], reverse=True)

    # -------------------------------
    # Store
    # -------------------------------
    try:
        supabase.table("trending_tags").delete().neq("tag_name", "placeholder").execute()
        supabase.table("trending_tags").insert(final_sorted).execute()

        return {
            "status": "success",
            "top_10": final_sorted[:10]
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}

# -------------------------------
# Get Trends
# -------------------------------
@app.get("/get_trends")
def get_trends():
    if not supabase:
        return []

    res = supabase.table("trending_tags").select("*").order("heat_score", desc=True).execute()
    return res.data
