from fastapi import FastAPI
from supabase import create_client
import feedparser
import os
import requests
import math
import difflib
from datetime import datetime, timezone
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
HF_API_URL = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.2"

# -------------------------------
# AI & NLP HELPERS
# -------------------------------
def get_smart_tag_and_category(title):
    """Uses LLM to extract a focused 2-3 word Hindi tag and Category."""
    clean_title = title.split('|')[0].split('-')[0].split(':')[0].strip()
    words = clean_title.split()
    
    # Reject vague or extremely short descriptions
    if len(words) < 5: 
        return None, None 

    if not HF_TOKEN: 
        return f"#{' '.join(words[:3])}", "समाचार"

    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    prompt = f"""[INST] You are a ShareChat Hindi News Editor. Read this headline: "{clean_title}"
    Task 1: Extract the core subject in exactly 2 to 3 words in Hindi (e.g., लोकसभा चुनाव, शेयर बाजार).
    Task 2: Categorize it strictly into ONE of: राजनीति, तकनीक, मनोरंजन, खेल, व्यापार, अंतर्राष्ट्रीय, क्राइम, समाचार.
    Format EXACTLY as: TAG | CATEGORY [/INST]"""

    payload = {"inputs": prompt, "parameters": {"max_new_tokens": 15, "temperature": 0.1}}
    
    try:
        res = requests.post(HF_API_URL, headers=headers, json=payload, timeout=8)
        result_text = res.json()[0]['generated_text'].strip()
        
        if "|" in result_text:
            tag, category = result_text.split("|", 1)
            # Ensure the tag is clean and has a single hashtag
            return f"#{tag.strip().replace('#', '')}", category.strip()
    except Exception:
        pass
        
    return f"#{' '.join(words[:3])}", "समाचार"

def is_similar_tag(new_tag, existing_tag, threshold=0.65):
    """Compares two tags to see if they mean the same thing (e.g., #बंगाल चुनाव and #पश्चिम बंगाल चुनाव)"""
    t1 = new_tag.replace("#", "").replace(" ", "").lower()
    t2 = existing_tag.replace("#", "").replace(" ", "").lower()
    return difflib.SequenceMatcher(None, t1, t2).ratio() > threshold

# -------------------------------
# ADVANCED DATA COLLECTION
# -------------------------------
def fetch_sources():
    """Fetches feeds and assigns precise Base Weights based on impact."""
    raw_data = []
    now = datetime.now(timezone.utc)

    def parse_time(entry):
        try: 
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        except: 
            return now

    # 1. Google Trends India (Pure Search Volume - Highest Weight: 120)
    trends_feed = feedparser.parse("https://trends.google.com/trending/rss?geo=IN")
    for entry in trends_feed.entries[:10]:
        raw_data.append({"title": entry.title, "source": "Google Trends", "base_score": 120, "time": parse_time(entry)})

    # 2. Mainstream Hindi News (High Credibility - Weight: 85)
    google_feed = feedparser.parse("https://news.google.com/rss?hl=hi&gl=IN&ceid=IN:hi")
    for entry in google_feed.entries[:10]:
        raw_data.append({"title": entry.title, "source": "Google News", "base_score": 85, "time": parse_time(entry)})

    bbc_feed = feedparser.parse("https://feeds.bbci.co.uk/hindi/rss.xml")
    for entry in bbc_feed.entries[:10]:
        raw_data.append({"title": entry.title, "source": "BBC Hindi", "base_score": 85, "time": parse_time(entry)})

    news18_feed = feedparser.parse("https://hindi.news18.com/rss/khabar/nation/nation.xml")
    for entry in news18_feed.entries[:10]:
        raw_data.append({"title": entry.title, "source": "News18", "base_score": 85, "time": parse_time(entry)})

    # 3. Reddit India (Community Engagement - Weight: 70)
    reddit_feed = feedparser.parse("https://www.reddit.com/r/india/hot/.rss")
    for entry in reddit_feed.entries[:10]:
        raw_data.append({"title": entry.title, "source": "Reddit", "base_score": 70, "time": parse_time(entry)})

    return raw_data, now

# -------------------------------
# CORE ENGINE
# -------------------------------
@app.get("/")
def root():
    return {"message": "ShareChat Trend Engine Active", "status": "Running"}

@app.get("/update_trends")
def update_trends():
    if not supabase: return {"error": "Supabase missing"}
    
    raw_scraped_data, current_time = fetch_sources()
    trend_groups = {}

    for item in raw_scraped_data:
        smart_tag, category = get_smart_tag_and_category(item["title"])
        if not smart_tag: 
            continue 
            
        # --- DEDUPLICATION & GROUPING LOGIC ---
        matched_key = None
        for existing_key in trend_groups.keys():
            if is_similar_tag(smart_tag, existing_key):
                matched_key = existing_key
                break
        
        # If no similar tag exists, create a new group
        if not matched_key:
            matched_key = smart_tag
            trend_groups[matched_key] = {
                "tag_name": smart_tag, 
                "descriptions": [],    
                "category": category,
                "score": 0,
                "sources_involved": set(),
                "mentions": 0
            }

        # Append description only if it's not a duplicate headline
        clean_desc = item["title"].split('-')[0].strip()
        if clean_desc not in trend_groups[matched_key]["descriptions"]:
            trend_groups[matched_key]["descriptions"].append(clean_desc)

        # --- SCIENTIFIC SCORING (Exponential Decay) ---
        hours_old = max(0, (current_time - item["time"]).total_seconds() / 3600)
        # Half-life of 12 hours: Score drops by 50% every 12 hours
        time_decay_multiplier = math.exp(-hours_old / 12.0) 

        trend_groups[matched_key]["mentions"] += 1
        trend_groups[matched_key]["sources_involved"].add(item["source"])
        trend_groups[matched_key]["score"] += (item["base_score"] * time_decay_multiplier)

    # --- RANKING & FINAL OUTPUT ---
    final_trends = []
    for key, data in trend_groups.items():
        total_score = data["score"]
        sources = list(data["sources_involved"])
        
        # Dynamic Viral Multiplier: 1 source = 1x, 2 sources = 1.5x, 3 sources = 2.0x
        cross_platform_multiplier = 1.0 + (0.5 * (len(sources) - 1))
        total_score *= cross_platform_multiplier
            
        # Format Descriptions into a clean bulleted list for UI (Max 3 contexts)
        formatted_descriptions = "\n".join([f"• {desc}" for desc in data["descriptions"][:3]]) 

        final_trends.append({
            "tag_name": data["tag_name"],
            "description": formatted_descriptions,
            "category": data["category"],       
            "heat_score": int(total_score),
            "source": ", ".join(sources)
        })

    # Strict cutoff: Top 10 hottest trends only
    top_10_output = sorted(final_trends, key=lambda x: x["heat_score"], reverse=True)[:10]

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
