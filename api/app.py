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

    # -------- Google Trends --------
    trends_feed = feedparser.parse("https://trends.google.com/trending/rss?geo=IN")
    for entry in trends_feed.entries[:5]:
        raw_data.append({
            "title": entry.title,
            "source": "Google Trends",
            "timestamp": datetime.datetime.utcnow()
        })

    # -------- YouTube (fixed) --------
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
    # Transform (NO SCORING)
    # -------------------------------
    final_output = []

    for item in raw_data:
        title = item["title"]
        tag = generate_smart_tag(title)

        final_output.append({
            "tag_name": tag,
            "description": title,
            "category": classify(title),
            "source": item["source"],
            "created_at": datetime.datetime.utcnow().isoformat()
        })

    # -------------------------------
    # Store
    # -------------------------------
    try:
        supabase.table("trending_tags").delete().neq("tag_name", "placeholder").execute()
        supabase.table("trending_tags").insert(final_output).execute()

        return {
            "status": "success",
            "total_inserted": len(final_output)
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

    res = supabase.table("trending_tags").select("*").execute()
    return res.data
