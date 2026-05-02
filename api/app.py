from fastapi import FastAPI
from supabase import create_client
import feedparser
import os
import math
import urllib.request
import requests
from collections import Counter

app = FastAPI()

# 1. Initialize Supabase
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase = create_client(url, key) if url and key else None

# 2. Hugging Face Configuration (Ensure HUGGINGFACE_TOKEN is in Vercel)
HF_TOKEN = os.environ.get("HUGGINGFACE_TOKEN")
# We use the MNLI model because it is designed to understand if a phrase matches a sentence's meaning
HF_CLASSIFIER_URL = "https://api-inference.huggingface.co/models/facebook/bart-large-mnli"

def get_ai_category(title):
    """Classifies the headline into a functional theme."""
    if not HF_TOKEN: return "General"
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    candidate_labels = ["Politics", "Technology", "Entertainment", "Sports", "Finance", "International", "Crime"]
    payload = {"inputs": title, "parameters": {"candidate_labels": candidate_labels}}
    try:
        response = requests.post(HF_CLASSIFIER_URL, headers=headers, json=payload, timeout=7)
        return response.json()['labels'][0]
    except: return "News"

def generate_smart_tag(title):
    """Extracts the most relevant 2-4 word theme by ranking candidates via AI."""
    if not HF_TOKEN: 
        return " ".join(title.split()[:2]).title()
    
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    
    # Clean the title: Remove source names, punctuation, and delimiters
    # e.g., "ABP News: West Bengal Election..." -> "West Bengal Election..."
    clean_title = title.split("|")[0].split("-")[0].split(":")[0].strip()
    
    # Generate potential candidate phrases from the headline
    words = clean_title.split()
    candidates = []
    if len(words) >= 2:
        candidates.append(" ".join(words[:2])) # e.g., "West Bengal"
        candidates.append(" ".join(words[:3])) # e.g., "West Bengal Election"
        if len(words) >= 4:
            candidates.append(" ".join(words[:4])) # e.g., "West Bengal Election 2026"
    else:
        candidates = [clean_title]

    # Ask the AI which of these candidates is the most accurate "Subject" of the full title
    payload = {
        "inputs": clean_title,
        "parameters": {"candidate_labels": candidates}
    }
    
    try:
        response = requests.post(HF_CLASSIFIER_URL, headers=headers, json=payload, timeout=7)
        result = response.json()
        # The AI ranks the candidates; result['labels'][0] is the most semantically relevant
        return result['labels'][0].title()
    except:
        return " ".join([w for w in words if len(w) > 3][:2]).title()

@app.get("/")
def root():
    return {"message": "ShareChat Trend Engine Active", "supabase_connected": supabase is not None}

@app.get("/update_trends")
def update_trends():
    if not supabase: return {"error": "Supabase missing"}
    
    # --- 1. BALANCED DATA INGESTION ---
    raw_scraped_data = []

    # Google News (10 items)
    google_feed = feedparser.parse("https://news.google.com/rss?hl=hi&gl=IN&ceid=IN:hi")
    for entry in google_feed.entries[:10]:
        raw_scraped_data.append({"title": entry.title, "source": "Google News", "base_weight": 100})

    # X / Twitter (10 items)
    try:
        req = urllib.request.Request("https://trends24.in/india/feed/", headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as res:
            x_feed = feedparser.parse(res.read())
        for entry in x_feed.entries[:10]:
            raw_scraped_data.append({"title": entry.title, "source": "Twitter/X", "base_weight": 100})
    except: pass

    # Reddit (10 items - Equal importance given)
    reddit_feed = feedparser.parse("https://www.reddit.com/r/india/hot/.rss")
    for entry in reddit_feed.entries[:10]:
        raw_scraped_data.append({"title": entry.title, "source": "Reddit", "base_weight": 100})

    # --- 2. AGGREGATION ENGINE ---
    trend_groups = {}

    for item in raw_scraped_data:
        # 1. Broad Category
        category = get_ai_category(item["title"])
        # 2. Descriptive Multi-word Tag
        smart_tag = generate_smart_tag(item["title"])
        
        # Create a key to group similar topics (e.g., "west bengalelection" = "West Bengal Election")
        group_key = smart_tag.lower().replace(" ", "")
        
        if group_key not in trend_groups:
            trend_groups[group_key] = {
                "tag_name": smart_tag,
                "description": item["title"][:100],
                "category": category,
                "score": 0,
                "sources_involved": set(),
                "mentions": 0
            }
        
        trend_groups[group_key]["mentions"] += 1
        trend_groups[group_key]["sources_involved"].add(item["source"])
        trend_groups[group_key]["score"] += item["base_weight"]

    # --- 3. RANKING & CROSS-SOURCE VALIDATION ---
    final_ranked_list = []
    for key, data in trend_groups.items():
        total_score = data["score"]
        
        # Multiplier: If found on >1 platform, it is a high-confidence trend
        if len(data["sources_involved"]) > 1:
            total_score *= 2.0
            data["description"] = f"Breaking on {', '.join(data['sources_involved'])}: {data['description']}"

        final_ranked_list.append({
            "tag_name": data["tag_name"],
            "description": data["description"],
            "category": data["category"],
            "heat_score": int(total_score),
            "source": "Multiple" if len(data["sources_involved"]) > 1 else list(data["sources_involved"])[0]
        })

    # Take the top 10 trends to ensure only the highest quality items are displayed
    top_output = sorted(final_ranked_list, key=lambda x: x["heat_score"], reverse=True)[:10]

    # --- 4. STORAGE ---
    try:
        supabase.table("trending_tags").delete().neq("tag_name", "placeholder").execute()
        supabase.table("trending_tags").insert(top_output).execute()
        return {"status": "success", "count": len(top_output)}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/get_trends")
def get_trends():
    if not supabase: return []
    res = supabase.table("trending_tags").select("*").order("heat_score", desc=True).execute()
    return res.data
