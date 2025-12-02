from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, StreamingResponse
# 1. 改用 Motor (非同步)
from motor.motor_asyncio import AsyncIOMotorClient
from bson.binary import Binary
from bson.objectid import ObjectId
import os
import io

app = FastAPI()

# 2. 設定資料庫連線
# 從 Render 環境變數拿，如果沒有就用預設值 (請換成您自己的!)
MONGO_URI = os.getenv("MONGO_URI") 
DB_NAME = "emogo_db"

# 全域變數用來存連線物件
db_client = None
db = None

# 3. 啟動事件 (老師範例的寫法)
@app.on_event("startup")
async def startup_db_client():
    global db_client, db
    if MONGO_URI:
        db_client = AsyncIOMotorClient(MONGO_URI)
        db = db_client[DB_NAME]
        print("✅ MongoDB connected successfully via Motor!")
    else:
        print("⚠️ Warning: MONGO_URI not found.")

# 4. 關閉事件
@app.on_event("shutdown")
async def shutdown_db_client():
    if db_client:
        db_client.close()
        print("🛑 MongoDB connection closed.")

# --- API 區域 ---

@app.get("/")
async def read_root():
    return {"message": "EmoGo Backend (Async Motor) is running!"}

# A. 上傳心情
@app.post("/upload/sentiment")
async def upload_sentiment(data: dict):
    if db is None: raise HTTPException(status_code=500, detail="DB not connected")
    # Motor 的寫法要加 await
    result = await db["sentiments"].insert_one(data)
    return {"status": "success", "id": str(result.inserted_id)}

# B. 上傳 GPS
@app.post("/upload/gps")
async def upload_gps(data: dict):
    if db is None: raise HTTPException(status_code=500, detail="DB not connected")
    result = await db["gps"].insert_one(data)
    return {"status": "success", "id": str(result.inserted_id)}

# C. 上傳影片
@app.post("/upload/vlog")
async def upload_vlog(
    file: UploadFile = File(...), 
    slot: str = Form(...), 
    mood: int = Form(...)
):
    if db is None: raise HTTPException(status_code=500, detail="DB not connected")
    try:
        file_content = await file.read()
        vlog_data = {
            "filename": file.filename,
            "slot": slot,
            "mood": mood,
            "data": Binary(file_content)
        }
        # Motor 寫法
        result = await db["vlogs"].insert_one(vlog_data)
        return {"status": "success", "filename": file.filename}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# D. 下載/檢視頁面 (作業要求)
@app.get("/data", response_class=HTMLResponse)
async def view_data():
    if db is None: return "<h1>Error: DB not connected</h1>"
    
    # Motor 讀取資料要用 .to_list(length)
    sentiments = await db["sentiments"].find({}, {"_id": 0}).to_list(100)
    gps_list = await db["gps"].find({}, {"_id": 0}).to_list(100)
    
    # Vlogs 只讀欄位資訊
    vlogs_cursor = db["vlogs"].find({}, {"_id": 1, "filename": 1, "slot": 1, "mood": 1})
    vlogs = await vlogs_cursor.to_list(100)
    
    vlogs_html = []
    for v in vlogs:
        download_link = f"/download/vlog/{str(v['_id'])}"
        vlogs_html.append(f"<li>Slot: {v.get('slot')}, Mood: {v.get('mood')} - <a href='{download_link}'>下載 {v.get('filename')}</a></li>")

    html_content = f"""
    <html>
        <head><title>EmoGo Data (Async)</title></head>
        <body style="font-family: Arial; padding: 20px;">
            <h1>EmoGo Backend Data</h1>
            <h2>1. Sentiments</h2>
            <pre>{sentiments}</pre>
            <h2>2. GPS</h2>
            <pre>{gps_list}</pre>
            <h2>3. Vlogs</h2>
            <ul>{''.join(vlogs_html)}</ul>
        </body>
    </html>
    """
    return html_content

# E. 影片下載
@app.get("/download/vlog/{vlog_id}")
async def download_vlog(vlog_id: str):
    if db is None: raise HTTPException(status_code=500, detail="DB not connected")
    try:
        # Motor 查詢單筆
        vlog = await db["vlogs"].find_one({"_id": ObjectId(vlog_id)})
        if not vlog:
            raise HTTPException(status_code=404, detail="Vlog not found")
            
        return StreamingResponse(io.BytesIO(vlog['data']), media_type="video/mp4")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))