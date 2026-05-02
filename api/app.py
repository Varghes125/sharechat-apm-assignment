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
# 2. STOPWORDS
# -------------------------------
STOPWORDS = set([
    "the","is","at","which","on","and","a","an","in","to","for","of","with","by",
    "from","that","this","it","as","are","was","were"
])

# -------------------------------
# 3. Normalize words
# -------------------------------
def normalize(word):
    return word[:-1] if word.endswith("s") else word

# -------------------------------
# 4. Tag Generator
# -------------------------------
def generate_smart_tag(title):
    title = title.lower()
    title = re.sub(r'[^a-zA-Z0-9\s]', '', title)

    words = [normalize(w) for w in title.split() if w not in STOPWORDS]
    tag_words = words[:3] if len(words) >= 3 else words

    return " ".join([w.capitalize() for w in tag_words])

# -------------------------------
# 5. Similarity
# -------------------------------
def similarity(tag1, tag2):
    set1 = set(tag1.lower().split())
    set2 = set(tag2.lower().split())
    return len(set1 & set2) / len(set1 | set2) if set1 and set2 else 0

# -------------------------------
# 6. Match tags
# -------------------------------
def find_matching_tag(new_tag, existing_tags):
    best_match = None
    best_score = 0

    for tag in existing_tags:
        score = similarity(new_tag, tag)
        if score > best_score:
            best_score = score
            best_match = tag

    return best_match if best_score >= 0.5 else new_tag

# -------------------------------
# 7. India relevance
# -------------------------------
INDIA_KEYWORDS = [
    "india","delhi","mumbai","bjp","modi","rbi","ipl",
    "chennai","kolkata","bangalore","hyderabad",
    "bollywood","cricket","election"
]

def india_score(text):
    text = text.lower()
    matches = sum(1 for word in INDIA_KEYWORDS if word in text)
    return min(1, matches / 3)

# -------------------------------
# 8. Recency
# -------------------------------
def recency_score(ts):
    diff = (datetime.datetime.utcnow() - ts).total_seconds()
    return max(0, 1 - diff / 43200)

# -------------------------------
# 9. Category
# -------------------------------
def classify(text):
    text = text.lower()
    if "cricket" in text or "ipl" in text:
        return "Sports"
    elif "movie" in text or "ott" in text or "youtube" in text:
        return "Entertainment"
    elif "rbi" in text or "market" in text:
        return "Finance"
    elif "election" in text or "bjp" in text:
        return "Politics"
    return "General"

# -------------------------------
# 10. Hindi translation
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
# 11. Root
# -------------------------------
@app.get("/")
def root():
    return {"message": "Trend Engine Active", "supabase": supabase is not None}

# -------------------------------
# 12. Update Trends
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
        raw_scraped_data.append({"title": entry.title, "source": "Google News", "timestamp": ts})

    # -------- Reddit --------
    reddit_feed = feedparser.parse("https://www.reddit.com/r/india/hot/.rss")
    for entry in reddit_feed.entries[:10]:
        raw_scraped_data.append({"title": entry.title, "source": "Reddit", "timestamp": datetime.datetime.utcnow()})

    # -------- Twitter --------
    try:
        req = urllib.request.Request("https://trends24.in/india/feed/", headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as res:
            x_feed = feedparser.parse(res.read())
        for entry in x_feed.entries[:10]:
            raw_scraped_data.append({"title": entry.title, "source": "Twitter/X", "timestamp": datetime.datetime.utcnow()})
    except:
        pass

    # -------- Google Trends --------
    trends_feed = feedparser.parse("https://trends.google.com/trending/rss?geo=IN")
    for entry in trends_feed.entries[:10]:
        raw_scraped_data.append({
            "title": entry.title,
            "source": "Google Trends",
            "timestamp": datetime.datetime.utcnow()
        })

    # -------- YouTube Trending --------
    yt_feed = feedparser.parse("https://www.youtube.com/feeds/videos.xml?chart=mostPopular&regionCode=IN")
    for entry in yt_feed.entries[:10]:
        raw_scraped_data.append({
            "title": entry.title,
            "source": "YouTube",
            "timestamp": datetime.datetime.utcnow()
        })

    # -------------------------------
    # Aggregation
    # -------------------------------
    trend_groups = {}

    for item in raw_scraped_data:
        title = item["title"]

        new_tag = generate_smart_tag(title)
        matched_tag = find_matching_tag(new_tag, trend_groups.keys())

        rec = recency_score(item["timestamp"])
        ind = india_score(title)

        score = 50 + (50 * rec) + (30 * ind)

        if matched_tag not in trend_groups:
            trend_groups[matched_tag] = {
                "tag_name": matched_tag,
                "description": title,
                "category": classify(title),
                "score": 0,
                "sources": set(),
                "mentions": 0
            }

        trend_groups[matched_tag]["score"] += score
        trend_groups[matched_tag]["mentions"] += 1
        trend_groups[matched_tag]["sources"].add(item["source"])

    # -------------------------------
    # Ranking
    # -------------------------------
    final_output = []

    for data in trend_groups.values():
        total_score = data["score"]

        # Cross-platform boost
        total_score *= (1 + 0.5 * len(data["sources"]))

        # Mention boost
        total_score *= (1 + 0.3 * data["mentions"])

        final_output.append({
            "tag_name": to_hindi(data["tag_name"]),
            "description": to_hindi(data["description"]),
            "category": data["category"],
            "heat_score": int(total_score),
            "source": "Multiple" if len(data["sources"]) > 1 else list(data["sources"])[0]
        })

    top_output = sorted(final_output, key=lambda x: x["heat_score"], reverse=True)[:20]

    # -------------------------------
    # Store
    # -------------------------------
    try:
        supabase.table("trending_tags").delete().neq("tag_name", "placeholder").execute()
        supabase.table("trending_tags").insert(top_output).execute()
        return {"status": "success", "data": top_output}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# -------------------------------
# 13. Get Trends
# -------------------------------
@app.get("/get_trends")
def get_trends():
    if not supabase:
        return []

    res = supabase.table("trending_tags").select("*").order("heat_score", desc=True).execute()
    return res.data
