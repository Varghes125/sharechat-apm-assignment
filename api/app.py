from fastapi import FastAPI
from supabase import create_client
import feedparser
import os
import math
import difflib
import re
import json
from groq import Groq
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

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# -------------------------------
# ROBUST NLP FALLBACK (Rule 2)
# -------------------------------
HINDI_STOPWORDS = {"में", "के", "और", "पर", "से", "लिए", "है", "की", "को", "ने", "इन", "का", "एक", "यह", "तथा", "वाला", "वाली", "क्या", "क्यों", "कैसे", "कहा", "गया"}
ENGLISH_STOPWORDS = {"the", "is", "at", "which", "on", "and", "a", "an", "in", "to", "for", "of", "with", "by", "from", "that", "after", "hit", "as", "sees", "news", "live", "updates"}

def generate_fallback_tag(title):
    cleaned = title.split('|')[0].split('-')[0].split(':')[0].strip()
    cleaned = re.sub(r'[^\w\s\u0900-\u097F]', '', cleaned)
    words = cleaned.split()
    
    meaningful_words = [w for w in words if w.lower() not in ENGLISH_STOPWORDS and w not in HINDI_STOPWORDS and len(w) > 2]
    
    if len(meaningful_words) < 2: return None
    return f"#{' '.join(meaningful_words[:3])}"

# -------------------------------
# GROQ AI BATCH PROCESSING
# -------------------------------
def get_batch_tags_and_categories(headlines):
    """Sends ALL headlines to Groq in a single 1.5s API call to bypass Rate Limits."""
    if not groq_client or not headlines:
        return [{"tag": generate_fallback_tag(h), "category": "NO_API_KEY"} for h in headlines]

    numbered_list = "\n".join([f"{i}. {h}" for i, h in enumerate(headlines)])

    prompt = f"""Read these {len(headlines)} news headlines carefully.
For EACH headline:
1. Extract a highly relevant 2 to 3 word tag representing the core theme (e.g., "पश्चिम बंगाल चुनाव", "Stock Market").
2. Categorize it strictly into ONE of: राजनीति, तकनीक, मनोरंजन, खेल, व्यापार, अंतर्राष्ट्रीय, क्राइम, समाचार.

You must return a valid JSON object containing EXACTLY ONE key called "data", which is an array of exactly {len(headlines)} objects in the exact order provided.
Example format:
{{
  "data": [
    {{"tag": "TagHere", "category": "CategoryHere"}},
    {{"tag": "TagHere", "category": "CategoryHere"}}
  ]
}}

Headlines:
{numbered_list}
"""

    try:
        # Groq Llama 3.1 execution with forced JSON mode
        chat_completion = groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": "You are an expert Indian News Editor. You must output strictly in JSON format."},
                # We added the word JSON to the user prompt as well to satisfy Groq's strict 400-error checks
                {"role": "user", "content": prompt + "\nRemember, return ONLY a valid JSON object."}
            ],
            # CHANGED: Upgraded to the current production model
            model="llama-3.1-8b-instant",
            temperature=0.1,
            response_format={"type": "json_object"}
        )
        
        raw_text = chat_completion.choices[0].message.content
        data_dict = json.loads(raw_text)
        data_array = data_dict.get("data", [])
        
        # Map the AI results back to our headlines safely
        results = []
        for i, h in enumerate(headlines):
            if i < len(data_array):
                tag = data_array[i].get("tag", "").strip().replace('#', '')
                cat = data_array[i].get("category", "समाचार").strip()
                
                if len(tag.split()) > 5 or len(tag) < 2:
                    results.append({"tag": generate_fallback_tag(h), "category": "TAG_TOO_LONG"})
                else:
                    results.append({"tag": f"#{tag}", "category": cat})
            else:
                results.append({"tag": generate_fallback_tag(h), "category": "ARRAY_MISMATCH"})
                
        return results
        
    except Exception as e:
        # CHANGED: Increased the error slice to 100 characters so we can see exactly what Groq is complaining about if it fails again
        error_msg = str(e)[:100] 
        return [{"tag": generate_fallback_tag(h), "category": f"GROQ_ERR_{error_msg}"} for h in headlines]try:
        # Groq Llama 3.1 execution with forced JSON mode
        chat_completion = groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": "You are an expert Indian News Editor. You must output strictly in JSON format."},
                # We added the word JSON to the user prompt as well to satisfy Groq's strict 400-error checks
                {"role": "user", "content": prompt + "\nRemember, return ONLY a valid JSON object."}
            ],
            # CHANGED: Upgraded to the current production model
            model="llama-3.1-8b-instant",
            temperature=0.1,
            response_format={"type": "json_object"}
        )
        
        raw_text = chat_completion.choices[0].message.content
        data_dict = json.loads(raw_text)
        data_array = data_dict.get("data", [])
        
        # Map the AI results back to our headlines safely
        results = []
        for i, h in enumerate(headlines):
            if i < len(data_array):
                tag = data_array[i].get("tag", "").strip().replace('#', '')
                cat = data_array[i].get("category", "समाचार").strip()
                
                if len(tag.split()) > 5 or len(tag) < 2:
                    results.append({"tag": generate_fallback_tag(h), "category": "TAG_TOO_LONG"})
                else:
                    results.append({"tag": f"#{tag}", "category": cat})
            else:
                results.append({"tag": generate_fallback_tag(h), "category": "ARRAY_MISMATCH"})
                
        return results
        
    except Exception as e:
        # CHANGED: Increased the error slice to 100 characters so we can see exactly what Groq is complaining about if it fails again
        error_msg = str(e)[:100] 
        return [{"tag": generate_fallback_tag(h), "category": f"GROQ_ERR_{error_msg}"} for h in headlines]

def is_similar_topic(new_tag, existing_tag, threshold=0.70):
    """Rule 3: Semantic check to reuse existing tags."""
    t1 = new_tag.replace("#", "").replace(" ", "").lower()
    t2 = existing_tag.replace("#", "").replace(" ", "").lower()
    if t1 in t2 or t2 in t1: return True
    return difflib.SequenceMatcher(None, t1, t2).ratio() > threshold

# -------------------------------
# DATA COLLECTION (Restored to 40 items)
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
    return {"status": "Running Groq Batch Engine"}

@app.get("/debug_ai")
def debug_ai():
    """A dedicated endpoint to test a single headline with Groq."""
    test_headline = "पश्चिम बंगाल में नतीजे से पहले बवाल; भाजपा कार्यकर्ता के घर पर गोलीबारी, 2 गिरफ्तार"
    # We pass it as a list of 1 because our function expects a batch
    result = get_batch_tags_and_categories([test_headline])[0]
    return {
        "input": test_headline,
        "resulting_tag": result["tag"],
        "resulting_category": result["category"]
    }

@app.get("/update_trends")
def update_trends():
    if not supabase: return {"error": "Supabase missing"}
    
    raw_scraped_data, current_time = fetch_sources()
    
    # 1. Clean titles & Reject tiny descriptions (Rule 1)
    clean_titles = []
    for item in raw_scraped_data:
        cleaned = item["title"].split('|')[0].split('-')[0].strip()
        if len(cleaned.split()) < 5:
            clean_titles.append("SKIP")
        else:
            clean_titles.append(cleaned)
            
    # 2. Fire the Batch Groq Request (1 API Call)
    ai_results = get_batch_tags_and_categories(clean_titles)
    
    # 3. Process, Score, and Cluster
    trend_groups = {}
    for i, item in enumerate(raw_scraped_data):
        if clean_titles[i] == "SKIP": 
            continue
            
        smart_tag = ai_results[i]["tag"]
        category = ai_results[i]["category"]
        
        if not smart_tag or "ERR" in category: continue 
            
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

        # Context Aggregation
        clean_desc = clean_titles[i]
        if clean_desc not in trend_groups[matched_key]["descriptions"]:
            trend_groups[matched_key]["descriptions"].append(clean_desc)

        # Rule 7: Exponential Time Decay
        hours_old = max(0, (current_time - item["time"]).total_seconds() / 3600)
        time_decay_multiplier = math.exp(-hours_old / 12.0) 

        trend_groups[matched_key]["mentions"] += 1
        trend_groups[matched_key]["sources_involved"].add(item["source"])
        trend_groups[matched_key]["score"] += (item["base_score"] * time_decay_multiplier)

    # --- RANKING & OUTPUT ---
    final_trends = []
    for key, data in trend_groups.items():
        total_score = data["score"]
        sources = list(data["sources_involved"])
        
        # Rule 7: Consensus Multiplier
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
