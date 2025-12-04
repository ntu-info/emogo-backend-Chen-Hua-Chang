from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient
from bson.binary import Binary
from bson.objectid import ObjectId
from bson.json_util import dumps
import os
import io
import json

app = FastAPI()

# 資料庫連線設定
MONGO_URI = os.getenv("MONGO_URI") 
DB_NAME = "emogo_db"

db_client = None
db = None

@app.on_event("startup")
async def startup_db_client():
    global db_client, db
    if MONGO_URI:
        db_client = AsyncIOMotorClient(MONGO_URI)
        db = db_client[DB_NAME]
        print("✅ MongoDB connected!")
    else:
        print("⚠️ Warning: MONGO_URI not found.")

@app.on_event("shutdown")
async def shutdown_db_client():
    if db_client:
        db_client.close()

# --- API 區域 ---

@app.get("/")
async def read_root():
    return {"message": "EmoGo Backend is running!"}

# --- 舊有的分開上傳接口 (保留以備不時之需) ---
@app.post("/upload/sentiment")
async def upload_sentiment(data: dict):
    if db is None: raise HTTPException(status_code=500, detail="DB not connected")
    result = await db["sentiments"].insert_one(data)
    return {"status": "success", "id": str(result.inserted_id)}

@app.post("/upload/gps")
async def upload_gps(data: dict):
    if db is None: raise HTTPException(status_code=500, detail="DB not connected")
    result = await db["gps"].insert_one(data)
    return {"status": "success", "id": str(result.inserted_id)}

@app.post("/upload/vlog")
async def upload_vlog(
    file: UploadFile = File(...), 
    slot: str = Form(...), 
    mood: int = Form(...),
    scale_id: str = Form(...) 
):
    if db is None: raise HTTPException(status_code=500, detail="DB not connected")
    try:
        file_content = await file.read()
        vlog_data = {
            "filename": file.filename,
            "slot": slot,
            "mood": mood,
            "scale_id": scale_id,
            "data": Binary(file_content)
        }
        result = await db["vlogs"].insert_one(vlog_data)
        return {"status": "success", "filename": file.filename}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- 🔥 新增：大一統上傳接口 (One-Shot Upload) ---
# 這個接口專門給 App 背景上傳使用，一次接收所有欄位
@app.post("/upload/full_record")
async def upload_full_record(
    file: UploadFile = File(...),
    mood_score: int = Form(...),
    slot: str = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...),
    timestamp: str = Form(...),
    duration: str = Form(None)
):
    """
    一次接收 GPS、心情、影片，並在後端自動拆解儲存。
    這樣前端只需要發送一次請求，避免背景執行時斷線。
    """
    if db is None: raise HTTPException(status_code=500, detail="DB not connected")
    
    try:
        # 1. 先存 GPS 資料
        gps_doc = {
            "latitude": latitude,
            "longitude": longitude,
            "timestamp": timestamp
        }
        gps_result = await db["gps"].insert_one(gps_doc)
        gps_id = str(gps_result.inserted_id) # 拿到 GPS ID

        # 2. 再存 心情 (Sentiment) 資料，並關聯 GPS ID
        sentiment_doc = {
            "score": mood_score, # 注意：資料庫顯示用 "score"
            "slot": slot,
            "timestamp": timestamp,
            "gps_id": gps_id
        }
        sentiment_result = await db["sentiments"].insert_one(sentiment_doc)
        scale_id = str(sentiment_result.inserted_id) # 拿到心情 ID (即 scale_id)

        # 3. 最後存 影片 (Vlog) 資料，並關聯 scale_id
        file_content = await file.read()
        vlog_doc = {
            "filename": file.filename,
            "slot": slot,
            "mood": mood_score,
            "scale_id": scale_id, # 這裡做關聯
            "duration": duration,
            "data": Binary(file_content),
            "timestamp": timestamp
        }
        await db["vlogs"].insert_one(vlog_doc)

        print(f"✅ Full record saved! GPS: {gps_id}, Scale: {scale_id}")
        return {"status": "success", "message": "All data saved successfully"}

    except Exception as e:
        print(f"❌ Upload failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Full upload failed: {str(e)}")


# --- 下載/檢視頁面 (保持不變) ---

@app.get("/data", response_class=HTMLResponse)
async def view_data():
    if db is None: return "<h1>Error: DB not connected</h1>"
    
    # 撈出資料
    sentiments = await db["sentiments"].find().sort("timestamp", -1).to_list(100)
    
    table_rows = ""
    
    for s in sentiments:
        s_id = str(s["_id"])
        timestamp = s.get("timestamp", "Unknown Time")
        slot = s.get("slot", "N/A")
        score = s.get("score", "N/A")
        
        # 關聯 GPS
        gps_info = "無 GPS 資料"
        if "gps_id" in s:
            try:
                gps_data = await db["gps"].find_one({"_id": ObjectId(s["gps_id"])})
                if gps_data:
                    lat = gps_data.get('latitude', 0)
                    lng = gps_data.get('longitude', 0)
                    gps_info = f"{lat:.4f}, {lng:.4f}"
            except:
                gps_info = "GPS ID 格式錯誤"

        # 關聯 Vlog
        vlog_info = "無影片"
        vlog_data = await db["vlogs"].find_one({"scale_id": s_id})
        
        if vlog_data:
            v_filename = vlog_data.get("filename", "video.mp4")
            v_id = str(vlog_data["_id"])
            download_link = f"/download/vlog/{v_id}"
            vlog_info = f"<a href='{download_link}' style='color: blue; text-decoration: underline;'>下載 {v_filename}</a>"

        table_rows += f"""
        <tr style="border-bottom: 1px solid #ddd;">
            <td style="padding: 10px;">{timestamp}</td>
            <td style="padding: 10px;">{slot}</td>
            <td style="padding: 10px; text-align: center;">{score}</td>
            <td style="padding: 10px;">{gps_info}</td>
            <td style="padding: 10px;">{vlog_info}</td>
        </tr>
        """

    html_content = f"""
    <html>
        <head>
            <title>EmoGo Integrated Data</title>
            <style>
                table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
                th {{ background-color: #f2f2f2; padding: 10px; text-align: left; }}
                tr:hover {{ background-color: #f5f5f5; }}
                .btn {{
                    background-color: #4CAF50; color: white; padding: 10px 20px;
                    text-decoration: none; border-radius: 5px; font-size: 16px;
                }}
                .btn:hover {{ background-color: #45a049; }}
            </style>
        </head>
        <body style="font-family: Arial; padding: 20px;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <h1>EmoGo 使用者紀錄總表</h1>
                <a href="/download_all_data" class="btn" target="_blank">📥 匯出所有資料 (JSON)</a>
            </div>
            
            <p>這裡整合顯示了每一次紀錄的完整資訊 (時間、心情、GPS、影片)。</p>
            
            <table border="1">
                <thead>
                    <tr>
                        <th>時間 (Time)</th>
                        <th>時段 (Slot)</th>
                        <th>心情 (Mood)</th>
                        <th>位置 (GPS)</th>
                        <th>影片 (Vlog)</th>
                    </tr>
                </thead>
                <tbody>
                    {table_rows}
                </tbody>
            </table>
        </body>
    </html>
    """
    return html_content

@app.get("/download_all_data")
async def download_all_json():
    if db is None: raise HTTPException(status_code=500, detail="DB not connected")
    
    sentiments = await db["sentiments"].find({}, {"_id": 0}).to_list(1000)
    gps_data = await db["gps"].find({}, {"_id": 0}).to_list(1000)
    vlogs_meta = await db["vlogs"].find({}, {"_id": 0, "data": 0}).to_list(1000)

    export_data = {
        "sentiments": sentiments,
        "gps_coordinates": gps_data,
        "vlogs_metadata": vlogs_meta
    }
    
    return JSONResponse(
        content=json.loads(dumps(export_data)), 
        headers={"Content-Disposition": "attachment; filename=emogo_full_data.json"}
    )

@app.get("/download/vlog/{vlog_id}")
async def download_vlog(vlog_id: str):
    if db is None: raise HTTPException(status_code=500, detail="DB not connected")
    try:
        vlog = await db["vlogs"].find_one({"_id": ObjectId(vlog_id)})
        if not vlog:
            raise HTTPException(status_code=404, detail="Vlog not found")
        return StreamingResponse(io.BytesIO(vlog['data']), media_type="video/mp4")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))