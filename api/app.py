from fastapi import FastAPI
from supabase import create_client
import feedparser
import os
import requests
import math
import difflib
import re
import json
import time
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
HF_API_URL = "https://api-inference.huggingface.co/models/HuggingFaceH4/zephyr-7b-beta"

# -------------------------------
# ROBUST NLP FALLBACK
# -------------------------------
HINDI_STOPWORDS = {"में", "के", "और", "पर", "से", "लिए", "है", "की", "को", "ने", "इन", "का", "एक", "यह", "तथा", "वाला", "वाली", "क्या", "क्यों", "कैसे", "कहा", "गया"}
ENGLISH_STOPWORDS = {"the", "is", "at", "which", "on", "and", "a", "an", "in", "to", "for", "of", "with", "by", "from", "that", "after", "hit", "as", "sees", "news", "live", "updates"}

def generate_fallback_tag(title):
    cleaned = title.split('|')[0].split('-')[0].split(':')[0].strip()
    cleaned = re.sub(r'[^\w\s\u0900-\u097F]', '', cleaned)
    words = cleaned.split()
    
    meaningful_words = [w for w in words if w.lower() not in ENGLISH_STOPWORDS and w not in HINDI_STOPWORDS and len(w) > 2]
    
    if len(meaningful_words) < 2:
        return None
    return f"#{' '.join(meaningful_words[:3])}"

# -------------------------------
# HUGGING FACE AI LOGIC
# -------------------------------
def get_smart_tag_and_category(title):
    """Tier 1 Logic: Tries Hugging Face with JSON constraints."""
    
    cleaned_title = title.split('|')[0].split('-')[0].strip()
    
    # Check 1: Is the description too small?
    if len(cleaned_title.split()) < 5:
        return None, None

    if not HF_TOKEN:
        return generate_fallback_tag(cleaned_title), "NO_HF_TOKEN"

    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json"
    }
    
    # We explicitly ask for JSON format without markdown wrappers
    prompt = f"""<|system|>
You are an expert Indian News Editor. Respond ONLY with valid JSON.
<|user|>
Read this headline: "{cleaned_title}"
Extract a 2 to 3 word tag representing the core entity (e.g., "पश्चिम बंगाल चुनाव", "Stock Market").
Categorize it strictly into ONE of: राजनीति, तकनीक, मनोरंजन, खेल, व्यापार, अंतर्राष्ट्रीय, क्राइम, समाचार.
Return ONLY a raw JSON object with keys "tag" and "category". Do not use markdown blocks.
<|assistant|>"""



    payload = {
        "inputs": prompt, 
        "parameters": {
            "max_new_tokens": 50, 
            "temperature": 0.1,
            "return_full_text": False
        }
    }
    
    try:
        # Give it a small pause to respect rate limits
        time.sleep(0.5) 
        res = requests.post(HF_API_URL, headers=headers, json=payload, timeout=10)
        
        if res.status_code != 200:
             return generate_fallback_tag(cleaned_title), f"HF_ERR_{res.status_code}"
             
        # Extract the generated text
        raw_text = res.json()[0].get('generated_text', '').strip()
        
        # Strip potential markdown formatting
        if raw_text.startswith("```"):
             raw_text = re.sub(r'^```(?:json)?|```$', '', raw_text, flags=re.MULTILINE).strip()

        try:
             data = json.loads(raw_text)
             tag = data.get("tag", "").strip().replace('#', '')
             category = data.get("category", "समाचार").strip()
             
             if len(tag.split()) > 5 or len(tag) < 2:
                  return generate_fallback_tag(cleaned_title), "HF_TAG_TOO_LONG"
             
             return f"#{tag}", category
             
        except json.JSONDecodeError:
             snip = raw_text[:20].replace('\n', '')
             return generate_fallback_tag(cleaned_title), f"HF_JSON_ERR_{snip}"

    except requests.exceptions.Timeout:
         return generate_fallback_tag(cleaned_title), "HF_TIMEOUT"
    except Exception as e:
        error_msg = str(e)[:20]
        return generate_fallback_tag(cleaned_title), f"HF_CRASH_{error_msg}"

def is_similar_topic(new_tag, existing_tag, threshold=0.70):
    t1 = new_tag.replace("#", "").replace(" ", "").lower()
    t2 = existing_tag.replace("#", "").replace(" ", "").lower()
    if t1 in t2 or t2 in t1: return True
    return difflib.SequenceMatcher(None, t1, t2).ratio() > threshold

# -------------------------------
# DATA COLLECTION (REDUCED LOAD)
# -------------------------------
def fetch_sources():
    raw_data = []
    now = datetime.now(timezone.utc)
    
    def parse_time(entry):
        try: return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        except: return now

    sources = [
        ("Google Trends", "[https://trends.google.com/trending/rss?geo=IN](https://trends.google.com/trending/rss?geo=IN)", 120),
        ("Google News", "[https://news.google.com/rss?hl=hi&gl=IN&ceid=IN:hi](https://news.google.com/rss?hl=hi&gl=IN&ceid=IN:hi)", 85),
        ("BBC Hindi", "[https://feeds.bbci.co.uk/hindi/rss.xml](https://feeds.bbci.co.uk/hindi/rss.xml)", 85)
    ]

    # ONLY taking the top 4 from each feed to reduce the HF API load (Total 12 items)
    for source_name, url, weight in sources:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:4]:
                raw_data.append({
                    "title": entry.title, 
                    "source": source_name, 
                    "base_score": weight, 
                    "time": parse_time(entry)
                })
        except: pass
    return raw_data, now

# -------------------------------
# CORE ENGINE
# -------------------------------
@app.get("/")
def root():
    return {"status": "Running HF Engine"}

@app.get("/debug_ai")
def debug_ai():
    """Test endpoint for the HF API."""
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

@app.get("/get_trends")
def get_trends():
    if not supabase: return []
    res = supabase.table("trending_tags").select("*").order("heat_score", desc=True).execute()
    return res.data
