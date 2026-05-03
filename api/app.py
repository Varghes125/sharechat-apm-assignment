from fastapi import FastAPI
from supabase import create_client
import feedparser
import os
import requests
import math
import difflib
import re
import json
import google.generativeai as genai
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

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    # Using strict system instructions to force pure JSON
    model = genai.GenerativeModel(
        model_name='gemini-2.5-flash',
        system_instruction="You are a News Data Extractor. Always respond with pure, valid JSON. Never use markdown code blocks like ```json."
    )
else:
    model = None

# -------------------------------
# AI TAGGING WITH VISIBLE DEBUGGING
# -------------------------------
def get_smart_tag_and_category(title):
    """Feeds the ENTIRE clean title to Gemini and catches explicit errors."""
    
    # We clean the source name off the end, but keep the whole sentence for context
    cleaned_title = title.split('|')[0].split('-')[0].strip()
    
    # Check 1: Is the description too small? (Less than 5 words)
    if len(cleaned_title.split()) < 5:
        return None, None

    if not model:
        return "#NO_API_KEY", "DEBUG_ERROR"

    prompt = f"""Read this entire news headline carefully and understand its core theme: "{cleaned_title}"
    
    Task 1: Create a highly relevant 2 to 3 word tag representing the core theme (e.g., "पश्चिम बंगाल चुनाव", "Stock Market", "Donald Trump"). Do NOT just pick the first 3 words.
    Task 2: Categorize it strictly into ONE of: राजनीति, तकनीक, मनोरंजन, खेल, व्यापार, अंतर्राष्ट्रीय, क्राइम, समाचार.
    
    Return ONLY a JSON object with keys "tag" and "category".
    """

    try:
        # String-based safety settings to prevent SDK crashes
        safety_settings = {
            'HARM_CATEGORY_HARASSMENT': 'BLOCK_NONE',
            'HARM_CATEGORY_HATE_SPEECH': 'BLOCK_NONE',
            'HARM_CATEGORY_SEXUALLY_EXPLICIT': 'BLOCK_NONE',
            'HARM_CATEGORY_DANGEROUS_CONTENT': 'BLOCK_NONE'
        }

        response = model.generate_content(
            prompt,
            safety_settings=safety_settings,
            generation_config={"response_mime_type": "application/json"}
        )
        
        raw_text = response.text.strip()
        
        # Check 2: Strip Markdown if Gemini stubbornly adds it
        if raw_text.startswith("```"):
            raw_text = re.sub(r'^```(?:json)?|```$', '', raw_text, flags=re.MULTILINE).strip()

        # Parse the JSON
        data = json.loads(raw_text)
        tag = data.get("tag", "PARSE_FAIL").strip()
        category = data.get("category", "PARSE_FAIL").strip()
        
        # Check 3: If AI hallucinates a massive sentence, catch it
        if len(tag.split()) > 5:
            return f"#TAG_TOO_LONG", "DEBUG_ERROR"
            
        return f"#{tag}", category
        
    except json.JSONDecodeError as e:
        # If Gemini didn't return JSON, show us what it actually returned
        snip = raw_text[:20].replace('\n', '')
        return f"#JSON_ERR: {snip}", "DEBUG_ERROR"
        
    except Exception as e:
        # Check 4: If safety filter or network fails, write the exact error to DB
        error_msg = str(e)[:30] # Truncate so it fits in Supabase
        return f"#ERR: {error_msg}", "DEBUG_ERROR"

def is_similar_topic(new_tag, existing_tag, threshold=0.70):
    """Semantic Check to reuse tags like #WestBengalElection and #BengalElections"""
    t1 = new_tag.replace("#", "").replace(" ", "").lower()
    t2 = existing_tag.replace("#", "").replace(" ", "").lower()
    
    if t1 in t2 or t2 in t1: return True
    return difflib.SequenceMatcher(None, t1, t2).ratio() > threshold

# -------------------------------
# DATA COLLECTION
# -------------------------------
def fetch_sources():
    raw_data = []
    now = datetime.now(timezone.utc)
    
    def parse_time(entry):
        try: return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        except: return now

    sources = [
        ("Google Trends", "https://trends.google.com/trending/rss?geo=IN", 120),
        ("Google News", "https://news.google.com/rss?hl=hi&gl=IN&ceid=IN:hi", 85),
        ("BBC Hindi", "https://feeds.bbci.co.uk/hindi/rss.xml", 85),
        ("Reddit India", "https://www.reddit.com/r/india/hot/.rss", 70)
    ]

    for source_name, url, weight in sources:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:
                raw_data.append({
                    "title": entry.title, 
                    "source": source_name, 
                    "base_score": weight, 
                    "time": parse_time(entry)
                })
        except: pass
    return raw_data, now

# -------------------------------
# ENDPOINTS
# -------------------------------
@app.get("/")
def root():
    return {"status": "Running Cleaned Engine"}

@app.get("/debug_ai")
def debug_ai():
    """A dedicated endpoint to test EXACTLY one headline and see the raw AI output."""
    test_headline = "पश्चिम बंगाल में नतीजे से पहले बवाल; भाजपा कार्यकर्ता के घर पर गोलीबारी, 2 गिरफ्तार"
    tag, cat = get_smart_tag_and_category(test_headline)
    return {
        "input": test_headline,
        "resulting_tag": tag,
        "resulting_category": cat
    }

@app.get("/update_trends")
def update_trends():
    if not supabase: return {"error": "Supabase missing"}
    
    raw_scraped_data, current_time = fetch_sources()
    trend_groups = {}

    for item in raw_scraped_data:
        smart_tag, category = get_smart_tag_and_category(item["title"])
        
        # If it's too short, skip it entirely
        if not smart_tag: continue 
            
        matched_key = None
        for existing_key in trend_groups.keys():
            if is_similar_topic(smart_tag, existing_key):
                matched_key = existing_key
                break
        
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

        clean_desc = item["title"].split('-')[0].strip()
        if clean_desc not in trend_groups[matched_key]["descriptions"]:
            trend_groups[matched_key]["descriptions"].append(clean_desc)

        hours_old = max(0, (current_time - item["time"]).total_seconds() / 3600)
        time_decay_multiplier = math.exp(-hours_old / 12.0) 

        trend_groups[matched_key]["mentions"] += 1
        trend_groups[matched_key]["sources_involved"].add(item["source"])
        trend_groups[matched_key]["score"] += (item["base_score"] * time_decay_multiplier)

    final_trends = []
    for key, data in trend_groups.items():
        total_score = data["score"]
        sources = list(data["sources_involved"])
        
        cross_platform_multiplier = 1.0 + (0.5 * (len(sources) - 1))
        total_score *= cross_platform_multiplier
            
        formatted_descriptions = "\n".join([f"• {desc}" for desc in data["descriptions"][:3]]) 

        final_trends.append({
            "tag_name": data["tag_name"],
            "description": formatted_descriptions,
            "category": data["category"],       
            "heat_score": int(total_score),
            "source": ", ".join(sources)
        })

    top_10_output = sorted(final_trends, key=lambda x: x["heat_score"], reverse=True)[:10]

    try:
        supabase.table("trending_tags").delete().neq("tag_name", "placeholder").execute()
        if top_10_output:
            supabase.table("trending_tags").insert(top_10_output).execute()
        return {"status": "success", "trends_found": len(top_10_output)}
    except Exception as e:
        return {"status": "error", "message": str(e)}
