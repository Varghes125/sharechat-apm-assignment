from fastapi import FastAPI
from supabase import create_client
import feedparser
import os
import requests
import math
import difflib
import re
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

# Initialize Gemini
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    # Using 'flash' because it is lightning fast and has a massive free tier
    model = genai.GenerativeModel('gemini-1.5-flash-latest')
else:
    model = None

# -------------------------------
# ROBUST NLP & FALLBACK LOGIC
# -------------------------------
HINDI_STOPWORDS = {"में", "के", "और", "पर", "से", "लिए", "है", "की", "को", "ने", "इन", "का", "एक", "यह", "तथा", "वाला", "वाली", "क्या", "क्यों", "कैसे", "कहा", "गया"}
ENGLISH_STOPWORDS = {"the", "is", "at", "which", "on", "and", "a", "an", "in", "to", "for", "of", "with", "by", "from", "that", "after", "hit", "as", "sees", "news", "live", "updates"}

def clean_text(text):
    """Removes source names and special characters."""
    clean = text.split('|')[0].split('-')[0].split(':')[0].strip()
    return re.sub(r'[^\w\s\u0900-\u097F]', '', clean) # Keep English & Hindi chars

def generate_fallback_tag(title):
    """Tier 2 Logic: If AI fails, extracts meaningful entities, NEVER just the first 3 words."""
    cleaned = clean_text(title)
    words = cleaned.split()
    
    meaningful_words = [w for w in words if w.lower() not in ENGLISH_STOPWORDS and w not in HINDI_STOPWORDS and len(w) > 2]
    
    if len(meaningful_words) < 2:
        return None
        
    return f"#{' '.join(meaningful_words[:3])}"

def get_smart_tag_and_category(title):
    """Tier 1 Logic: Tries Gemini for perfect tagging and categorization."""
    cleaned_title = clean_text(title)
    words = cleaned_title.split()
    
    # RULE 2: Reject small/vague descriptions immediately
    if len(words) < 5: 
        return None, None 

    if not model: 
        return generate_fallback_tag(cleaned_title), "समाचार"

    # Strict prompt to prevent AI from chatting
    prompt = f"""You are a News Editor for an Indian audience. Read this headline: "{cleaned_title}"
Task 1: Extract the core subject in exactly 2 to 3 words. (e.g., पश्चिम बंगाल चुनाव, Share Market). Do NOT use connecting words.
Task 2: Categorize it into ONE of: राजनीति, तकनीक, मनोरंजन, खेल, व्यापार, अंतर्राष्ट्रीय, क्राइम, समाचार.
Format EXACTLY as: TAG | CATEGORY
Do not add any greetings or explanations."""

    try:
        response = model.generate_content(prompt)
        result_text = response.text.strip()
        
        # Extract using Regex to ensure format compliance
        match = re.search(r'([^\n\|]+)\s*\|\s*([^\n]+)', result_text)
        if match:
            tag = match.group(1).strip().replace('#', '')
            category = match.group(2).strip()
            
            # Sanity check: If AI generated a sentence instead of a tag, reject it
            if len(tag.split()) > 4:
                return generate_fallback_tag(cleaned_title), "समाचार"
                
            return f"#{tag}", category
            
    except Exception as e:
        print(f"Gemini API Error: {e}")
        pass
        
    # If API logic fails, fall back to robust NLP
    return generate_fallback_tag(cleaned_title), "समाचार"

def is_similar_topic(new_tag, existing_tag, threshold=0.65):
    """RULE 3: Semantic Clustering. Checks if #बंगाल चुनाव matches #पश्चिम बंगाल चुनाव."""
    t1 = new_tag.replace("#", "").replace(" ", "").lower()
    t2 = existing_tag.replace("#", "").replace(" ", "").lower()
    
    if t1 in t2 or t2 in t1: return True
    return difflib.SequenceMatcher(None, t1, t2).ratio() > threshold

# -------------------------------
# DATA COLLECTION (High Quality Only)
# -------------------------------
def fetch_sources():
    """RULE 6: Collates from highly relevant, high-velocity Indian feeds."""
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
# CORE ENGINE & RANKING
# -------------------------------
@app.get("/")
def root():
    return {"message": "ShareChat Trend Engine Active (Gemini)", "status": "Running"}

@app.get("/update_trends")
def update_trends():
    if not supabase: return {"error": "Supabase missing"}
    
    raw_scraped_data, current_time = fetch_sources()
    trend_groups = {}

    for item in raw_scraped_data:
        smart_tag, category = get_smart_tag_and_category(item["title"])
        
        # RULE 2 Enforced: If tag generator returns None, discard.
        if not smart_tag: continue 
            
        # RULE 3 Enforced: Deduplication & Reusing Tags
        matched_key = None
        for existing_key in trend_groups.keys():
            if is_similar_topic(smart_tag, existing_key):
                matched_key = existing_key
                break
        
        if not matched_key:
            matched_key = smart_tag
            trend_groups[matched_key] = {
                "tag_name": smart_tag,     # RULE 4: Spaces are retained here!
                "descriptions": [],    
                "category": category,
                "score": 0,
                "sources_involved": set(),
                "mentions": 0
            }

        # Context Aggregation: Append description if it's unique
        clean_desc = item["title"].split('-')[0].split('|')[0].strip()
        if clean_desc not in trend_groups[matched_key]["descriptions"]:
            trend_groups[matched_key]["descriptions"].append(clean_desc)

        # RULE 7 Enforced: Sensible Scoring (Exponential Time Decay)
        hours_old = max(0, (current_time - item["time"]).total_seconds() / 3600)
        time_decay_multiplier = math.exp(-hours_old / 12.0) 

        trend_groups[matched_key]["mentions"] += 1
        trend_groups[matched_key]["sources_involved"].add(item["source"])
        trend_groups[matched_key]["score"] += (item["base_score"] * time_decay_multiplier)

    # --- RANKING & FINAL OUTPUT ---
    final_trends = []
    for key, data in trend_groups.items():
        total_score = data["score"]
        sources = list(data["sources_involved"])
        
        # RULE 7 Enforced: The Consensus Multiplier
        cross_platform_multiplier = 1.0 + (0.5 * (len(sources) - 1))
        total_score *= cross_platform_multiplier
            
        # Format Descriptions: Display up to 3 distinct headlines covering the topic
        formatted_descriptions = "\n".join([f"• {desc}" for desc in data["descriptions"][:3]]) 

        final_trends.append({
            "tag_name": data["tag_name"],
            "description": formatted_descriptions,
            "category": data["category"],       
            "heat_score": int(total_score),
            "source": ", ".join(sources)
        })

    # Strict cutoff: Deliver ONLY the Top 10 hottest trends
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



@app.get("/test_gemini")
def test_gemini():
    """Diagnostic tool to list all available models for your specific API Key."""
    if not GEMINI_API_KEY:
        return {"status": "error", "message": "GEMINI_API_KEY environment variable is missing."}
    
    try:
        available_models = []
        # Query Google's servers for models your key has access to
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available_models.append(m.name)
                
        return {
            "status": "success", 
            "message": "These are the exact model strings your API key supports:",
            "supported_models": available_models
        }
    except Exception as e:
        return {
            "status": "error", 
            "message": f"Failed to fetch models. Error: {str(e)}"
        }
