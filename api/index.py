from fastapi import FastAPI

app = FastAPI()

@app.get("/update_trends")
def update_trends():
    return {"status": "Vercel found the handler!"}
