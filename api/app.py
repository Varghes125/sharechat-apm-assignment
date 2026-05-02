from fastapi import FastAPI
from supabase import create_client
import feedparser
import os
import json
import math
import urllib.request
import requests
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
    candidate_labels = ["Politics", "Technology", "Entertainment", "Sports", "Finance", "Bizarre", "Health"]
    
    payload = {
        "inputs": title,
        "parameters": {"candidate_labels": candidate_labels}
    }
    
    try:
        response = requests.post(HF_API_URL, headers=headers, json=payload, timeout=5)
        result = response.json()
        return result['labels'][0]
    except Exception as e:
        print(f"AI Classification failed: {e}")
        return "News"

def get_dynamic_consensus_tag(target_title, all_titles, ai_category):
    """Extracts keywords via TF-IDF and uses AI to pick the one best matching the theme."""
    
    def tokenize(text):
        stop_words = {"जानिए", "क्यों", "नहीं", "लिए", "बड़ी", "आज", "कैसे", "with", "from", "that"}
        words = text.replace("?", "").replace("-", " ").replace("|", "").lower().split()
        return [w for w in words if len(w) > 3 and w not in stop_words]

    corpus = [tokenize(t) for t in all_titles]
    target_tokens = tokenize(target_title)
    
    if not target_tokens:
        return "#Trending"

    # 1. Calculate TF-IDF scores to find candidates
    num_docs = len(all_titles)
    scores = {}
    for word in set(target_tokens):
        tf = target_tokens.count(word)
        containing_docs = sum(1 for doc in corpus if word in doc)
        idf = math.log(num_docs / (1 + containing_docs))
        scores[word] = tf * idf

    # Get top 3 statistical candidates
    sorted_candidates = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_candidates = [k[0] for k in sorted_candidates[:3]]

    # 2. Dynamic Semantic Mapping
    # Ask AI: Which of these candidates best represents the [ai_category]?
    if HF_TOKEN and len(top_candidates) > 1:
        headers = {"Authorization": f"Bearer {HF_TOKEN}"}
        payload = {
            "inputs": f"The main subject of this {ai_category} news is:",
            "parameters": {"candidate_labels": top_candidates}
        }
        try:
            response = requests.post(HF_API_URL, headers=headers, json=payload, timeout=5)
            result = response.json()
            best_word = result['labels'][0]
            return f"#{best_word.capitalize()}"
        except:
            pass

    # Fallback to highest TF-IDF word
    best_word = top_candidates[0] if top_candidates else "BharatPulse"
    return f"#{best_word.capitalize()}"

@app.get("/")
def root():
    return {"message": "ShareChat Trend Engine Active", "supabase_connected": supabase is not None}

@app.get("/update_trends")
def update_trends():
    if not supabase:
        return {"error": "Supabase credentials not configured"}

    raw_entries = []

    # --- INGESTION ---
    google_url = "https://news.google.com/rss?hl=hi&gl=IN&ceid=IN:hi"
    google_feed = feedparser.parse(google_url)
    for entry in google_feed.entries[:8]:
        raw_entries.append({"title": entry.title, "source": "Google News", "score": 95})

    x_url = "https://trends24.in/india/feed/"
    try:
        req = urllib.request.Request(x_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            x_feed = feedparser.parse(response.read())
        for entry in x_feed.entries[:5]:
            raw_entries.append({"title": entry.title, "source": "Twitter/X", "score": 90})
    except: pass

    reddit_url = "https://www.reddit.com/r/india/hot/.rss"
    reddit_feed = feedparser.parse(reddit_url)
    for entry in reddit_feed.entries[:5]:
        raw_entries.append({"title": entry.title, "source": "Reddit", "score": 80})

    # --- PROCESSING ---
    all_titles = [item["title"] for item in raw_entries]
    final_trends = []

    for item in raw_entries:
        # 1. Identify the broad Theme
        ai_category = get_ai_category(item["title"])
        
        # 2. Extract the Tag that best fits that Theme
        smart_tag = get_dynamic_consensus_tag(item["title"], all_titles, ai_category)
        
        final_trends.append({
            "tag_name": smart_tag,
            "description": item["title"][:100],
            "category": ai_category,
            "heat_score": item["score"],
            "source": item["source"]
        })

    # --- STORAGE ---
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
