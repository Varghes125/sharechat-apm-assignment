from supabase import create_client
import feedparser
import os
import json

def handler(request):
    try:
        # 1. Check for Env Vars in Vercel Logs
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")
        
        if not url or not key:
            raise Exception("Missing Supabase Credentials in Vercel Settings")

        supabase = create_client(url, key)

        # 2. Scrape News (Freshness constraint)
        feed = feedparser.parse("https://news.google.com/rss?hl=hi&gl=IN&ceid=IN:hi")
        
        # 3. Clean and Insert
        # This solves the Part 1 task: "Output a ranked list"
        new_tags = [{"tag_name": f"#{e.title.split()[0]}", "description": e.title, "category": "News", "heat_score": 80} for e in feed.entries[:10]]
        
        supabase.table("trending_tags").delete().neq("id", 0).execute()
        supabase.table("trending_tags").insert(new_tags).execute()
        
        return {"statusCode": 200, "body": json.dumps({"status": "success"})}
    except Exception as e:
        # This will now appear in your Vercel Runtime Logs
        print(f"ERROR: {str(e)}") 
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
