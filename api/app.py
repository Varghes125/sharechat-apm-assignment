from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    print("🔥 ROOT HIT")
    return {"message": "API working"}

@app.get("/update_trends")
def update_trends():
    print("🔥 TRENDS HIT")
    return {"status": "success"}
