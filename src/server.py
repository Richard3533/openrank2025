import os
import json
import pandas as pd
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from functools import lru_cache
from typing import Optional

# 配置
DEFAULT_LOCAL_PATH = r"D:\courses\contest\openrank2025\top_300_metrics"
BASE_PATH = os.getenv("DATA_PATH", DEFAULT_LOCAL_PATH)

# 2. MaxKB 配置
APP_ID = os.getenv("MAXKB_APP_ID", "019baba3-9fb2-7252-b4fd-24c8984c6d04")
MAXKB_HOST = os.getenv("MAXKB_HOST", "10.132.221.29") 
MAXKB_PORT = os.getenv("MAXKB_PORT", "3001")
MAXKB_API_KEY = os.getenv("MAXKB_API_KEY", "agent-db5c40c7583580b61fe007c11c09c8a1")
MAXKB_API_URL = f"http://{MAXKB_HOST}:{MAXKB_PORT}/chat/api/{APP_ID}/chat/completions"

# 3. 禁用系统代理
os.environ['NO_PROXY'] = '10.132.221.29'

# =========================================================

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === 数据处理核心逻辑 ===

@lru_cache(maxsize=128)
def _load_json_internal(path_str):
    try:
        if not os.path.exists(path_str):
            return None
        with open(path_str, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not data: return None
        
        # 转换为 DataFrame
        df = pd.DataFrame(list(data.items()), columns=['Date', 'Value'])
        df = df.sort_values('Date')
        
        # 提取最后一个值
        last_val = df['Value'].iloc[-1] if not df.empty else 0
        
        # 处理 NumPy 类型转换
        if hasattr(last_val, 'item'):
            last_val = last_val.item()

        return {
            "dates": df['Date'].tolist(),
            "values": df['Value'].tolist(),
            "last_value": last_val
        }
    except Exception as e:
        print(f"Error loading {path_str}: {e}")
        return None

def load_json(repo_name, filename):
    parts = repo_name.split('/')
    if len(parts) < 2: return None
    full_path = os.path.join(BASE_PATH, parts[0], parts[1], filename)
    return _load_json_internal(full_path)

def get_project_context(owner, repo):
    repo_name = f"{owner}/{repo}"
    activity = load_json(repo_name, "activity.json")
    openrank = load_json(repo_name, "openrank.json")
    bus = load_json(repo_name, "bus_factor.json")
    
    context = f"项目 [{repo_name}] 数据快照:\n"
    if activity: context += f"- 最新活跃度: {activity['last_value']:.2f}\n"
    if openrank: context += f"- 最新 OpenRank: {openrank['last_value']:.2f}\n"
    if bus: context += f"- 巴士系数: {bus['last_value']}\n"
    return context

# === API 路由 ===

@app.get("/api/repos")
def get_repos():
    repos = []
    if os.path.exists(BASE_PATH):
        try:
            for owner in os.listdir(BASE_PATH):
                owner_path = os.path.join(BASE_PATH, owner)
                if os.path.isdir(owner_path):
                    for repo in os.listdir(owner_path):
                        if any(f.endswith('.json') for f in os.listdir(os.path.join(owner_path, repo))):
                            repos.append(f"{owner}/{repo}")
        except Exception as e:
            print(f"Scan Error: {e}")
    return {"repos": sorted(repos)}

@app.get("/api/metrics/{owner}/{repo}")
def get_metrics(owner: str, repo: str):
    repo_name = f"{owner}/{repo}"
    return {
        "activity": load_json(repo_name, "activity.json"),
        "openrank": load_json(repo_name, "openrank.json"),
        "code_add": load_json(repo_name, "code_change_lines_add.json"),
        "code_remove": load_json(repo_name, "code_change_lines_remove.json"),
        "bus_factor": load_json(repo_name, "bus_factor.json")
    }

class ChatRequest(BaseModel):
    message: str
    repo_name: str
    compare_repo_name: Optional[str] = None

@app.post("/api/chat")
def chat_with_maxkb(req: ChatRequest):
    try:
        owner1, name1 = req.repo_name.split('/')
        data_context = get_project_context(owner1, name1)
        
        if req.compare_repo_name:
            owner2, name2 = req.compare_repo_name.split('/')
            data_context += "\n---------- VS ----------\n"
            data_context += get_project_context(owner2, name2)
            data_context += "\n请对这两个项目进行对比分析，重点关注活跃度和影响力的差异。"
        else:
            data_context += "\n请分析该项目的健康状况，并给出社区运营建议。"

        full_query = f"【数据背景】\n{data_context}\n\n【用户问题】\n{req.message}"
        print(f"Calling MaxKB for {req.repo_name}...")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {MAXKB_API_KEY}"
        }
        
        payload = {
            "messages": [{"role": "user", "content": full_query}],
            "stream": False
        }
        
        response = requests.post(MAXKB_API_URL, headers=headers, json=payload, timeout=60)
        
        if response.status_code == 200:
            result = response.json()
            if 'choices' in result and len(result['choices']) > 0:
                return {"reply": result['choices'][0]['message']['content']}
            return {"reply": "AI 未返回有效内容。"}
        else:
            return {"reply": f"MaxKB Error ({response.status_code}): {response.text[:100]}"}
            
    except Exception as e:
        print(f"Server Error: {e}")
        return {"reply": f"服务器内部错误: {str(e)}"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)