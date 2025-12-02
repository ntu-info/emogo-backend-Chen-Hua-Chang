from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from motor.motor_asyncio import AsyncIOMotorClient
from bson.binary import Binary
from bson.objectid import ObjectId
import os
import io

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

# --- API 區域 (上傳邏輯保持不變) ---

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
    # 接收前端傳來的關聯 ID (這很重要，用來把資料串起來)
    scale_id: str = Form(...) 
):
    if db is None: raise HTTPException(status_code=500, detail="DB not connected")
    try:
        file_content = await file.read()
        vlog_data = {
            "filename": file.filename,
            "slot": slot,
            "mood": mood,
            "scale_id": scale_id, # 存入關聯 ID
            "data": Binary(file_content)
        }
        result = await db["vlogs"].insert_one(vlog_data)
        return {"status": "success", "filename": file.filename}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- 關鍵修改：下載/檢視頁面 (整合顯示) ---

@app.get("/data", response_class=HTMLResponse)
async def view_data():
    if db is None: return "<h1>Error: DB not connected</h1>"
    
    # 1. 先撈出所有的「心情 (Sentiments)」作為主軸
    # 因為心情是每次紀錄的核心
    sentiments = await db["sentiments"].find().sort("timestamp", -1).to_list(100)
    
    table_rows = ""
    
    for s in sentiments:
        # 取得這筆心情的基本資料
        s_id = str(s["_id"])
        timestamp = s.get("timestamp", "Unknown Time")
        slot = s.get("slot", "N/A")
        score = s.get("score", "N/A")
        
        # 2. 去找這筆心情對應的 GPS 資料
        # (前端在存心情時，有把 gps_id 存進去)
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

        # 3. 去找這筆心情對應的 Vlog 資料
        # (前端在存 Vlog 時，有把 scale_id (即 sentiment id) 存進去)
        # 我們用 scale_id 來反查
        vlog_info = "無影片"
        vlog_data = await db["vlogs"].find_one({"scale_id": s_id})
        
        if vlog_data:
            v_filename = vlog_data.get("filename", "video.mp4")
            v_id = str(vlog_data["_id"])
            download_link = f"/download/vlog/{v_id}"
            vlog_info = f"<a href='{download_link}' style='color: blue; text-decoration: underline;'>下載 {v_filename}</a>"

        # 4. 組合成表格的一列
        table_rows += f"""
        <tr style="border-bottom: 1px solid #ddd;">
            <td style="padding: 10px;">{timestamp}</td>
            <td style="padding: 10px;">{slot}</td>
            <td style="padding: 10px; text-align: center;">{score}</td>
            <td style="padding: 10px;">{gps_info}</td>
            <td style="padding: 10px;">{vlog_info}</td>
        </tr>
        """

    # 5. 輸出漂亮的 HTML 表格
    html_content = f"""
    <html>
        <head>
            <title>EmoGo Integrated Data</title>
            <style>
                table {{ border-collapse: collapse; width: 100%; }}
                th {{ background-color: #f2f2f2; padding: 10px; text-align: left; }}
                tr:hover {{ background-color: #f5f5f5; }}
            </style>
        </head>
        <body style="font-family: Arial; padding: 20px;">
            <h1>EmoGo 使用者紀錄總表</h1>
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