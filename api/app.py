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
    if not HF_TOKEN: return "General"
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    candidate_labels = ["Politics", "Technology", "Entertainment", "Sports", "Finance", "Bizarre", "Health"]
    payload = {"inputs": title, "parameters": {"candidate_labels": candidate_labels}}
    try:
        response = requests.post(HF_API_URL, headers=headers, json=payload, timeout=5)
        return response.json()['labels'][0]
    except: return "News"

def get_dynamic_consensus_tag(target_title, all_titles, ai_category):
    def tokenize(text):
        stop_words = {"जानिए", "क्यों", "नहीं", "लिए", "बड़ी", "आज", "कैसे", "with", "from", "that", "here", "gaya", "सबकों"}
        clean_text = text.replace("?", "").replace("-", " ").replace("|", "").replace(",", "").replace("!", "")
        words = clean_text.lower().split()
        return [w for w in words if len(w) > 3 and w not in stop_words]

    corpus = [tokenize(t) for t in all_titles]
    target_tokens = tokenize(target_title)
    if not target_tokens: return "#BharatPulse"

    num_docs = len(all_titles)
    scores = {}
    for word in set(target_tokens):
        tf = target_tokens.count(word)
        containing_docs = sum(1 for doc in corpus if word in doc)
        idf = math.log(num_docs / (1 + containing_docs))
        scores[word] = tf * idf

    top_candidates = [k for k, v in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:3]]

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
    
    # --- 1. COLLECT RAW DATA ---
    raw_scraped_data = []

    # Google News
    google_feed = feedparser.parse("https://news.google.com/rss?hl=hi&gl=IN&ceid=IN:hi")
    for entry in google_feed.entries[:15]: # Increased limit for better correlation
        raw_scraped_data.append({"title": entry.title, "source": "Google News", "base_weight": 95})

    # X / Twitter
    try:
        req = urllib.request.Request("https://trends24.in/india/feed/", headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as res:
            x_feed = feedparser.parse(res.read())
        for entry in x_feed.entries[:10]:
            raw_scraped_data.append({"title": entry.title, "source": "Twitter/X", "base_weight": 90})
    except: pass

    # Reddit
    reddit_feed = feedparser.parse("https://www.reddit.com/r/india/hot/.rss")
    for entry in reddit_feed.entries[:10]:
        raw_scraped_data.append({"title": entry.title, "source": "Reddit", "base_weight": 80})

    # --- 2. THE AGGREGATION & CORRELATION ENGINE ---
    all_titles = [item["title"] for item in raw_scraped_data]
    trend_groups = {} # Dictionary to group by Smart Tag

    for item in raw_scraped_data:
        cat = get_ai_category(item["title"])
        tag = get_dynamic_consensus_tag(item["title"], all_titles, cat)
        
        if tag not in trend_groups:
            trend_groups[tag] = {
                "tag_name": tag,
                "description": item["title"][:100],
                "category": cat,
                "score_accumulator": 0,
                "sources_involved": set(),
                "mentions": 0
            }
        
        # Calculate individual mention weight
        trend_groups[tag]["mentions"] += 1
        trend_groups[tag]["sources_involved"].add(item["source"])
        # Each mention adds (Base Weight + 10 points frequency bonus)
        trend_groups[tag]["score_accumulator"] += (item["base_weight"] + 10)

    # --- 3. THE TREND FILTER & RANKER ---
    final_ranked_list = []
    for tag, data in trend_groups.items():
        total_score = data["score_accumulator"]
        
        # PARAMETER: Cross-Platform Multiplier
        # If a topic is found on >1 platform, boost the score by 1.5x
        if len(data["sources_involved"]) > 1:
            total_score *= 1.5
            data["description"] = f"Viral across {', '.join(data['sources_involved'])}: {data['description']}"

        final_ranked_list.append({
            "tag_name": data["tag_name"],
            "description": data["description"],
            "category": data["category"],
            "heat_score": int(total_score),
            "source": "Multiple" if len(data["sources_involved"]) > 1 else list(data["sources_involved"])[0]
        })

    # SORT and LIMIT: Take only the Top 10 trends to filter out noise
    top_trends = sorted(final_ranked_list, key=lambda x: x["heat_score"], reverse=True)[:10]

    # --- 4. STORAGE ---
    try:
        supabase.table("trending_tags").delete().neq("tag_name", "placeholder").execute()
        supabase.table("trending_tags").insert(top_trends).execute()
        return {"status": "success", "unique_trends_saved": len(top_trends)}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/get_trends")
def get_trends():
    if not supabase: return []
    res = supabase.table("trending_tags").select("*").order("heat_score", desc=True).execute()
    return res.data
