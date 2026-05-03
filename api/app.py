from fastapi import FastAPI
from supabase import create_client
import feedparser
import os
import re
import urllib.request
import requests
from collections import Counter
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
# CONFIGURATION
# -------------------------------
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase = create_client(url, key) if url and key else None

HF_TOKEN = os.environ.get("HUGGINGFACE_TOKEN")
# We use MNLI for both categorization and tag generation as it handles zero-shot semantic matching perfectly.
HF_API_URL = "https://api-inference.huggingface.co/models/facebook/bart-large-mnli"

# -------------------------------
# INTELLIGENT NLP HELPERS
# -------------------------------
def get_ai_category(title):
    """Zero-shot classification into ShareChat-friendly categories in Hindi."""
    if not HF_TOKEN: return "विविध" (Miscellaneous)
    
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    # Using English labels for the model to understand, but we map them to Hindi
    candidate_labels = ["Politics", "Technology", "Entertainment", "Sports", "Finance", "International", "Crime"]
    
    payload = {"inputs": title, "parameters": {"candidate_labels": candidate_labels}}
    
    # Mapping English categories to Hindi for the final output
    category_map = {
        "Politics": "राजनीति",
        "Technology": "तकनीक",
        "Entertainment": "मनोरंजन",
        "Sports": "खेल",
        "Finance": "व्यापार",
        "International": "अंतर्राष्ट्रीय",
        "Crime": "क्राइम",
        "News": "समाचार"
    }

    try:
        response = requests.post(HF_API_URL, headers=headers, json=payload, timeout=7)
        top_label = response.json()['labels'][0]
        return category_map.get(top_label, "समाचार")
    except: return "समाचार"

def generate_smart_tag(title):
    """Extracts the core entity/theme from the text using semantic candidate ranking."""
    # Pre-cleaning: Remove source names (e.g., "ABP News:") and punctuation
    clean_title = title.split('|')[0].split('-')[0].split(':')[0].strip()
    clean_title = re.sub(r'[^a-zA-Z0-9\u0900-\u097F\s]', '', clean_title) # Keep English and Hindi chars
    
    words = clean_title.split()
    
    # Rule 3 validation: If the description has fewer than 6 words, it's invalid.
    if len(words) < 6:
        return None

    if not HF_TOKEN:
        # Basic fallback: First 3 words
        return " ".join(words[:3])

    # Generate multi-word candidates for the AI to evaluate
    candidates = []
    candidates.append(" ".join(words[:2]))
    candidates.append(" ".join(words[:3]))
    if len(words) >= 4:
        candidates.append(" ".join(words[:4]))

    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    payload = {"inputs": clean_title, "parameters": {"candidate_labels": candidates}}
    
    try:
        response = requests.post(HF_API_URL, headers=headers, json=payload, timeout=7)
        # The AI returns the candidate phrase that best summarizes the sentence
        best_tag = response.json()['labels'][0]
        return f"#{best_tag}"
    except:
        return f"#{' '.join(words[:3])}"

# -------------------------------
# DATA COLLECTION MODULE
# -------------------------------
def fetch_sources():
    """Collects raw data and assigns base authority weights."""
    raw_data = []

    # 1. Google News Hindi (High Authority)
    google_feed = feedparser.parse("https://news.google.com/rss?hl=hi&gl=IN&ceid=IN:hi")
    for entry in google_feed.entries[:15]:
        raw_data.append({"title": entry.title, "source": "Google News", "base_score": 100})

    # 2. Twitter/X India Trends (High Velocity)
    try:
        req = urllib.request.Request("https://trends24.in/india/feed/", headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as res:
            x_feed = feedparser.parse(res.read())
        for entry in x_feed.entries[:10]:
            raw_data.append({"title": entry.title, "source": "X/Twitter", "base_score": 90})
    except: pass

    # 3. Reddit India (High Engagement)
    reddit_feed = feedparser.parse("https://www.reddit.com/r/india/hot/.rss")
    for entry in reddit_feed.entries[:10]:
        raw_data.append({"title": entry.title, "source": "Reddit", "base_score": 80})

    return raw_data

# -------------------------------
# MAIN API ENDPOINTS
# -------------------------------
@app.get("/")
def root():
    return {"message": "ShareChat Trend Engine Active", "supabase_connected": supabase is not None}

@app.get("/update_trends")
def update_trends():
    if not supabase: return {"error": "Supabase missing"}
    
    # 1. Collect Data
    raw_scraped_data = fetch_sources()
    
    # 2. Aggregation Dictionary
    trend_groups = {}

    for item in raw_scraped_data:
        # Extract the semantic tag. Returns None if description < 6 words.
        smart_tag = generate_smart_tag(item["title"])
        
        # Rule 3 Enforcement: Skip items that are too short/vague
        if not smart_tag:
            continue
            
        category = get_ai_category(item["title"])
        
        # Normalize the key for grouping (e.g. "#WestBengal" == "#westbengal")
        group_key = smart_tag.lower().replace(" ", "")

        if group_key not in trend_groups:
            trend_groups[group_key] = {
                "tag_name": smart_tag,
                "description": item["title"],
                "category": category,
                "score": 0,
                "sources_involved": set(),
                "mentions": 0
            }

        # Add points to this specific trend group
        trend_groups[group_key]["mentions"] += 1
        trend_groups[group_key]["sources_involved"].add(item["source"])
        trend_groups[group_key]["score"] += item["base_score"]

    # 3. Ranking Engine
    final_trends = []
    for key, data in trend_groups.items():
        total_score = data["score"]
        sources = list(data["sources_involved"])
        
        # The Viral Multiplier: If a trend appears on more than 1 platform, it is highly validated.
        if len(sources) > 1:
            total_score *= 2.0
            
        # Format Source display for the UI
        source_display = ", ".join(sources)

        final_trends.append({
            "tag_name": data["tag_name"],
            "description": data["description"], # Keeping original description
            "category": data["category"],       # Now in Hindi
            "heat_score": int(total_score),
            "source": source_display            # Rule 1 satisfied
        })

    # Sort by Heat Score and strict cutoff at Top 10
    top_10_output = sorted(final_trends, key=lambda x: x["heat_score"], reverse=True)[:10]

    # 4. Storage Update
    try:
        supabase.table("trending_tags").delete().neq("tag_name", "placeholder").execute()
        if top_10_output:
            supabase.table("trending_tags").insert(top_10_output).execute()
        return {"status": "success", "trends_found": len(top_10_output)}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/get_trends")
def get_trends():
    if not supabase: return []
    res = supabase.table("trending_tags").select("*").order("heat_score", desc=True).execute()
    return res.data
