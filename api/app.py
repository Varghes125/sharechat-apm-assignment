from fastapi import FastAPI
from supabase import create_client
import feedparser
import os
import json

app = FastAPI()

# 1. Initialize Supabase
# Ensure these are set in your Vercel Environment Variables
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase = create_client(url, key) if url and key else None

@app.get("/")
def root():
    return {"message": "ShareChat Trend Engine Active", "supabase_connected": supabase is not None}

@app.get("/update_trends")
def update_trends():
    if not supabase:
        return {"error": "Supabase credentials not configured"}

    all_trends = []

    # --- SOURCE 1: GOOGLE NEWS (Hindi/India focus) ---
    # Parameters: hl=hi (Hindi), gl=IN (India), ceid=IN:hi
    google_url = "https://news.google.com/rss?hl=hi&gl=IN&ceid=IN:hi"
    google_feed = feedparser.parse(google_url)
    for entry in google_feed.entries[:5]:
        all_trends.append({
            "tag_name": f"#{entry.title.split()[0]}",
            "description": entry.title[:100],
            "category": "News",
            "heat_score": 95,  # Changed 95.0 to 95
            "source": "Google News"
        })

    
   # --- SOURCE 2: X / TWITTER TRENDS (Improved) ---
    import urllib.request
    
    x_url = "https://trends24.in/india/"
    try:
        # We add a User-Agent header to mimic a Chrome browser
        req = urllib.request.Request(x_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            x_html = response.read()
            x_feed = feedparser.parse(x_html)
            
        for entry in x_feed.entries[:5]:
            all_trends.append({
                "tag_name": entry.title,
                "description": "Viral topic on X (India)",
                "category": "Social",
                "heat_score": 90,
                "source": "Twitter/X"
            })
    except Exception as x_error:
        print(f"X Scraping failed: {x_error}")
        # We don't crash the whole app, just log that X failed




    
    # --- SOURCE 3: REDDIT (r/India Hot) ---
    reddit_url = "https://www.reddit.com/r/india/hot/.rss"
    # Note: User-Agent is sometimes required for Reddit RSS
    reddit_feed = feedparser.parse(reddit_url)
    for entry in reddit_feed.entries[:3]:
        all_trends.append({
            "tag_name": f"#{entry.title.split()[-1][:12]}",
            "description": entry.title[:100],
            "category": "Community",
            "heat_score": 80,  # Changed 80.0 to 80
            "source": "Reddit"
        })

    try:
        # Step 2: Clear old trends to maintain "Freshness"
        # This deletes entries that aren't the 'id 0' placeholder if you have one
        supabase.table("trending_tags").delete().neq("tag_name", "placeholder").execute()

        # Step 3: Insert fresh ranked trends
        result = supabase.table("trending_tags").insert(all_trends).execute()
        
        return {
            "status": "success",
            "new_trends_count": len(all_trends),
            "sources_synced": ["Google News", "X", "Reddit"]
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/get_trends")
def get_trends():
    if not supabase:
        return {"error": "Supabase not connected"}
    
    # Fetch top 15 trends ranked by heat_score
    response = supabase.table("trending_tags")\
        .select("*")\
        .order("heat_score", desc=True)\
        .limit(15)\
        .execute()
        
    return response.data
