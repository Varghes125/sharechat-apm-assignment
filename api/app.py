from fastapi import FastAPI
from supabase import create_client
import feedparser
import os
import json
import math
import urllib.request
import requests  # Added for Hugging Face API
from collections import Counter

app = FastAPI()

# 1. Initialize Supabase
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase = create_client(url, key) if url and key else None

# 2. Hugging Face Configuration
HF_TOKEN = os.environ.get("HUGGINGFACE_TOKEN")
HF_API_URL = "https://api-inference.huggingface.co/models/facebook/bart-large-mnli"

def get_ai_category(title):
    """Classifies the headline into a theme using Zero-Shot Classification."""
    if not HF_TOKEN:
        return "General"
    
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    # Define themes relevant to the ShareChat "Bharat" audience
    candidate_labels = ["Politics", "Technology", "Entertainment", "Sports", "Finance", "Bizarre", "Health"]
    
    payload = {
        "inputs": title,
        "parameters": {"candidate_labels": candidate_labels}
    }
    
    try:
        # Standard Hugging Face Inference API call
        response = requests.post(HF_API_URL, headers=headers, json=payload, timeout=5)
        result = response.json()
        # Return the top predicted label
        return result['labels'][0]
    except Exception as e:
        print(f"AI Classification failed: {e}")
        return "News"

def get_tfidf_tag(target_title, all_titles):
    """Extracts the most statistically significant word from a title."""
    def tokenize(text):
        stop_words = {"जानिए", "क्यों", "नहीं", "लिए", "बड़ी", "आज", "कैसे", "with", "from", "that"}
        words = text.replace("?", "").replace("-", " ").replace("|", "").lower().split()
        return [w for w in words if len(w) > 3 and w not in stop_words]

    corpus = [tokenize(t) for t in all_titles]
    target_tokens = tokenize(target_title)
    
    if not target_tokens:
        return "#Trending"

    num_docs = len(all_titles)
    scores = {}

    for word in set(target_tokens):
        tf = target_tokens.count(word)
        containing_docs = sum(1 for doc in corpus if word in doc)
        idf = math.log(num_docs / (1 + containing_docs))
        scores[word] = tf * idf

    if not scores: return "#BharatPulse"
    
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
        raw_entries.append({"title": entry.title, "source": "Google News", "base_cat": "News", "score": 95})

    # --- SOURCE 2: X / TWITTER ---
    x_url = "https://trends24.in/india/feed/"
    try:
        req = urllib.request.Request(x_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            x_feed = feedparser.parse(response.read())
        for entry in x_feed.entries[:5]:
            raw_entries.append({"title": entry.title, "source": "Twitter/X", "base_cat": "Social", "score": 90})
    except: pass

    # --- SOURCE 3: REDDIT ---
    reddit_url = "https://www.reddit.com/r/india/hot/.rss"
    reddit_feed = feedparser.parse(reddit_url)
    for entry in reddit_feed.entries[:5]:
        raw_entries.append({"title": entry.title, "source": "Reddit", "base_cat": "Community", "score": 80})

    # --- TF-IDF & AI PROCESSING ---
    all_titles = [item["title"] for item in raw_entries]
    final_trends = []

    for item in raw_entries:
        # 1. Dynamic Tag Extraction (TF-IDF)
        smart_tag = get_tfidf_tag(item["title"], all_titles)
        
        # 2. Intelligent Categorization (Zero-Shot AI)
        # We pass the title to the AI to get a precise category
        ai_category = get_ai_category(item["title"])
        
        final_trends.append({
            "tag_name": smart_tag,
            "description": item["title"][:100],
            "category": ai_category,  # Replaces the static base_cat with AI result
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
