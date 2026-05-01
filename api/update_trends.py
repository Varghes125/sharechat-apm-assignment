from supabase import create_client
import feedparser
import os

def handler(request):
    # 1. Connect to Supabase
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    supabase = create_client(url, key)

    # 2. Scrape Signals (as we discussed in Step 1)
    feed = feedparser.parse("https://news.google.com/rss?hl=hi&gl=IN&ceid=IN:hi")

    # 3. Simple Extraction (In your real pitch, mention using LLM for this)
    new_tags = []
    for entry in feed.entries[:10]:
        new_tags.append({
            "tag_name": f"#{entry.title.split()[0]}", # Mocking tag extraction
            "description": entry.title,
            "category": "News",
            "heat_score": 85,
            "source": "Google News"
        })

    # 4. Clear old trends and insert fresh ones
    supabase.table("trending_tags").delete().neq("id", 0).execute()
    supabase.table("trending_tags").insert(new_tags).execute()

    return {"status": "success"}
