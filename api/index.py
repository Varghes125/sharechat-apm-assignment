from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    return {"message": "API working"}

@app.get("/update_trends")
def update_trends():
    print("🔥 Function executed")
    return {"status": "success"}
