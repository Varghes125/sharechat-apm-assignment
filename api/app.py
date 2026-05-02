from fastapi import FastAPI
from supabase import create_client
import feedparser
import os
import json
import math
import urllib.request
from collections import Counter

app = FastAPI()

# 1. Initialize Supabase
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase = create_client(url, key) if url and key else None

def get_tfidf_tag(target_title, all_titles):
    """Extracts the most statistically significant word from a title."""
    def tokenize(text):
        # Basic cleaning and stopword removal (Hindi + English)
        stop_words = {"जानिए", "क्यों", "नहीं", "लिए", "बड़ी", "आज", "कैसे", "with", "from", "that"}
        # Remove punctuation and split
        words = text.replace("?", "").replace("-", " ").replace("|", "").lower().split()
        return [w for w in words if len(w) > 3 and w not in stop_words]

    corpus = [tokenize(t) for t in all_titles]
    target_tokens = tokenize(target_title)
    
    if not target_tokens:
        return "#Trending"

    num_docs = len(all_titles)
    scores = {}

    for word in set(target_tokens):
        # TF: count in current title
        tf = target_tokens.count(word)
        # IDF: rarity across all scraped titles
        containing_docs = sum(1 for doc in corpus if word in doc)
        idf = math.log(num_docs / (1 + containing_docs))
        scores[word] = tf * idf

    if not scores: return "#BharatPulse"
    
    # Pick the word with the highest statistical weight
    best_word = max(scores, key=scores.get)
    return f"#{best_word.capitalize()}"

@app.get("/")
def root():
    return {"message": "ShareChat Trend Engine Active", "supabase_connected": supabase is not None}

@app.get("/update_trends")
def update_trends():
    if not supabase:
        return {"error": "Supabase credentials not configured"}

    raw_entries = []

    # --- SOURCE 1: GOOGLE NEWS ---
    google_url = "https://news.google.com/rss?hl=hi&gl=IN&ceid=IN:hi"
    google_feed = feedparser.parse(google_url)
    for entry in google_feed.entries[:8]:
        raw_entries.append({"title": entry.title, "source": "Google News", "cat": "News", "score": 95})

    # --- SOURCE 2: X / TWITTER ---
    x_url = "https://trends24.in/india/feed/" # Added /feed/ for RSS compatibility
    try:
        req = urllib.request.Request(x_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            x_feed = feedparser.parse(response.read())
        for entry in x_feed.entries[:5]:
            raw_entries.append({"title": entry.title, "source": "Twitter/X", "cat": "Social", "score": 90})
    except: pass

    # --- SOURCE 3: REDDIT ---
    reddit_url = "https://www.reddit.com/r/india/hot/.rss"
    reddit_feed = feedparser.parse(reddit_url)
    for entry in reddit_feed.entries[:5]:
        raw_entries.append({"title": entry.title, "source": "Reddit", "cat": "Community", "score": 80})

    # --- TF-IDF PROCESSING ---
    all_titles = [item["title"] for item in raw_entries]
    final_trends = []

    for item in raw_entries:
        smart_tag = get_tfidf_tag(item["title"], all_titles)
        final_trends.append({
            "tag_name": smart_tag,
            "description": item["title"][:100],
            "category": item["cat"],
            "heat_score": item["score"],
            "source": item["source"]
        })

    try:
        supabase.table("trending_tags").delete().neq("tag_name", "placeholder").execute()
        supabase.table("trending_tags").insert(final_trends).execute()
        
        return {"status": "success", "count": len(final_trends)}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/get_trends")
def get_trends():
    if not supabase: return {"error": "Supabase not connected"}
    response = supabase.table("trending_tags").select("*").order("heat_score", desc=True).limit(15).execute()
    return response.data
