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

# 2. Hugging Face Configuration
HF_TOKEN = os.environ.get("HUGGINGFACE_TOKEN")
# Using a model better suited for multi-word extraction/summarization
HF_SUMMARIZER_URL = "https://api-inference.huggingface.co/models/facebook/bart-large-cnn"
HF_CLASSIFIER_URL = "https://api-inference.huggingface.co/models/facebook/bart-large-mnli"

def get_ai_category(title):
    """Identifies the broad category of the news."""
    if not HF_TOKEN: return "General"
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    candidate_labels = ["Politics", "Technology", "Entertainment", "Sports", "Finance", "International", "Health"]
    payload = {"inputs": title, "parameters": {"candidate_labels": candidate_labels}}
    try:
        response = requests.post(HF_CLASSIFIER_URL, headers=headers, json=payload, timeout=7)
        return response.json()['labels'][0]
    except: return "News"

def generate_smart_tag(title):
    """Generates a concise, multi-word tag representing the core entity/event."""
    if not HF_TOKEN: return "#Trending"
    
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    # We prompt the model to treat the title as a prompt for a short entity name
    payload = {
        "inputs": f"Extract the main subject or event as a 2-4 word title: {title}",
        "parameters": {"max_length": 10, "min_length": 2, "do_sample": False}
    }
    
    try:
        response = requests.post(HF_SUMMARIZER_URL, headers=headers, json=payload, timeout=7)
        summary = response.json()[0]['summary_text']
        # Clean up the output to keep it tag-like
        tag = summary.replace("The main subject is", "").replace("Subject:", "").strip()
        return tag.title()
    except:
        # Fallback to a cleaned version of the first few words if AI fails
        words = title.split()[:3]
        return " ".join(words).replace(":", "")

@app.get("/update_trends")
def update_trends():
    if not supabase: return {"error": "Supabase missing"}
    
    # --- 1. BALANCED INGESTION ---
    # Fetching roughly equal amounts from each source to ensure balanced representation
    raw_scraped_data = []

    # Google News
    google_feed = feedparser.parse("https://news.google.com/rss?hl=hi&gl=IN&ceid=IN:hi")
    for entry in google_feed.entries[:10]:
        raw_scraped_data.append({"title": entry.title, "source": "Google News", "base_weight": 100})

    # X / Twitter
    try:
        req = urllib.request.Request("https://trends24.in/india/feed/", headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as res:
            x_feed = feedparser.parse(res.read())
        for entry in x_feed.entries[:10]:
            raw_scraped_data.append({"title": entry.title, "source": "Twitter/X", "base_weight": 100})
    except: pass

    # Reddit (Equal importance given to community discussion)
    reddit_feed = feedparser.parse("https://www.reddit.com/r/india/hot/.rss")
    for entry in reddit_feed.entries[:10]:
        raw_scraped_data.append({"title": entry.title, "source": "Reddit", "base_weight": 100})

    # --- 2. THE GROUPING ENGINE ---
    # We use the generated tags to find overlapping topics
    trend_groups = {}

    for item in raw_scraped_data:
        # Generate the multi-word tag
        smart_tag = generate_smart_tag(item["title"])
        category = get_ai_category(item["title"])
        
        # Normalized key for grouping (lowercase, no spaces)
        group_key = smart_tag.lower().replace(" ", "")
        
        if group_key not in trend_groups:
            trend_groups[group_key] = {
                "tag_name": smart_tag,
                "description": item["title"][:100],
                "category": category,
                "score": 0,
                "sources": set(),
                "mentions": 0
            }
        
        trend_groups[group_key]["mentions"] += 1
        trend_groups[group_key]["sources"].add(item["source"])
        trend_groups[group_key]["score"] += item["base_weight"]

    # --- 3. THE RANKING & SELECTION ---
    final_trends = []
    for key, data in trend_groups.items():
        total_score = data["score"]
        
        # Cross-source validation bonus
        if len(data["sources"]) > 1:
            total_score *= 2.0  # Double the score if multiple platforms are talking about it
            data["description"] = f"Breaking on {', '.join(data['sources'])}: {data['description']}"

        final_trends.append({
            "tag_name": data["tag_name"],
            "description": data["description"],
            "category": data["category"],
            "heat_score": int(total_score),
            "source": "Multiple" if len(data["sources"]) > 1 else list(data["sources"])[0]
        })

    # Sort by score and take only high-relevance results
    top_output = sorted(final_trends, key=lambda x: x["heat_score"], reverse=True)[:10]

    # --- 4. STORAGE ---
    try:
        supabase.table("trending_tags").delete().neq("tag_name", "placeholder").execute()
        supabase.table("trending_tags").insert(top_output).execute()
        return {"status": "success", "count": len(top_output)}
    except Exception as e:
        return {"status": "error", "message": str(e)}
