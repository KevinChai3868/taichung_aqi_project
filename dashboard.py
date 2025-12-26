# dashboard.py
# 臺中市空品微環境儀表板（A2 多點位）
# 解法 A：雲端（Streamlit Cloud）只讀 data/taichung_micro_latest.json，不直接打 API（避免 SSL 憑證問題）
# 本機可選擇性打 API（但預設也仍以 JSON 快照為主）

import os
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

# --- 可選：本機才需要 requests（雲端不會用到，requirements 有沒有也不影響主要功能） ---
try:
    import requests  # type: ignore
except Exception:
    requests = None  # noqa

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

# 你之前嘗試過的 API 候選（保留在 UI 顯示來源，但雲端不會真的連）
DEFAULT_API_CANDIDATES = [
    "https://datacenter.taichung.gov.tw/swagger/OpenData/33093aab-c094-4caf-9653-389ee511a618?limit=1000&offset=0",
    "https://datacenter.taichung.gov.tw/OpenData/33093aab-c094-4caf-9653-389ee511a618?limit=1000&offset=0",
    "https://datacenter.taichung.gov.tw/api/OpenData/33093aab-c094-4caf-9653-389ee511a618?limit=1000&offset=0",
    "https://datacenter.taichung.gov.tw/openapi/OpenData/33093aab-c094-4caf-9653-389ee511a618?limit=1000&offset=0",
]

# -----------------------------
# 工具：判斷環境
# -----------------------------
def is_streamlit_cloud() -> bool:
    """
    粗略判斷是否在 Streamlit Cloud。
    - Streamlit Cloud 常見環境變數：STREAMLIT_SHARING / STREAMLIT_CLOUD 等（可能會變）
    - 我們採「保守策略」：只要不是明確本機，就當作雲端，避免打 API。
    """
    for k in ["STREAMLIT_SHARING", "STREAMLIT_CLOUD", "STREAMLIT_RUNTIME_ENV"]:
        if os.getenv(k):
            return True
    # GitHub Codespaces / Replit 等也當作雲端類環境，避免 SSL/網路不穩
    if os.getenv("CODESPACES") or os.getenv("REPL_ID"):
        return True
    # 若使用者有顯示設定 LOCAL_RUN=1，才視為本機
    if os.getenv("LOCAL_RUN") == "1":
        return False
    # 預設保守：視為雲端
    return True


# -----------------------------
# 工具：PM2.5 分級（你畫面已在用的門檻）
# -----------------------------
def pm25_level(pm25: float) -> Tuple[str, str]:
    """
    回傳：等級文字、建議短語（給一般民眾可理解）
    門檻沿用你畫面上的版本：
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


def pm25_color_tag(level: str) -> str:
    # 文字標籤用（不強制顏色，避免不同環境渲染差異）
    return {
        "良好": "🟢",
        "普通": "🟡",
        "敏感族群留意": "🟠",
        "不健康": "🔴",
    }.get(level, "⚪")


# -----------------------------
# 工具：讀取 JSON
# -----------------------------
def load_json_snapshot(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_records(obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    支援兩種常見格式：
    1) {"records":[...]}
    2) 直接就是 list / 或 {"data":[...]}
    """
    if isinstance(obj, dict):
        if isinstance(obj.get("records"), list):
            return obj["records"]
        if isinstance(obj.get("data"), list):
            return obj["data"]
    if isinstance(obj, list):
        return obj
    return []


def normalize_df(records: List[Dict[str, Any]]) -> pd.DataFrame:
    """
    盡量容錯：不同資料集欄位名可能不同
    你目前需要的核心欄位：
    - 經度、緯度（lon/lat）
    - PM2.5（pm25）
    - 溫度/濕度（temp/humidity）可有可無
    - 行政區（district）可有可無
    - 點位名稱（name）可有可無
    - 觀測時間（time）可有可無
    """
    df = pd.DataFrame(records).copy()

    # 小寫化欄位，方便對齊
    df.columns = [str(c).strip().lower() for c in df.columns]

    # 欄位別名對齊
    rename_map = {
        "longitude": "lon",
        "lng": "lon",
        "long": "lon",
        "經度": "lon",
        "latitude": "lat",
        "緯度": "lat",
        "pm2_5": "pm25",
        "pm25": "pm25",
        "pm2.5": "pm25",
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

    # 清掉沒有經緯度的點
    if "lon" in df.columns and "lat" in df.columns:
        df = df.dropna(subset=["lon", "lat"])
    else:
        # 沒有經緯度就回傳空，避免地圖報錯
        return pd.DataFrame()

    # PM2.5 缺值就先 drop（地圖與排名都需要）
    if "pm25" in df.columns:
        df = df.dropna(subset=["pm25"])
    else:
        return pd.DataFrame()

    # 加上分級
    levels = df["pm25"].apply(lambda x: pm25_level(float(x))[0])
    advices = df["pm25"].apply(lambda x: pm25_level(float(x))[1])
    df["level"] = levels
    df["advice"] = advices
    df["level_tag"] = df["level"].apply(pm25_color_tag)

    # 補足缺欄位
    for c in ["name", "district", "temp", "humidity", "time"]:
        if c not in df.columns:
            df[c] = None

    return df


def infer_latest_time(df: pd.DataFrame) -> Optional[str]:
    """
    嘗試從 time 欄位推估最新時間，若資料本身不提供，回傳 None
    """
    if "time" not in df.columns:
        return None
    # time 可能是字串：嘗試 parse
    s = df["time"].dropna().astype(str).str.strip()
    if s.empty:
        return None
    # 嘗試多種格式
    parsed = pd.to_datetime(s, errors="coerce", utc=False)
    parsed = parsed.dropna()
    if parsed.empty:
        return None
    # 取最大
    t = parsed.max()
    # 顯示為臺灣常用格式
    return t.strftime("%Y-%m-%d %H:%M:%S")


# -----------------------------
# （本機可選）抓 API：雲端直接禁止
# -----------------------------
def try_fetch_api(urls: List[str], timeout: int = 20) -> Dict[str, Any]:
    if requests is None:
        raise RuntimeError("requests 未安裝，無法抓 API。請改用本機快照 JSON。")

    last_err = None
    for u in urls:
        try:
            r = requests.get(u, timeout=timeout)
            r.raise_for_status()
            return {"ok": True, "url": u, "json": r.json()}
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"所有候選 API 都失敗，最後錯誤：{last_err}")


# -----------------------------
# 版面：Sidebar
# -----------------------------
st.sidebar.markdown("## 顯示模式")
mode = st.sidebar.radio(
    "選擇畫面",
    ["一般民眾（快速理解）", "專業人員（完整分析）"],
    index=0,
)

st.sidebar.markdown("---")
st.sidebar.markdown("## 連線設定")

api_url_hint = st.sidebar.text_input(
    "API URL（可留空）",
    value="https://datacenter.taichung.gov.tw/…",
    help="雲端展示版不直接連線 API（避免 SSL 問題），此欄位僅作為資料來源說明。",
)

api_key_masked = st.sidebar.text_input(
    "API Key（如需，已隱藏）",
    value="********",
    type="password",
    help="本專題雲端展示版不使用 API Key；若你本機需要，可在 .env 或 fetch_local.py 管理。",
)

colA, colB = st.sidebar.columns(2)
btn_refresh = colA.button("立即更新", use_container_width=True)
btn_clear = colB.button("清除快取", use_container_width=True)

st.sidebar.markdown("---")
st.sidebar.markdown("## 顯示選項（共用）")
only_geo = st.sidebar.checkbox("只顯示有經緯度的點位", value=True)
only_hot = st.sidebar.checkbox("只顯示超標點位（PM2.5 > 35.4）", value=False)
show_trend = st.sidebar.checkbox("點位半徑隨 PM2.5 變化", value=True)

topn = st.sidebar.slider("Top N（PM2.5）", min_value=10, max_value=100, value=50, step=5)

st.sidebar.markdown("---")
st.sidebar.caption("🔒 雲端展示版採用資料快照（JSON），不即時連線政府 API，以確保穩定性與安全性。")

# 清除快取
if btn_clear:
    st.cache_data.clear()
    st.toast("已清除快取", icon="🧹")


# -----------------------------
# 讀取資料（核心）
# -----------------------------
@st.cache_data(ttl=60)
def load_data() -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    讀取資料策略（最穩）：
    1) 若 data/taichung_micro_latest.json 存在 → 直接讀（本機/雲端都能跑）
    2) 若不存在：
       - 雲端：直接提示「請先推送 JSON」
       - 本機：可選擇嘗試打 API（仍不建議，因 SSL 常不穩）
    """
    meta: Dict[str, Any] = {
        "source": "臺中市政府 OpenData（微型感測）",
        "snapshot_path": DATA_JSON_PATH,
        "used": None,
        "used_url": None,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    if os.path.exists(DATA_JSON_PATH):
        obj = load_json_snapshot(DATA_JSON_PATH)
        recs = extract_records(obj)
        df = normalize_df(recs)
        meta["used"] = "snapshot_json"
        return df, meta

    # JSON 不存在 → 分流
    if is_streamlit_cloud():
        meta["used"] = "cloud_no_snapshot"
        return pd.DataFrame(), meta

    # 本機才允許嘗試 API（但你可視需求關掉）
    # 若你不希望本機也打 API，可直接回傳空 df
    # 這裡預設：本機會嘗試一次
    result = try_fetch_api(DEFAULT_API_CANDIDATES)
    obj = result["json"]
    recs = extract_records(obj)
    df = normalize_df(recs)
    meta["used"] = "api_local"
    meta["used_url"] = result["url"]
    return df, meta


df, meta = load_data()

# -----------------------------
# Header
# -----------------------------
st.title(APP_TITLE)
st.caption("資料來源：臺中市政府 OpenData（微型感測：PM2.5／溫度／濕度／經緯度）")

# 使用方式提示
st.success(
    "使用方式：先看「快速判讀」抓重點 → 再看地圖定位；可用下拉選擇行政區聚焦查看；若只想看需注意地點，勾選左側「只顯示超標點位」。",
    icon="✅",
)

# -----------------------------
# 若雲端沒 snapshot，給明確提示（不打 API，不報 SSL）
# -----------------------------
if df.empty:
    st.error(
        "目前找不到資料快照（data/taichung_micro_latest.json）。\n\n"
        "✅ 解法 A（建議）：請在本機執行 `python fetch_local.py` 產生最新 JSON，然後 push 到 GitHub。\n"
        "雲端展示版將自動讀取該 JSON，不直接連線 API（避免 SSL 憑證問題）。",
        icon="🚫",
    )
    st.stop()


# -----------------------------
# 篩選
# -----------------------------
if only_geo:
    df = df.dropna(subset=["lon", "lat"])

if only_hot:
    df = df[df["pm25"] > 35.4]

# 行政區下拉（含「全市」）
districts = ["全市"] + sorted([d for d in df["district"].dropna().unique().tolist() if str(d).strip() != ""])
st.markdown("### 選擇行政區（聚焦查看）")
sel_dist = st.selectbox("行政區", districts, index=0, label_visibility="collapsed")

if sel_dist != "全市":
    df_view = df[df["district"] == sel_dist].copy()
else:
    df_view = df.copy()

# -----------------------------
# 指標區：快訊 / 分級
# -----------------------------
pm25_median = float(df_view["pm25"].median()) if not df_view.empty else 0.0
level_txt, advice_txt = pm25_level(pm25_median)

# 最新時間（資料內有 time 就用；否則顯示「以抓取時間為準」）
latest_time = infer_latest_time(df_view)
if latest_time is None:
    latest_time_display = f"{meta['fetched_at']}（本資料集未提供觀測時間欄位）"
else:
    latest_time_display = latest_time

# 四個 KPI
k1, k2, k3, k4 = st.columns(4)
k1.metric("點位數（每裝置取最新一筆）", f"{len(df_view):,}")
k2.metric("PM2.5 中位數", f"{pm25_median:.1f}", f"{pm25_color_tag(level_txt)} {level_txt}")
k3.metric("PM2.5 最大值", f"{float(df_view['pm25'].max()):.1f}")
k4.metric("資料時間（最新）", latest_time_display)

# 快速判讀
st.markdown("## 快速判讀")
st.info(f"臺中市整體空品以「{level_txt}」為主（PM2.5 中位數 {pm25_median:.1f}）。{advice_txt}", icon="🧭")

# 提醒區：你需要留意什麼？
st.markdown("## 你需要留意什麼？")
# 以行政區內「最高 PM2.5」排序（取 Top 3）
tmp = df.copy()
tmp["district"] = tmp["district"].fillna("（未提供行政區）")
grp = tmp.groupby("district", dropna=False).agg(
    max_pm25=("pm25", "max"),
    avg_pm25=("pm25", "mean"),
    cnt=("pm25", "count"),
).reset_index().sort_values("max_pm25", ascending=False)

top3 = grp.head(3)
lines = []
for _, r in top3.iterrows():
    lvl, adv = pm25_level(float(r["max_pm25"]))
    lines.append(
        f"- **{r['district']}**：最高 {r['max_pm25']:.1f}（平均 {r['avg_pm25']:.1f}，點位 {int(r['cnt'])}）"
        f"　{pm25_color_tag(lvl)} {lvl}｜{adv}"
    )
st.markdown("\n".join(lines))

# 看圖小抄：分級門檻與敏感族群提醒（你要求的短語）
st.markdown("## 看圖小抄")
st.markdown(
    "- 🟢 ≤15.4：良好　　- 🟡 15.5–35.4：普通　　- 🟠 35.5–54.4：敏感族群留意　　- 🔴 ≥54.5：不健康\n"
    "- 圓點越大代表 PM2.5 越高。\n"
    "- **敏感族群提醒**：如有不適，請減少戶外活動並留意身體狀況。\n"
    f"- 系統抓取時間：**{meta['fetched_at']}**（雲端展示版採用資料快照）"
)

# -----------------------------
# 地圖（點位分佈）
# -----------------------------
st.markdown("## 地圖（點位分佈：依 PM2.5 分級上色）")

# tooltip（更完整：溫度/濕度/分級建議）
def build_tooltip(row: pd.Series) -> str:
    name = row.get("name") if pd.notna(row.get("name")) else "（未命名點位）"
    dist = row.get("district") if pd.notna(row.get("district")) else "（未提供行政區）"
    pm = float(row.get("pm25", 0.0))
    lvl = row.get("level", "")
    adv = row.get("advice", "")
    t = row.get("temp")
    h = row.get("humidity")
    t_txt = f"{float(t):.1f}°C" if pd.notna(t) else "未提供"
    h_txt = f"{float(h):.0f}%" if pd.notna(h) else "未提供"
    return (
        f"{name}\n"
        f"行政區：{dist}\n"
        f"PM2.5：{pm:.1f}（{lvl}）\n"
        f"溫度：{t_txt}｜濕度：{h_txt}\n"
        f"建議：{adv}"
    )

df_map = df_view.copy()
df_map["tooltip"] = df_map.apply(build_tooltip, axis=1)

# 點位半徑：可隨 PM2.5 變化（你要求的自動縮放感）
if show_trend:
    # 基礎半徑 + 依 pm25 拉伸（限制最大值避免爆表）
    df_map["radius"] = (df_map["pm25"].clip(lower=0, upper=200) / 2.5 + 40).clip(lower=40, upper=180)
else:
    df_map["radius"] = 60

# 顏色分級：用 level_tag 區分（streamlit map 只能用 color 需搭配 st.pydeck）
import pydeck as pdk  # 放這裡避免你 requirements 缺 pydeck 時太早爆

# 顏色映射（RGBA）
COLOR_MAP = {
    "良好": [0, 200, 120, 180],
    "普通": [240, 200, 0, 180],
    "敏感族群留意": [255, 140, 0, 180],
    "不健康": [230, 60, 60, 180],
}

def color_of(level: str) -> List[int]:
    return COLOR_MAP.get(level, [120, 120, 120, 160])

df_map["color"] = df_map["level"].apply(color_of)

# 自動縮放：用點位的平均值作為中心
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

view_state = pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=11, pitch=0)

deck = pdk.Deck(
    layers=[layer],
    initial_view_state=view_state,
    tooltip={"text": "{tooltip}"},
)

st.pydeck_chart(deck, use_container_width=True)

# -----------------------------
# 表格區：Top N 高點位 + 全表
# -----------------------------
st.markdown(f"## PM2.5 前 {topn} 高點位")
df_top = df_view.sort_values("pm25", ascending=False).head(topn).copy()
show_cols = ["level_tag", "name", "district", "pm25", "temp", "humidity", "level", "advice", "time", "lon", "lat"]
show_cols = [c for c in show_cols if c in df_top.columns]
st.dataframe(df_top[show_cols], use_container_width=True, height=380)

# 專業模式：顯示更多統計摘要
if mode.startswith("專業"):
    st.markdown("## 專業摘要（統計）")
    c1, c2, c3 = st.columns(3)
    c1.metric("PM2.5 平均", f"{float(df_view['pm25'].mean()):.1f}")
    c2.metric("PM2.5 75 分位數", f"{float(df_view['pm25'].quantile(0.75)):.1f}")
    c3.metric("超標點位數（>35.4）", f"{int((df_view['pm25'] > 35.4).sum()):,}")

    st.markdown("### 行政區分佈（依最高 PM2.5 排序）")
    st.dataframe(grp.head(20), use_container_width=True)

# 來源資訊
st.markdown("---")
st.caption(
    f"資料來源：{meta['source']}｜讀取方式：{meta['used']}｜快照路徑：{meta['snapshot_path']}"
)
