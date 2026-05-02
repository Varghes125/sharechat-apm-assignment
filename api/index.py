from supabase import create_client
import feedparser
import os
import json

def handler(request):
    try:
        # 1. Initialize Supabase
        # These variables must be set in Vercel Settings > Environment Variables
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")
        
        if not url or not key:
            return {
                'statusCode': 500,
                'body': json.dumps({"error": "Missing Supabase Credentials"})
            }
            
        supabase = create_client(url, key)
        all_trends = []

        # --- SOURCE 1: GOOGLE NEWS (HINDI) ---
        google_feed = feedparser.parse("[https://news.google.com/rss?hl=hi&gl=IN&ceid=IN:hi](https://news.google.com/rss?hl=hi&gl=IN&ceid=IN:hi)")
        for entry in google_feed.entries[:5]:
            all_trends.append({
                "tag_name": f"#{entry.title.split()[0]}",
                "description": entry.title[:100],
                "category": "News",
                "heat_score": 95,
                "source": "Google News"
            })

        # --- SOURCE 2: TWITTER/X TRENDS (INDIA) ---
        x_feed = feedparser.parse("[https://trends24.in/india/feed/](https://trends24.in/india/feed/)")
        for entry in x_feed.entries[:5]:
            all_trends.append({
                "tag_name": entry.title,
                "description": "Real-time viral topic on X",
                "category": "Social",
                "heat_score": 90,
                "source": "Twitter/X"
            })

        # --- SOURCE 3: REDDIT INDIA ---
        reddit_feed = feedparser.parse("[https://www.reddit.com/r/india/hot/.rss](https://www.reddit.com/r/india/hot/.rss)")
        for entry in reddit_feed.entries[:3]:
            all_trends.append({
                "tag_name": f"#{entry.title.split()[-1][:12]}",
                "description": entry.title[:100],
                "category": "Community",
                "heat_score": 85,
                "source": "Reddit"
            })

        # 2. Update Supabase
        # First, clear the old trends to keep the feed 'Fresh'
        supabase.table("trending_tags").delete().neq("id", 0).execute()
        
        # Second, insert the new ranked list
        supabase.table("trending_tags").insert(all_trends).execute()

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'  # Crucial for your v0 frontend connection
            },
            'body': json.dumps({"status": "success", "count": len(all_trends)})
        }

    except Exception as e:
        print(f"Error: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({"status": "error", "message": str(e)})
        }
