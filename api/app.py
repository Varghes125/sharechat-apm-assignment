from fastapi import FastAPI
from supabase import create_client
import feedparser
import os
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
    model = genai.GenerativeModel(
        model_name='gemini-2.5-flash',
        system_instruction="You are a News Data Extractor. Always respond with pure, valid JSON. Never use markdown code blocks."
    )
else:
    model = None

# -------------------------------
# ROBUST NLP FALLBACK
# -------------------------------
HINDI_STOPWORDS = {"में", "के", "और", "पर", "से", "लिए", "है", "की", "को", "ने", "इन", "का", "एक", "यह", "तथा", "वाला", "वाली", "क्या", "क्यों", "कैसे", "कहा", "गया"}
ENGLISH_STOPWORDS = {"the", "is", "at", "which", "on", "and", "a", "an", "in", "to", "for", "of", "with", "by", "from", "that", "after", "hit", "as", "sees", "news", "live", "updates"}

def generate_fallback_tag(title):
    words = title.split()
    meaningful_words = [w for w in words if w.lower() not in ENGLISH_STOPWORDS and w not in HINDI_STOPWORDS and len(w) > 2]
    if len(meaningful_words) < 2: return None
    return f"#{' '.join(meaningful_words[:3])}"

# -------------------------------
# AI BATCH PROCESSING (THE FIX)
# -------------------------------
def get_batch_tags_and_categories(headlines):
    """Sends ALL headlines to Gemini in a single API call to prevent Rate Limits."""
    if not model or not headlines:
        return [{"tag": generate_fallback_tag(h), "category": "समाचार"} for h in headlines]

    # Create a numbered list of headlines
    numbered_list = "\n".join([f"{i}. {h}" for i, h in enumerate(headlines)])

    prompt = f"""Read these {len(headlines)} news headlines carefully.
For EACH headline:
1. Create a highly relevant 2 to 3 word tag representing the core theme (e.g., "पश्चिम बंगाल चुनाव", "Stock Market"). Do NOT just pick the first 3 words.
2. Categorize it strictly into ONE of: राजनीति, तकनीक, मनोरंजन, खेल, व्यापार, अंतर्राष्ट्रीय, क्राइम, समाचार.

Return ONLY a JSON array of objects. The array MUST contain exactly {len(headlines)} items in the exact order provided.
Example format:
[
  {{"tag": "TagHere", "category": "CategoryHere"}},
  {{"tag": "TagHere", "category": "CategoryHere"}}
]

Headlines:
{numbered_list}
"""

    try:
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
        if raw_text.startswith("```"):
            raw_text = re.sub(r'^```(?:json)?|```$', '', raw_text, flags=re.MULTILINE).strip()

        data_array = json.loads(raw_text)
        
        # Map the AI results back to our headlines safely
        results = []
        for i, h in enumerate(headlines):
            if i < len(data_array):
                tag = data_array[i].get("tag", "").strip().replace('#', '')
                cat = data_array[i].get("category", "समाचार").strip()
                
                if len(tag.split()) > 5 or len(tag) < 2:
                    results.append({"tag": generate_fallback_tag(h), "category": "समाचार"})
                else:
                    results.append({"tag": f"#{tag}", "category": cat})
            else:
                results.append({"tag": generate_fallback_tag(h), "category": "समाचार"})
                
        return results
        
    except Exception as e:
        print(f"Batch AI Error: {e}")
        return [{"tag": generate_fallback_tag(h), "category": "DEBUG_ERROR"} for h in headlines]

def is_similar_topic(new_tag, existing_tag, threshold=0.65):
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
        ("Google Trends", "[https://trends.google.com/trending/rss?geo=IN](https://trends.google.com/trending/rss?geo=IN)", 120),
        ("Google News", "[https://news.google.com/rss?hl=hi&gl=IN&ceid=IN:hi](https://news.google.com/rss?hl=hi&gl=IN&ceid=IN:hi)", 85),
        ("BBC Hindi", "[https://feeds.bbci.co.uk/hindi/rss.xml](https://feeds.bbci.co.uk/hindi/rss.xml)", 85),
        ("Reddit India", "[https://www.reddit.com/r/india/hot/.rss](https://www.reddit.com/r/india/hot/.rss)", 70)
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
    return {"status": "Running Batch Engine"}

@app.get("/update_trends")
def update_trends():
    if not supabase: return {"error": "Supabase missing"}
    
    raw_scraped_data, current_time = fetch_sources()
    
    # 1. Clean all titles and prepare them for batch processing
    clean_titles = []
    for item in raw_scraped_data:
        cleaned = item["title"].split('|')[0].split('-')[0].strip()
        # Reject tiny descriptions before sending to AI
        if len(cleaned.split()) < 5:
            clean_titles.append("SKIP")
        else:
            clean_titles.append(cleaned)
            
    # 2. Get ALL tags in exactly ONE API call
    ai_results = get_batch_tags_and_categories(clean_titles)
    
    # 3. Process and Cluster
    trend_groups = {}
    for i, item in enumerate(raw_scraped_data):
        if clean_titles[i] == "SKIP": 
            continue
            
        smart_tag = ai_results[i]["tag"]
        category = ai_results[i]["category"]
        
        if not smart_tag or "ERR" in smart_tag: continue 
            
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

        clean_desc = clean_titles[i]
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
