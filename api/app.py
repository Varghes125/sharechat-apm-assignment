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
    """Classifies the headline into a theme."""
    if not HF_TOKEN: return "General"
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    candidate_labels = ["Politics", "Technology", "Entertainment", "Sports", "Finance", "Bizarre", "Health"]
    payload = {"inputs": title, "parameters": {"candidate_labels": candidate_labels}}
    try:
        response = requests.post(HF_API_URL, headers=headers, json=payload, timeout=5)
        return response.json()['labels'][0]
    except: return "News"

def get_dynamic_consensus_tag(target_title, all_titles, ai_category):
    """Picks the keyword that best maps to the identified AI category."""
    def tokenize(text):
        # Stopwords filter out filler words like 'सबकों', 'here's', 'gaya'
        stop_words = {"जानिए", "क्यों", "नहीं", "लिए", "बड़ी", "आज", "कैसे", "with", "from", "that", "here", "gaya", "सबकों"}
        # Clean punctuation to avoid tags like '#तेज़,'
        clean_text = text.replace("?", "").replace("-", " ").replace("|", "").replace(",", "").replace("!", "")
        words = clean_text.lower().split()
        return [w for w in words if len(w) > 3 and w not in stop_words]

    corpus = [tokenize(t) for t in all_titles]
    target_tokens = tokenize(target_title)
    
    if not target_tokens: return "#BharatPulse"

    # Stage 1: TF-IDF to find statistically 'unique' candidates
    num_docs = len(all_titles)
    scores = {}
    for word in set(target_tokens):
        tf = target_tokens.count(word)
        containing_docs = sum(1 for doc in corpus if word in doc)
        idf = math.log(num_docs / (1 + containing_docs))
        scores[word] = tf * idf

    # Get top 3 candidates by statistical weight
    top_candidates = [k for k, v in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:3]]

    # Stage 2: Semantic Alignment (The Consensus Pass)
    # We ask the AI which of these 3 unique words best fits the Category
    if HF_TOKEN and len(top_candidates) > 1:
        headers = {"Authorization": f"Bearer {HF_TOKEN}"}
        payload = {
            "inputs": f"In the context of {ai_category}, the most important word is:",
            "parameters": {"candidate_labels": top_candidates}
        }
        try:
            res = requests.post(HF_API_URL, headers=headers, json=payload, timeout=5)
            best_word = res.json()['labels'][0]
            return f"#{best_word.capitalize()}"
        except: pass

    return f"#{top_candidates[0].capitalize()}"

@app.get("/")
def root():
    return {"message": "Trend Engine Active", "supabase_connected": supabase is not None}

@app.get("/update_trends")
def update_trends():
    if not supabase: return {"error": "Supabase missing"}
    raw_entries = []

    # --- SOURCE 1: GOOGLE NEWS ---
    google_feed = feedparser.parse("https://news.google.com/rss?hl=hi&gl=IN&ceid=IN:hi")
    for entry in google_feed.entries[:8]:
        raw_entries.append({"title": entry.title, "source": "Google News", "score": 95})

    # --- SOURCE 2: X / TWITTER ---
    try:
        req = urllib.request.Request("https://trends24.in/india/feed/", headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as res:
            x_feed = feedparser.parse(res.read())
        for entry in x_feed.entries[:5]:
            raw_entries.append({"title": entry.title, "source": "Twitter/X", "score": 90})
    except: pass

    # --- SOURCE 3: REDDIT ---
    reddit_feed = feedparser.parse("https://www.reddit.com/r/india/hot/.rss")
    for entry in reddit_feed.entries[:5]:
        raw_entries.append({"title": entry.title, "source": "Reddit", "score": 80})

    # --- PROCESSING ---
    all_titles = [item["title"] for item in raw_entries]
    final_trends = []

    for item in raw_entries:
        cat = get_ai_category(item["title"])
        tag = get_dynamic_consensus_tag(item["title"], all_titles, cat)
        final_trends.append({
            "tag_name": tag,
            "description": item["title"][:100],
            "category": cat,
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
    if not supabase: return []
    res = supabase.table("trending_tags").select("*").order("heat_score", desc=True).execute()
    return res.data
