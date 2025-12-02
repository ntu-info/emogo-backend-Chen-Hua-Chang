from fastapi import FastAPI

# 1. 初始化 APP
app = FastAPI()

# 2. 定義一個根目錄 (首頁)
@app.get("/")
def read_root():
    return {"message": "Hello! EmoGo Backend is successfully running on Render!"}

# 3. 定義一個簡單的測試頁面
@app.get("/test")
def read_test():
    return {"status": "ok", "detail": "This is a test endpoint without database."}