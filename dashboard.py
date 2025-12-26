# dashboard.py
# 臺中市空品微環境儀表板（A2 多點位）
# 解法 A（固定）：雲端只讀 data/taichung_micro_latest.json，不直接連線 API（避免 SSL 憑證問題）
# ===== DEBUG: Cloud 檔案盤點（暫時用，確認完就刪）=====
import os
import streamlit as st

st.write("DEBUG cwd =", os.getcwd())
st.write("DEBUG root files =", os.listdir("."))

if os.path.exists("data"):
    st.write("DEBUG data/ files =", os.listdir("data"))
else:
    st.write("DEBUG data/ folder NOT found")

st.write("DEBUG exists data/taichung_micro_latest.json =",
         os.path.exists(os.path.join("data", "taichung_micro_latest.json")))
# =====================================================

import os
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
import pydeck as pdk

# -----------------------------
# 基本設定
# -----------------------------
st.set_page_config(
    page_title="臺中市空品微環境儀表板（A2 多點位）",
    layout="wide",
    initial_sidebar_state="expanded",
)

APP_TITLE = "臺中市空品微環境儀表板（A2 多點位）"
DATA_JSON_PATH = os.path.join("data", "taichung_micro_latest.json")


# -----------------------------
# PM2.5 分級（依你畫面門檻）
# -----------------------------
def pm25_level(pm25: float) -> Tuple[str, str]:
    """
    門檻（與你畫面一致）：
    <=15.4 良好
    15.5–35.4 普通
    35.5–54.4 敏感族群留意
    >=54.5 不健康
    """
    if pm25 <= 15.4:
        return "良好", "可正常活動。"
    if pm25 <= 35.4:
        return "普通", "多數人可正常活動；敏感族群留意身體狀況。"
    if pm25 <= 54.4:
        return "敏感族群留意", "敏感族群建議減少戶外劇烈活動。"
    return "不健康", "建議減少戶外活動；敏感族群避免外出。"


def pm25_tag(level: str) -> str:
    return {"良好": "🟢", "普通": "🟡", "敏感族群留意": "🟠", "不健康": "🔴"}.get(level, "⚪")


COLOR_MAP = {
    "良好": [0, 200, 120, 180],
    "普通": [240, 200, 0, 180],
    "敏感族群留意": [255, 140, 0, 180],
    "不健康": [230, 60, 60, 180],
}
def color_of(level: str) -> List[int]:
    return COLOR_MAP.get(level, [120, 120, 120, 160])


# -----------------------------
# JSON 讀取與正規化
# -----------------------------
def load_json_snapshot(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_records(obj: Any) -> List[Dict[str, Any]]:
    """
    更強健的 records 萃取：
    支援：
    1) {"records":[...]}
    2) {"data":[...]} / {"data":{"records":[...]}}
    3) {"result":{"records":[...]}}
    4) {"response":{"records":[...]}}
    5) 直接就是 list
    """
    if obj is None:
        return []

    # 直接 list
    if isinstance(obj, list):
        return obj

    if not isinstance(obj, dict):
        return []

    # 直接 records / data
    if isinstance(obj.get("records"), list):
        return obj["records"]
    if isinstance(obj.get("data"), list):
        return obj["data"]

    # 常見巢狀：result/response/data 裡的 records
    for k in ["result", "response", "data"]:
        v = obj.get(k)
        if isinstance(v, dict) and isinstance(v.get("records"), list):
            return v["records"]

    return []


def normalize_df(records: List[Dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(records).copy()
    if df.empty:
        return df

    df.columns = [str(c).strip().lower() for c in df.columns]
    
        "coordinatelatitude": "lat",
        "coordinatelongitude": "lon",
        "hum": "humidity",
        "town": "district",
        "landmark": "name",

    rename_map = {
        "longitude": "lon",
        "lng": "lon",
        "long": "lon",
        "經度": "lon",
        "latitude": "lat",
        "緯度": "lat",
        "pm2_5": "pm25",
        "pm2.5": "pm25",
        "pm25": "pm25",
        "pm2_5_avg": "pm25",
        "temperature": "temp",
        "temp_c": "temp",
        "溫度": "temp",
        "humidity": "humidity",
        "rh": "humidity",
        "濕度": "humidity",
        "district": "district",
        "行政區": "district",
        "area": "district",
        "sitename": "name",
        "site_name": "name",
        "點位": "name",
        "name": "name",
        "time": "time",
        "timestamp": "time",
        "datatime": "time",
        "datetime": "time",
        "測定時間": "time",
        "publishtime": "time",
        "publish_time": "time",
    }

    for k, v in rename_map.items():
        if k in df.columns and v not in df.columns:
            df = df.rename(columns={k: v})

    # 轉數字欄位
    for c in ["lon", "lat", "pm25", "temp", "humidity"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # 必要欄位檢查
    if "lon" not in df.columns or "lat" not in df.columns or "pm25" not in df.columns:
        return pd.DataFrame()

    df = df.dropna(subset=["lon", "lat", "pm25"]).copy()

    # 補欄位
    for c in ["name", "district", "temp", "humidity", "time"]:
        if c not in df.columns:
            df[c] = None

    # 分級與建議
    df["level"] = df["pm25"].apply(lambda x: pm25_level(float(x))[0])
    df["advice"] = df["pm25"].apply(lambda x: pm25_level(float(x))[1])
    df["level_tag"] = df["level"].apply(pm25_tag)
    df["color"] = df["level"].apply(color_of)

    return df


def infer_latest_time_from_timecol(df: pd.DataFrame) -> Optional[str]:
    if "time" not in df.columns:
        return None
    s = df["time"].dropna().astype(str).str.strip()
    if s.empty:
        return None
    parsed = pd.to_datetime(s, errors="coerce")
    parsed = parsed.dropna()
    if parsed.empty:
        return None
    return parsed.max().strftime("%Y-%m-%d %H:%M:%S")


def file_mtime_str(path: str) -> Optional[str]:
    try:
        ts = os.path.getmtime(path)
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


# -----------------------------
# Sidebar
# -----------------------------
st.sidebar.markdown("## 顯示模式")
mode = st.sidebar.radio(
    "選擇畫面",
    ["一般民眾（快速理解）", "專業人員（完整分析）"],
    index=0,
)

st.sidebar.markdown("---")
st.sidebar.markdown("## 連線設定（展示用）")

api_url_hint = st.sidebar.text_input(
    "API URL（展示用，可留空）",
    value="https://datacenter.taichung.gov.tw/…",
    help="本儀表板雲端展示版不直接連線 API，避免 SSL/網路不穩；資料由本機 fetch_local.py 產生 JSON 快照後推送。",
)

api_key_masked = st.sidebar.text_input(
    "API Key（如需，已隱藏）",
    value="********",
    type="password",
    help="雲端展示版不使用 API Key；此欄位僅為增加可信度/讓讀者理解「可支援需要 Key 的資料源」。",
)

colA, colB = st.sidebar.columns(2)
btn_refresh = colA.button("立即更新（重新讀取）", use_container_width=True)
btn_clear = colB.button("清除快取", use_container_width=True)

st.sidebar.markdown("---")
st.sidebar.markdown("## 顯示選項（共用）")
only_geo = st.sidebar.checkbox("只顯示有經緯度的點位", value=True)
only_hot = st.sidebar.checkbox("只顯示超標點位（PM2.5 > 35.4）", value=False)
show_radius = st.sidebar.checkbox("點位半徑隨 PM2.5 變化（自動縮放感）", value=True)
topn = st.sidebar.slider("Top N（PM2.5）", 10, 100, 50, 5)

st.sidebar.markdown("---")
st.sidebar.caption("🔒 雲端固定只讀 JSON 快照，不連線政府 API（避免 SSL 憑證問題，穩定展示）。")

if btn_clear:
    st.cache_data.clear()
    st.toast("已清除快取", icon="🧹")

if btn_refresh:
    st.cache_data.clear()
    st.toast("已重新讀取（清除快取後載入）", icon="🔄")


# -----------------------------
# 讀資料（核心：只讀 JSON）
# -----------------------------
@st.cache_data(ttl=60)
def load_data_snapshot() -> Tuple[pd.DataFrame, Dict[str, Any]]:
    meta: Dict[str, Any] = {
        "source": "臺中市政府 OpenData（微型感測）",
        "snapshot_path": DATA_JSON_PATH,
        "used": "snapshot_json_only",
        "loaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "snapshot_mtime": file_mtime_str(DATA_JSON_PATH),
        "snapshot_fetched_at": None,  # 若 fetch_local.py 有寫入 fetched_at，我們會抓
    }

    if not os.path.exists(DATA_JSON_PATH):
        return pd.DataFrame(), meta

    obj = load_json_snapshot(DATA_JSON_PATH)

    # 若 fetch_local.py 有寫 meta 欄位（例如 fetched_at），這裡可讀出來
    if isinstance(obj, dict):
        # 常見：{"fetched_at":"...","records":[...]}
        if isinstance(obj.get("fetched_at"), str):
            meta["snapshot_fetched_at"] = obj["fetched_at"]
        if isinstance(obj.get("meta"), dict) and isinstance(obj["meta"].get("fetched_at"), str):
            meta["snapshot_fetched_at"] = obj["meta"]["fetched_at"]

    records = extract_records(obj)
    df = normalize_df(records)
    return df, meta


df, meta = load_data_snapshot()

# -----------------------------
# Header
# -----------------------------
st.title(APP_TITLE)
st.caption("資料來源：臺中市政府 OpenData（微型感測：PM2.5／溫度／濕度／經緯度）")

st.success(
    "使用方式：先看「快速判讀」抓重點 → 再看地圖定位；用下拉選擇行政區聚焦；只想看需注意地點可勾選「只顯示超標點位」。",
    icon="✅",
)

# 若沒有 JSON，給明確提示（不報 SSL，不打 API）
if df.empty:
    st.error(
        f"已讀到 JSON 檔，但資料解析後為空（df.empty=True）。\n\n"
        f"✅ 檔案存在：{DATA_JSON_PATH}\n"
        f"❗ 代表問題不是「沒檔案」，而是「JSON 內容欄位無法對應」\n\n"
        f"請確認 JSON 內是否包含：經度、緯度、PM2.5（名稱可能不同）。",
        icon="🚫",
    )
    st.stop()

# -----------------------------
# 篩選：行政區下拉
# -----------------------------
if only_geo:
    df = df.dropna(subset=["lon", "lat"])

if only_hot:
    df = df[df["pm25"] > 35.4]

df["district"] = df["district"].fillna("（未提供行政區）")
df["name"] = df["name"].fillna("（未命名點位）")

districts = ["全市"] + sorted([d for d in df["district"].unique().tolist() if str(d).strip() != ""])
st.markdown("### 選擇行政區（聚焦查看）")
sel_dist = st.selectbox("行政區", districts, index=0, label_visibility="collapsed")

df_view = df.copy()
if sel_dist != "全市":
    df_view = df[df["district"] == sel_dist].copy()

# -----------------------------
# KPI 與時間（不再顯示未知）
# -----------------------------
pm25_median = float(df_view["pm25"].median()) if not df_view.empty else 0.0
level_txt, advice_txt = pm25_level(pm25_median)

latest_from_timecol = infer_latest_time_from_timecol(df_view)

# 顯示優先順序：
# 1) time欄位推得的最新時間
# 2) snapshot_fetched_at（fetch_local.py寫入）
# 3) 檔案最後修改時間 snapshot_mtime
# 4) loaded_at（載入時間）
if latest_from_timecol:
    latest_time_display = latest_from_timecol + "（資料欄位 time 推得）"
elif meta.get("snapshot_fetched_at"):
    latest_time_display = str(meta["snapshot_fetched_at"]) + "（快照產生時間）"
elif meta.get("snapshot_mtime"):
    latest_time_display = str(meta["snapshot_mtime"]) + "（檔案最後修改時間）"
else:
    latest_time_display = str(meta["loaded_at"]) + "（載入時間）"

k1, k2, k3, k4 = st.columns(4)
k1.metric("點位數（每裝置取最新一筆）", f"{len(df_view):,}")
k2.metric("PM2.5 中位數", f"{pm25_median:.1f}", f"{pm25_tag(level_txt)} {level_txt}")
k3.metric("PM2.5 最大值", f"{float(df_view['pm25'].max()):.1f}")
k4.metric("資料時間（最新）", latest_time_display)

# -----------------------------
# 一般民眾：快速判讀文字說明
# -----------------------------
st.markdown("## 快速判讀")
st.info(f"目前 {sel_dist} 整體空品以「{level_txt}」為主（PM2.5 中位數 {pm25_median:.1f}）。{advice_txt}", icon="🧭")

st.markdown("## 你需要留意什麼？")
grp = (
    df.groupby("district", dropna=False)
    .agg(max_pm25=("pm25", "max"), avg_pm25=("pm25", "mean"), cnt=("pm25", "count"))
    .reset_index()
    .sort_values("max_pm25", ascending=False)
)
top3 = grp.head(3)

lines = []
for _, r in top3.iterrows():
    lvl, adv = pm25_level(float(r["max_pm25"]))
    lines.append(
        f"- **{r['district']}**：最高 {r['max_pm25']:.1f}（平均 {r['avg_pm25']:.1f}，點位 {int(r['cnt'])}）"
        f"　{pm25_tag(lvl)} {lvl}｜{adv}"
    )
st.markdown("\n".join(lines))

st.markdown("## 看圖小抄")
st.markdown(
    "- 🟢 ≤15.4：良好　　- 🟡 15.5–35.4：普通　　- 🟠 35.5–54.4：敏感族群留意　　- 🔴 ≥54.5：不健康\n"
    "- 圓點越大代表 PM2.5 越高。\n"
    "- **敏感族群提醒**：如有不適，請減少戶外活動並留意身體狀況。\n"
    f"- 本次顯示時間：**{latest_time_display}**"
)

# -----------------------------
# 地圖：自動縮放中心 + hover tooltip（溫度/濕度/建議）
# -----------------------------
st.markdown("## 地圖（點位分佈：依 PM2.5 分級上色）")

def build_tooltip(row: pd.Series) -> str:
    pm = float(row.get("pm25", 0.0))
    lvl = row.get("level", "")
    adv = row.get("advice", "")
    t = row.get("temp")
    h = row.get("humidity")
    t_txt = f"{float(t):.1f}°C" if pd.notna(t) else "未提供"
    h_txt = f"{float(h):.0f}%" if pd.notna(h) else "未提供"
    return (
        f"{row.get('name','（未命名點位）')}\n"
        f"行政區：{row.get('district','（未提供行政區）')}\n"
        f"PM2.5：{pm:.1f}（{lvl}）\n"
        f"溫度：{t_txt}｜濕度：{h_txt}\n"
        f"建議：{adv}"
    )

df_map = df_view.copy()
df_map["tooltip"] = df_map.apply(build_tooltip, axis=1)

if show_radius:
    df_map["radius"] = (df_map["pm25"].clip(0, 200) / 2.5 + 40).clip(40, 180)
else:
    df_map["radius"] = 60

center_lat = float(df_map["lat"].mean())
center_lon = float(df_map["lon"].mean())

layer = pdk.Layer(
    "ScatterplotLayer",
    data=df_map,
    get_position="[lon, lat]",
    get_fill_color="color",
    get_radius="radius",
    pickable=True,
    auto_highlight=True,
)

# zoom 不寫死太死：依點位範圍略微調整
# 簡化策略：全市預設 11，特定行政區略放大
zoom = 11 if sel_dist == "全市" else 12

deck = pdk.Deck(
    layers=[layer],
    initial_view_state=pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=zoom, pitch=0),
    tooltip={"text": "{tooltip}"},
)

st.pydeck_chart(deck, use_container_width=True)

# -----------------------------
# 表格：Top N +（專業模式）行政區彙整表
# -----------------------------
st.markdown(f"## PM2.5 前 {topn} 高點位")
df_top = df_view.sort_values("pm25", ascending=False).head(topn).copy()

show_cols = ["level_tag", "name", "district", "pm25", "temp", "humidity", "level", "advice", "time", "lon", "lat"]
show_cols = [c for c in show_cols if c in df_top.columns]
st.dataframe(df_top[show_cols], use_container_width=True, height=380)

if mode.startswith("專業"):
    st.markdown("## 專業摘要（統計）")
    c1, c2, c3 = st.columns(3)
    c1.metric("PM2.5 平均", f"{float(df_view['pm25'].mean()):.1f}")
    c2.metric("PM2.5 75 分位數", f"{float(df_view['pm25'].quantile(0.75)):.1f}")
    c3.metric("超標點位數（>35.4）", f"{int((df_view['pm25'] > 35.4).sum()):,}")

    st.markdown("### 行政區分佈（依最高 PM2.5 排序）")
    st.dataframe(grp.head(30), use_container_width=True)

# -----------------------------
# Footer
# -----------------------------
st.markdown("---")
st.caption(
    f"資料來源：{meta.get('source')}｜讀取方式：{meta.get('used')}｜快照：{meta.get('snapshot_path')}｜載入時間：{meta.get('loaded_at')}"
)





