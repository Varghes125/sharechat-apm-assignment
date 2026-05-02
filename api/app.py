from fastapi import FastAPI
from supabase import create_client
import feedparser
import os
import re
import datetime
import urllib.request
import urllib.parse
import json

app = FastAPI()

# -------------------------------
# 1. Supabase Setup
# -------------------------------
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase = create_client(url, key) if url and key else None

# -------------------------------
# 2. Utility: Tag Generator (FREE)
# -------------------------------
STOPWORDS = set([
    "the","is","at","which","on","and","a","an","in","to","for","of","with","by",
    "from","that","this","it","as","are","was","were"
])

def generate_smart_tag(title):
    title = title.lower()
    title = re.sub(r'[^a-zA-Z0-9\s]', '', title)

    words = [w for w in title.split() if w not in STOPWORDS]

    tag_words = words[:3] if len(words) >= 3 else words
    return "#" + "".join([w.capitalize() for w in tag_words])


# -------------------------------
# 3. India Relevance Score
# -------------------------------
INDIA_KEYWORDS = [
    "india","delhi","mumbai","bjp","modi","rbi","ipl",
    "chennai","kolkata","bangalore","hyderabad"
]

def india_score(text):
    text = text.lower()
    return 1 if any(word in text for word in INDIA_KEYWORDS) else 0


# -------------------------------
# 4. Recency Score
# -------------------------------
def recency_score(ts):
    diff = (datetime.datetime.utcnow() - ts).total_seconds()
    return max(0, 1 - diff / 43200)  # 12 hour decay


# -------------------------------
# 5. Simple Category Classifier
# -------------------------------
def classify(text):
    text = text.lower()
    if "cricket" in text or "ipl" in text:
        return "Sports"
    elif "movie" in text or "ott" in text or "film" in text:
        return "Entertainment"
    elif "rbi" in text or "stock" in text or "market" in text:
        return "Finance"
    elif "election" in text or "bjp" in text or "government" in text:
        return "Politics"
    return "General"


# -------------------------------
# 6. Free Hindi Translation
# -------------------------------
def to_hindi(text):
    try:
        url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=hi&dt=t&q=" + urllib.parse.quote(text)
        response = urllib.request.urlopen(url)
        result = json.loads(response.read())
        return result[0][0][0]
    except:
        return text


# -------------------------------
# 7. Root API
# -------------------------------
@app.get("/")
def root():
    return {
        "message": "ShareChat Trend Engine Active",
        "supabase_connected": supabase is not None
    }


# -------------------------------
# 8. Main Trend Engine
# -------------------------------
@app.get("/update_trends")
def update_trends():
    if not supabase:
        return {"error": "Supabase not configured"}

    raw_scraped_data = []

    # -------- Google News --------
    google_feed = feedparser.parse("https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en")
    for entry in google_feed.entries[:10]:
        ts = datetime.datetime(*entry.published_parsed[:6]) if "published_parsed" in entry else datetime.datetime.utcnow()
        raw_scraped_data.append({
            "title": entry.title,
            "source": "Google News",
            "timestamp": ts
        })

    # -------- Twitter (via Trends24) --------
    try:
        req = urllib.request.Request("https://trends24.in/india/feed/", headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as res:
            x_feed = feedparser.parse(res.read())
        for entry in x_feed.entries[:10]:
            raw_scraped_data.append({
                "title": entry.title,
                "source": "Twitter/X",
                "timestamp": datetime.datetime.utcnow()
            })
    except:
        pass

    # -------- Reddit --------
    reddit_feed = feedparser.parse("https://www.reddit.com/r/india/hot/.rss")
    for entry in reddit_feed.entries[:10]:
        raw_scraped_data.append({
            "title": entry.title,
            "source": "Reddit",
            "timestamp": datetime.datetime.utcnow()
        })

    # -------------------------------
    # 9. Aggregation Engine
    # -------------------------------
    trend_groups = {}

    for item in raw_scraped_data:
        title = item["title"]

        tag = generate_smart_tag(title)
        group_key = tag.lower()

        rec = recency_score(item["timestamp"])
        ind = india_score(title)

        score = 50 + (50 * rec) + (30 * ind)

        if group_key not in trend_groups:
            trend_groups[group_key] = {
                "tag_name": tag,
                "description": title,
                "category": classify(title),
                "score": 0,
                "sources": set(),
                "mentions": 0
            }

        trend_groups[group_key]["score"] += score
        trend_groups[group_key]["mentions"] += 1
        trend_groups[group_key]["sources"].add(item["source"])

    # -------------------------------
    # 10. Ranking
    # -------------------------------
    final_output = []

    for data in trend_groups.values():
        total_score = data["score"]

        # cross-platform boost
        total_score *= (1 + 0.5 * len(data["sources"]))

        final_output.append({
            "tag_name": to_hindi(data["tag_name"]),
            "description": to_hindi(data["description"]),
            "category": data["category"],
            "heat_score": int(total_score),
            "source": "Multiple" if len(data["sources"]) > 1 else list(data["sources"])[0]
        })

    top_output = sorted(final_output, key=lambda x: x["heat_score"], reverse=True)[:10]

    # -------------------------------
    # 11. Store in Supabase
    # -------------------------------
    try:
        supabase.table("trending_tags").delete().neq("tag_name", "placeholder").execute()
        supabase.table("trending_tags").insert(top_output).execute()
        return {"status": "success", "count": len(top_output), "data": top_output}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# -------------------------------
# 12. Fetch Trends
# -------------------------------
@app.get("/get_trends")
def get_trends():
    if not supabase:
        return []

    res = supabase.table("trending_tags").select("*").order("heat_score", desc=True).execute()
    return res.data
