import os
import json
from datetime import datetime
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse

import requests
from dotenv import load_dotenv


APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "data")
OUT_FILE = os.path.join(DATA_DIR, "taichung_micro_latest.json")

load_dotenv(os.path.join(APP_DIR, ".env"))

UUID = "33093aab-c094-4caf-9653-389ee511a618"

# 你可以在 .env 設定 TAICHUNG_MICRO_API_URL；沒設就用候選清單自動嘗試
ENV_URL = os.getenv("TAICHUNG_MICRO_API_URL", "").strip().strip('"').strip("'")
API_KEY = os.getenv("TAICHUNG_MICRO_API_KEY", "").strip().strip('"').strip("'")


def ensure_dir(p: str):
    os.makedirs(p, exist_ok=True)


def with_query(url: str, add_params: dict) -> str:
    u = urlparse(url)
    q = parse_qs(u.query)
    for k, v in add_params.items():
        q[k] = [str(v)]
    new_query = urlencode({k: q[k][0] for k in q}, doseq=False)
    return urlunparse((u.scheme, u.netloc, u.path, u.params, new_query, u.fragment))


def normalize_records(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        # 常見欄位：records / data / items / result
        for k in ["records", "data", "items", "result"]:
            v = payload.get(k)
            if isinstance(v, list):
                return v
            if isinstance(v, dict) and isinstance(v.get("records"), list):
                return v["records"]
    return []


def build_candidates() -> list[str]:
    # 若使用者在 .env 指定 URL，就把它放第一順位
    candidates = []
    if ENV_URL:
        candidates.append(ENV_URL)

    # 自動候選清單（避免你一直手動換）
    base = "https://datacenter.taichung.gov.tw"
    candidates += [
        f"{base}/OpenData/{UUID}",
        f"{base}/swagger/OpenData/{UUID}",
        f"{base}/api/OpenData/{UUID}",
        f"{base}/openapi/OpenData/{UUID}",
    ]

    # 去重（維持順序）
    seen = set()
    uniq = []
    for u in candidates:
        if u and u not in seen:
            uniq.append(u)
            seen.add(u)
    return uniq


def fetch_json(url: str):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
    }
    if API_KEY:
        headers["Authorization"] = API_KEY
        headers["X-API-KEY"] = API_KEY

    final_url = with_query(url, {"limit": 1000, "offset": 0})

    r = requests.get(final_url, headers=headers, timeout=35)
    r.raise_for_status()

    try:
        return final_url, r.json()
    except Exception:
        return final_url, json.loads(r.text)


def main():
    candidates = build_candidates()

    last_err = None
    used_url = None
    payload = None

    for u in candidates:
        try:
            used_url, payload = fetch_json(u)
            break
        except Exception as e:
            last_err = e

    if payload is None:
        raise RuntimeError(f"所有候選 API 都失敗，最後錯誤：{last_err}")

    records = normalize_records(payload)

    ensure_dir(DATA_DIR)
    saved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    out = {
        "saved_at_local": saved_at,
        "source_api": used_url,
        "count": len(records),
        "records": records,
    }

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"✅ 已更新：{OUT_FILE}")
    print(f"✅ 使用 API：{used_url}")
    print(f"✅ 筆數：{len(records)}")
    print(f"✅ 時間：{saved_at}")


if __name__ == "__main__":
    main()
