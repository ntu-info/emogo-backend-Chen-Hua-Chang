from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient
from bson.binary import Binary
from bson.objectid import ObjectId
from bson.json_util import dumps
import os
import io
import json
import csv # [New] 引入 CSV 模組

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

# --- 舊有的分開上傳接口 (保留) ---
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


# --- 🔥 大一統上傳接口 (One-Shot Upload) ---
# 保持你原本的簡易儲存邏輯 (不使用 GridFS)，確保相容性
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
    if db is None: raise HTTPException(status_code=500, detail="DB not connected")
    
    try:
        # 1. 存 GPS
        gps_doc = {
            "latitude": latitude,
            "longitude": longitude,
            "timestamp": timestamp
        }
        gps_result = await db["gps"].insert_one(gps_doc)
        gps_id = str(gps_result.inserted_id) 

        # 2. 存 心情
        sentiment_doc = {
            "score": mood_score,
            "slot": slot,
            "timestamp": timestamp,
            "gps_id": gps_id
        }
        sentiment_result = await db["sentiments"].insert_one(sentiment_doc)
        scale_id = str(sentiment_result.inserted_id) 

        # 3. 存 影片 (維持一般的 Binary 儲存)
        file_content = await file.read()
        vlog_doc = {
            "filename": file.filename,
            "slot": slot,
            "mood": mood_score,
            "scale_id": scale_id, 
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


# --- 下載/檢視頁面 ---

@app.get("/data", response_class=HTMLResponse)
async def view_data():
    if db is None: return "<h1>Error: DB not connected</h1>"
    
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
            <title>EmoGo Data</title>
            <style>
                table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
                th {{ background-color: #f2f2f2; padding: 10px; text-align: left; }}
                tr:hover {{ background-color: #f5f5f5; }}
                .btn {{ background-color: #2196F3; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; }}
            </style>
        </head>
        <body style="font-family: Arial; padding: 20px;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <h1>EmoGo 使用者紀錄總表</h1>
                <a href="/download_all_data" class="btn" target="_blank">📥 匯出 Excel (CSV)</a>
            </div>
            
            <p>這裡整合顯示了每一次紀錄的完整資訊 (時間、心情、GPS、影片)。</p>
            
            <table border="1">
                <thead>
                    <tr><th>時間 (Time)</th><th>時段 (Slot)</th><th>心情 (Mood)</th><th>位置 (GPS)</th><th>影片 (Vlog)</th></tr>
                </thead>
                <tbody>{table_rows}</tbody>
            </table>
        </body>
    </html>
    """
    return html_content

# --- 🔥 修改：匯出 CSV 功能 (針對你的簡易資料庫結構) ---
@app.get("/download_all_data")
async def download_all_csv():
    if db is None: raise HTTPException(status_code=500, detail="DB not connected")
    
    # 1. 準備資料
    # 撈出所有 Mood (主表)
    sentiments = await db["sentiments"].find().sort("timestamp", -1).to_list(1000)
    
    # 建立 GPS 對照表 (加速查詢)
    all_gps = await db["gps"].find().to_list(1000)
    gps_map = {str(g["_id"]): g for g in all_gps}

    # 建立 Vlog 對照表
    # 因為你是用簡單的 vlogs collection 存的，所以我們直接撈這裡
    all_vlogs = await db["vlogs"].find({}, {"data": 0}).to_list(1000) # data:0 代表不撈影片內容，只撈資訊，避免記憶體爆掉
    vlog_map = {}
    for v in all_vlogs:
        if "scale_id" in v:
            vlog_map[str(v["scale_id"])] = v

    # 2. 建立 CSV 內容
    output = io.StringIO()
    writer = csv.writer(output)
    
    # 寫入標頭
    writer.writerow(["Timestamp", "Slot", "Mood_Score", "Latitude", "Longitude", "Vlog_Filename", "Duration", "Vlog_Download_Link"])

    # 寫入資料列
    base_url = "https://emogo-backend-chen-hua-chang.onrender.com" # 你的後端網址前綴

    for s in sentiments:
        s_id = str(s["_id"])
        
        # 找 GPS
        lat = "N/A"
        lng = "N/A"
        if "gps_id" in s and s["gps_id"] in gps_map:
            g = gps_map[s["gps_id"]]
            lat = g.get("latitude", "")
            lng = g.get("longitude", "")
            
        # 找 Vlog
        v_filename = "No Video"
        duration = ""
        download_link = ""
        
        if s_id in vlog_map:
            v = vlog_map[s_id]
            v_filename = v.get("filename", "")
            duration = v.get("duration", "")
            v_id = str(v["_id"])
            download_link = f"{base_url}/download/vlog/{v_id}"

        writer.writerow([
            s.get("timestamp", ""),
            s.get("slot", ""),
            s.get("score", ""),
            lat,
            lng,
            v_filename,
            duration,
            download_link
        ])

    # 3. 回傳 CSV 檔案 (使用 utf-8-sig 支援 Excel 中文)
    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode('utf-8-sig')), 
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=emogo_data.csv"}
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