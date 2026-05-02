from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    print("🔥 Root hit")
    return {"message": "API working"}

@app.get("/update_trends")
def update_trends():
    print("🔥 Trends endpoint hit")
    return {"status": "success"}
