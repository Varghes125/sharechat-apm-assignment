from fastapi import FastAPI
from supabase import create_client
import feedparser
import os
import re
import urllib.request
import requests
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
# Using Mistral Instruct for Text Generation (Better at following rules and translating to Hindi)
HF_API_URL = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.2"

# -------------------------------
# INTELLIGENT NLP ENGINE (MISTRAL)
# -------------------------------
def get_smart_tag_and_category(title):
    """
    Instructs the LLM to act as a Hindi editor. 
    Returns a tuple: (Tag with spaces, Category) in Hindi.
    """
    # Clean the title of unwanted source prefixes
    clean_title = title.split('|')[0].split('-')[0].split(':')[0].strip()
    words = clean_title.split()
    
    # Rule: Reject vague/short descriptions
    if len(words) < 6:
        return None, None

    if not HF_TOKEN:
        return f"#{' '.join(words[:3])}", "समाचार"

    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    
    # The Prompt Engineering: Forcing the model to output a strict Hindi format
    prompt = f"""[INST] You are a professional ShareChat Hindi News Editor. Read this news headline: "{clean_title}"
    Task 1: Extract the main topic in exactly 2 to 3 words in Hindi (e.g., पश्चिम बंगाल चुनाव, विराट कोहली).
    Task 2: Categorize it strictly into ONE of these: राजनीति, तकनीक, मनोरंजन, खेल, व्यापार, अंतर्राष्ट्रीय, क्राइम, समाचार.
    Format your output EXACTLY like this: TAG | CATEGORY
    Example output: पश्चिम बंगाल चुनाव | राजनीति [/INST]"""

    payload = {
        "inputs": prompt,
        "parameters": {
            "max_new_tokens": 15, 
            "temperature": 0.1, # Low temp for factual consistency
            "return_full_text": False
        }
    }
    
    try:
        response = requests.post(HF_API_URL, headers=headers, json=payload, timeout=8)
        result_text = response.json()[0]['generated_text'].strip()
        
        # Parse the output: "पश्चिम बंगाल चुनाव | राजनीति"
        if "|" in result_text:
            tag, category = result_text.split("|", 1)
            tag = tag.strip().replace("#", "") # Clean up
            category = category.strip()
            
            # Ensure proper spacing and hashtag
            return f"#{tag}", category
        else:
            return f"#{' '.join(words[:3])}", "समाचार"
    except Exception as e:
        print(f"LLM Error: {e}")
        return f"#{' '.join(words[:3])}", "समाचार"

# -------------------------------
# DATA COLLECTION (HINDI FOCUS)
# -------------------------------
def fetch_sources():
    """Collects real-time raw data from highly relevant Indian/Hindi sources."""
    raw_data = []
    now = datetime.now(timezone.utc)

    def parse_time(entry):
        try:
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        except:
            return now

    # 1. Google News Hindi (Broad Coverage)
    google_feed = feedparser.parse("https://news.google.com/rss?hl=hi&gl=IN&ceid=IN:hi")
    for entry in google_feed.entries[:10]:
        raw_data.append({"title": entry.title, "source": "Google News", "base_score": 100, "time": parse_time(entry)})

    # 2. BBC Hindi (High Quality Editorial)
    bbc_feed = feedparser.parse("https://feeds.bbci.co.uk/hindi/rss.xml")
    for entry in bbc_feed.entries[:10]:
        raw_data.append({"title": entry.title, "source": "BBC Hindi", "base_score": 90, "time": parse_time(entry)})

    # 3. News18 India Hindi (Grassroots/Political)
    news18_feed = feedparser.parse("https://hindi.news18.com/rss/khabar/nation/nation.xml")
    for entry in news18_feed.entries[:10]:
        raw_data.append({"title": entry.title, "source": "News18", "base_score": 90, "time": parse_time(entry)})

    # 4. Reddit India (High Velocity / Viral factor)
    reddit_feed = feedparser.parse("https://www.reddit.com/r/india/hot/.rss")
    for entry in reddit_feed.entries[:5]:
        raw_data.append({"title": entry.title, "source": "Reddit", "base_score": 80, "time": parse_time(entry)})

    return raw_data, now

# -------------------------------
# MAIN API ENDPOINTS
# -------------------------------
@app.get("/")
def root():
    return {"message": "ShareChat Trend Engine Active (Hindi)", "supabase_connected": supabase is not None}

@app.get("/update_trends")
def update_trends():
    if not supabase: return {"error": "Supabase missing"}
    
    raw_scraped_data, current_time = fetch_sources()
    trend_groups = {}

    for item in raw_scraped_data:
        # Get perfectly spaced Hindi tag and category from Mistral
        smart_tag, category = get_smart_tag_and_category(item["title"])
        
        # Discard if description is too short (Rule 3)
        if not smart_tag:
            continue
            
        # Create a matching key without spaces (e.g., "#पश्चिमबंगालचुनाव") to group similar stories together
        group_key = smart_tag.replace(" ", "")

        if group_key not in trend_groups:
            trend_groups[group_key] = {
                "tag_name": smart_tag,           # Keeps the original spaces! (#पश्चिम बंगाल चुनाव)
                "description": item["title"],
                "category": category,
                "score": 0,
                "sources_involved": set(),
                "mentions": 0,
                "newest_timestamp": item["time"]
            }

        # Velocity Scoring: Decay score based on how old the news is (hours)
        hours_old = max(0, (current_time - item["time"]).total_seconds() / 3600)
        time_decay_multiplier = max(0.2, 1.0 - (hours_old * 0.05)) # Decays over 20 hours

        trend_groups[group_key]["mentions"] += 1
        trend_groups[group_key]["sources_involved"].add(item["source"])
        
        # Add score: Base Score * Velocity Multiplier
        trend_groups[group_key]["score"] += (item["base_score"] * time_decay_multiplier)

    # --- RANKING ENGINE ---
    final_trends = []
    for key, data in trend_groups.items():
        total_score = data["score"]
        sources = list(data["sources_involved"])
        
        # The Viral Multiplier: If found on multiple feeds, it's a confirmed trend!
        if len(sources) > 1:
            total_score *= 2.0
            
        final_trends.append({
            "tag_name": data["tag_name"],
            "description": data["description"], 
            "category": data["category"],       
            "heat_score": int(total_score),
            "source": ", ".join(sources)        # e.g., "Google News, BBC Hindi"
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
