from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient
from bson.binary import Binary
from bson.objectid import ObjectId
from bson.json_util import dumps # 用來處理 MongoDB 的特殊格式
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

# --- 下載/檢視頁面 (已加入 JSON 匯出按鈕) ---

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
            # 影片本來就是檔案，保留個別下載連結
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

# --- 新增：打包下載所有文字資料 (JSON) ---
@app.get("/download_all_data")
async def download_all_json():
    if db is None: raise HTTPException(status_code=500, detail="DB not connected")
    
    # 撈取所有文字型資料 (排除影片 binary 內容以免檔案太大)
    sentiments = await db["sentiments"].find({}, {"_id": 0}).to_list(1000)
    gps_data = await db["gps"].find({}, {"_id": 0}).to_list(1000)
    # Vlog 只撈 metadata (檔名、關聯ID)，不撈 content
    vlogs_meta = await db["vlogs"].find({}, {"_id": 0, "data": 0}).to_list(1000)

    export_data = {
        "sentiments": sentiments,
        "gps_coordinates": gps_data,
        "vlogs_metadata": vlogs_meta
    }
    
    # 回傳可下載的 JSON 檔案
    return JSONResponse(
        content=json.loads(dumps(export_data)), # 使用 dumps 處理 ObjectId 等特殊格式
        headers={"Content-Disposition": "attachment; filename=emogo_full_data.json"}
    )

# E. 影片下載 (保持不變)
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