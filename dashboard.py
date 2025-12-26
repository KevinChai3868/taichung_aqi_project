import os
import json
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse
import math

import requests
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
import pydeck as pdk


# =========================
# 0) 讀取 .env（與本檔同一層）
# =========================
APP_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(APP_DIR, ".env")
load_dotenv(ENV_PATH)

API_URL = os.getenv("TAICHUNG_MICRO_API_URL", "").strip().strip('"').strip("'")
API_KEY = os.getenv("TAICHUNG_MICRO_API_KEY", "").strip().strip('"').strip("'")

TZ_TW = timezone(timedelta(hours=8))
DATA_DIR = os.path.join(APP_DIR, "data")
CACHE_FILE = os.path.join(DATA_DIR, "taichung_micro_latest.json")

UUID = "33093aab-c094-4caf-9653-389ee511a618"
DEFAULT_SWAGGER_URL = f"https://datacenter.taichung.gov.tw/swagger/OpenData/{UUID}"


# =========================
# 工具函式
# =========================
def ensure_dir(p: str):
    os.makedirs(p, exist_ok=True)


def now_tw():
    return datetime.now(TZ_TW)


def safe_float(x):
    try:
        if x is None:
            return None
        s = str(x).strip()
        if s == "" or s.lower() in ("nan", "none", "null"):
            return None
        return float(s)
    except Exception:
        return None


def normalize_records(payload):
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ["records", "data", "items", "result"]:
            v = payload.get(k)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                vv = v.get("records")
                if isinstance(vv, list):
                    return vv
    return []


def with_query(url: str, add_params: dict):
    u = urlparse(url)
    q = parse_qs(u.query)
    for k, v in add_params.items():
        if k not in q:
            q[k] = [str(v)]
    new_query = urlencode({k: q[k][0] for k in q}, doseq=False)
    return urlunparse((u.scheme, u.netloc, u.path, u.params, new_query, u.fragment))


def candidate_urls(base_url: str):
    base_url = base_url.strip()
    cands = []
    if base_url:
        cands.append(with_query(base_url, {"limit": 1000, "offset": 0}))
    cands.append(with_query(DEFAULT_SWAGGER_URL, {"limit": 1000, "offset": 0}))
    cands.append(with_query(f"https://datacenter.taichung.gov.tw/OpenData/{UUID}", {"limit": 1000, "offset": 0}))
    cands.append(with_query(f"https://datacenter.taichung.gov.tw/api/OpenData/{UUID}", {"limit": 1000, "offset": 0}))
    cands.append(with_query(f"https://datacenter.taichung.gov.tw/api/v1/OpenData/{UUID}", {"limit": 1000, "offset": 0}))
    cands.append(with_query(f"https://datacenter.taichung.gov.tw/openapi/OpenData/{UUID}", {"limit": 1000, "offset": 0}))

    seen = set()
    uniq = []
    for u in cands:
        if u not in seen:
            uniq.append(u)
            seen.add(u)
    return uniq


def fetch_json(url: str, api_key: str):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "Connection": "close",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if api_key:
        headers["Authorization"] = api_key
        headers["X-API-KEY"] = api_key

    s = requests.Session()
    r = s.get(url, headers=headers, timeout=35)
    r.raise_for_status()

    try:
        payload = r.json()
    except Exception:
        payload = json.loads(r.text)
    return payload


@st.cache_data(ttl=60)
def fetch_records_smart(base_url: str, api_key: str):
    last_err = None
    tried = []
    for u in candidate_urls(base_url):
        tried.append(u)
        try:
            payload = fetch_json(u, api_key)
            records = normalize_records(payload)
            if isinstance(payload, list) and len(payload) > 0:
                return u, payload
            if records and len(records) > 0:
                return u, records
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(
        "所有候選 API 都抓不到資料。\n"
        f"最後錯誤：{last_err}\n"
        f"已嘗試：\n- " + "\n- ".join(tried)
    )


def build_df(records):
    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records).copy()

    candidates = {
        "Device": ["Device", "device", "設備", "裝置"],
        "Town": ["Town", "town", "district", "area", "行政區", "區", "鄉鎮市區"],
        "Landmark": ["Landmark", "landmark", "name", "location", "地標", "站名", "地點"],
        "Lat": ["CoordinateLatitude", "latitude", "lat", "CoordinateLat", "Coordinate_Latitude", "緯度"],
        "Lon": ["Coordinatelongitude", "longitude", "lon", "lng", "CoordinateLon", "Coordinate_Longitude", "經度"],
        "PM25": ["PM2.5", "pm2.5", "pm25", "PM25", "pm2_5", "PM2_5", "細懸浮微粒", "PM2_5_UGM3"],
        "Temp": ["Temp", "temp", "temperature", "溫度", "TEMP"],
        "Hum": ["Hum", "hum", "humidity", "濕度", "HUM"],
        "Id": ["Id", "id"],
    }

    rename = {}
    for std, cands in candidates.items():
        for c in cands:
            if c in df.columns:
                rename[c] = std
                break
    df = df.rename(columns=rename)

    for c in ["Lat", "Lon", "PM25", "Temp", "Hum"]:
        if c in df.columns:
            df[c] = df[c].apply(safe_float)

    return df


def latest_per_device(df):
    if df.empty:
        return df
    if "Device" not in df.columns:
        return df
    return df.drop_duplicates(subset=["Device"], keep="first")


def pm25_level(pm):
    if pm is None:
        return "無資料"
    if pm <= 15.4: return "良好"
    if pm <= 35.4: return "普通"
    if pm <= 54.4: return "對敏感族群不健康"
    if pm <= 150.4: return "不健康"
    if pm <= 250.4: return "非常不健康"
    return "危害"


def pm25_advice(level: str):
    if level == "良好":
        return "可正常活動。"
    if level == "普通":
        return "可正常活動；敏感族群留意。"
    if level == "對敏感族群不健康":
        return "敏感族群減少長時間戶外活動。"
    if level == "不健康":
        return "建議減少戶外活動，必要時戴口罩。"
    if level == "非常不健康":
        return "盡量避免外出；敏感族群建議留在室內。"
    if level == "危害":
        return "避免外出；若需外出請加強防護。"
    return "暫無建議（資料不足）。"


def sensitive_note(level: str):
    if level in ("良好", "普通"):
        return "敏感族群留意身體狀況"
    if level in ("對敏感族群不健康", "不健康", "非常不健康", "危害"):
        return "敏感族群建議減少戶外活動"
    return "—"


def pm25_color(pm):
    if pm is None:
        return [160, 160, 160, 160]
    if pm <= 15.4:
        return [0, 180, 90, 180]
    if pm <= 35.4:
        return [255, 210, 0, 180]
    if pm <= 54.4:
        return [255, 140, 0, 180]
    if pm <= 150.4:
        return [230, 0, 0, 180]
    if pm <= 250.4:
        return [150, 0, 200, 180]
    return [120, 60, 0, 180]


def pm25_radius(pm, base=60, max_r=260):
    if pm is None:
        return base
    r = base + (pm ** 0.5) * 25
    return min(max_r, max(base, r))


def save_cache(records, used_url: str, fetch_time_str: str):
    ensure_dir(DATA_DIR)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"saved_at_tw": fetch_time_str, "used_api": used_url, "count": len(records), "records": records},
            f,
            ensure_ascii=False,
            indent=2,
        )


def citizen_summary(df: pd.DataFrame, fetch_time_str: str):
    if df.empty or "PM25" not in df.columns:
        return {
            "headline": f"目前尚無足夠資料可判讀（系統抓取時間：{fetch_time_str}）。",
            "district": "未取得 PM2.5 欄位或資料為空，建議稍後再更新。",
            "howto": "若畫面點位很多，建議先開啟「只顯示超標點位」。"
        }

    pm = df["PM25"].dropna()
    if pm.empty:
        return {
            "headline": f"目前 PM2.5 暫無可用數值（系統抓取時間：{fetch_time_str}）。",
            "district": "建議稍後再更新，或確認資料源是否正常。",
            "howto": "可切換到「專業人員版」查看原始欄位是否完整。"
        }

    median = float(pm.median())
    med_level = pm25_level(median)

    if med_level in ("良好", "普通"):
        headline = f"臺中市整體空品以「{med_level}」為主（PM2.5 中位數 {median:.1f}）。多數地區可正常活動。"
    else:
        headline = f"臺中市目前空品偏「{med_level}」（PM2.5 中位數 {median:.1f}）。建議敏感族群減少長時間戶外活動。"

    district_text = ""
    if "Town" in df.columns:
        dd = df[df["Town"].notna() & (df["Town"].astype(str).str.strip() != "")].copy()
        dd = dd[dd["PM25"].notna()]
        if len(dd) > 0:
            g = dd.groupby("Town")["PM25"]
            summary = pd.DataFrame({"最大": g.max(), "平均": g.mean(), "點位數": g.count()}).sort_values(by="最大", ascending=False)
            top3 = summary.head(3)
            lines = [f"- {town}：最高 {row['最大']:.1f}（平均 {row['平均']:.1f}，點位 {int(row['點位數'])}）"
                     for town, row in top3.iterrows()]
            district_text = "需要留意的行政區（以區內最高 PM2.5 排序）：\n" + "\n".join(lines)
        else:
            district_text = "行政區資訊不足，暫以全市數值判讀。"
    else:
        district_text = "資料未提供行政區欄位，暫以全市數值判讀。"

    howto = (
        f"怎麼看這張圖：\n"
        f"- 🟢 ≤15.4：良好　🟡 15.5–35.4：普通　🟠 35.5–54.4：敏感族群留意　🔴 ≥54.5：不健康\n"
        f"- 圓點越大代表 PM2.5 越高。\n"
        f"- 系統抓取時間：{fetch_time_str}（本資料集未提供觀測時間戳）"
    )

    return {"headline": headline, "district": district_text, "howto": howto}


def district_table(df: pd.DataFrame):
    if "Town" not in df.columns or "PM25" not in df.columns:
        return pd.DataFrame()
    dd = df.copy()
    dd = dd[dd["Town"].notna() & (dd["Town"].astype(str).str.strip() != "")]
    dd = dd[dd["PM25"].notna()]
    if dd.empty:
        return pd.DataFrame()
    g = dd.groupby("Town")["PM25"]
    tbl = pd.DataFrame({
        "點位數": g.count(),
        "平均 PM2.5": g.mean().round(1),
        "最大 PM2.5": g.max().round(1),
        "中位數 PM2.5": g.median().round(1),
    }).sort_values(by="最大 PM2.5", ascending=False)
    return tbl


def district_stats_line(df: pd.DataFrame, town: str):
    if df.empty or "PM25" not in df.columns:
        return "此行政區目前沒有足夠資料可判讀。"
    pm = df["PM25"].dropna()
    if pm.empty:
        return "此行政區目前沒有 PM2.5 可用數值。"
    n = int(pm.count())
    med = float(pm.median())
    mx = float(pm.max())
    lvl = pm25_level(med)
    msg = f"{town}目前整體屬「{lvl}」（中位數 {med:.1f}）。{pm25_advice(lvl)}（點位數 {n}，最高 {mx:.1f}）"
    return msg


# =========================
# ✅ 自動縮放：由點位 bounds 推估 zoom
# =========================
def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def zoom_from_bounds(lat_min, lat_max, lon_min, lon_max, viewport_px=(1100, 650), padding=1.25):
    lat_span = max(1e-6, lat_max - lat_min)
    lon_span = max(1e-6, lon_max - lon_min)

    def lat_to_mercator_y(lat):
        lat = clamp(lat, -85.0, 85.0)
        rad = math.radians(lat)
        return math.log(math.tan(rad / 2.0 + math.pi / 4.0))

    y_min = lat_to_mercator_y(lat_min)
    y_max = lat_to_mercator_y(lat_max)
    y_span = max(1e-6, y_max - y_min)

    vp_w, vp_h = viewport_px
    scale_x = (vp_w / 256.0) / lon_span
    scale_y = (vp_h / 256.0) / y_span
    scale = min(scale_x, scale_y) / padding

    zoom = math.log(scale, 2)
    return clamp(zoom, 8, 14)


def view_state_for_points(df_map: pd.DataFrame, default_zoom=10):
    m = df_map.dropna(subset=["Lat", "Lon"]).copy()
    if m.empty:
        return pdk.ViewState(latitude=24.15, longitude=120.67, zoom=default_zoom, pitch=0)

    lat_min, lat_max = float(m["Lat"].min()), float(m["Lat"].max())
    lon_min, lon_max = float(m["Lon"].min()), float(m["Lon"].max())

    center_lat = (lat_min + lat_max) / 2.0
    center_lon = (lon_min + lon_max) / 2.0

    if (lat_max - lat_min) < 0.002 and (lon_max - lon_min) < 0.002:
        z = 13
    else:
        z = zoom_from_bounds(lat_min, lat_max, lon_min, lon_max)

    return pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=z, pitch=0)


# =========================
# UI
# =========================
st.set_page_config(page_title="臺中市空品微環境儀表板（A2 多點位）", layout="wide")
st.title("臺中市空品微環境儀表板（A2 多點位）")
st.caption("資料來源：臺中市政府 OpenData（微型感測：PM2.5／溫度／濕度／經緯度）")

with st.sidebar:
    st.header("顯示模式")
    mode = st.radio("選擇畫面", ["一般民眾（快速理解）", "專業人員（完整分析）"], index=0)

    st.divider()
    st.header("連線設定")
    url = st.text_input("API URL（可留空）", value=API_URL)
    api_key = st.text_input("API Key（如需，已隱藏）", value=API_KEY, type="password")

    c1, c2 = st.columns(2)
    with c1:
        btn_refresh = st.button("立即更新", use_container_width=True)
    with c2:
        btn_clear = st.button("清除快取", use_container_width=True)

    if btn_clear:
        st.cache_data.clear()
        st.success("已清除快取（下次會重新抓）")

    st.divider()
    st.header("顯示選項（共用）")
    only_geo = st.checkbox("只顯示有經緯度的點位", True)
    show_only_exceed = st.checkbox("只顯示超標點位（PM2.5 > 35.4）", False)
    radius_by_pm = st.checkbox("點位半徑隨 PM2.5 變化", True)
    top_n = st.slider("Top N（PM2.5）", 10, 200, 50, 10)


# =========================
# 取資料 + 抓取時間
# =========================
fetch_time = now_tw()
fetch_time_str = fetch_time.strftime("%Y-%m-%d %H:%M:%S")

try:
    if btn_refresh:
        st.cache_data.clear()
    used_url, records = fetch_records_smart(url, api_key)
    save_cache(records, used_url, fetch_time_str)
except Exception as e:
    st.error(f"抓取資料失敗：{e}")
    st.stop()

df_raw = build_df(records)
df = latest_per_device(df_raw)

if df.empty:
    st.warning("資料為空（欄位格式不符或回傳空集合）。")
    st.stop()

if only_geo and ("Lat" in df.columns) and ("Lon" in df.columns):
    df = df[df["Lat"].notna() & df["Lon"].notna()]

if show_only_exceed and "PM25" in df.columns:
    df = df[df["PM25"].notna() & (df["PM25"] > 35.4)]

dist_tbl = district_table(df)


def render_map(df_map: pd.DataFrame, fit_zoom: bool = False):
    if "Lat" not in df_map.columns or "Lon" not in df_map.columns:
        st.warning("資料缺少經緯度，無法顯示地圖。")
        return
    if df_map[["Lat", "Lon"]].dropna().shape[0] == 0:
        st.warning("目前沒有可用的經緯度點位可畫地圖。")
        return

    m = df_map.copy()
    if "Town" not in m.columns:
        m["Town"] = ""
    if "Landmark" not in m.columns:
        m["Landmark"] = ""
    if "PM25" not in m.columns:
        m["PM25"] = None
    if "Temp" not in m.columns:
        m["Temp"] = None
    if "Hum" not in m.columns:
        m["Hum"] = None

    m["level"] = m["PM25"].apply(pm25_level)
    m["advice"] = m["level"].apply(pm25_advice)
    m["sensitive"] = m["level"].apply(sensitive_note)

    m["color"] = m["PM25"].apply(pm25_color)
    m["radius"] = m["PM25"].apply(lambda x: pm25_radius(x)) if radius_by_pm else 80

    if fit_zoom:
        view_state = view_state_for_points(m, default_zoom=10)
    else:
        view_state = pdk.ViewState(
            latitude=float(m["Lat"].median()),
            longitude=float(m["Lon"].median()),
            zoom=10,
            pitch=0,
        )

    layer = pdk.Layer(
        "ScatterplotLayer",
        data=m,
        get_position=["Lon", "Lat"],
        get_radius="radius",
        get_fill_color="color",
        pickable=True,
        auto_highlight=True,
    )

    tooltip = {
        "text": (
            "{Town}｜{Landmark}\n"
            "PM2.5：{PM25} μg/m³（{level}）\n"
            "溫度：{Temp} °C｜濕度：{Hum} %\n"
            "建議：{advice}\n"
            "敏感族群：{sensitive}\n"
            "— 分級門檻 —\n"
            "≤15.4 良好｜15.5–35.4 普通｜35.5–54.4 敏感族群｜≥54.5 不健康"
        )
    }

    deck = pdk.Deck(
        map_style=None,
        initial_view_state=view_state,
        layers=[layer],
        tooltip=tooltip,
    )
    st.pydeck_chart(deck, use_container_width=True)


# =========================
# 版面：一般民眾 vs 專業人員
# =========================
if mode == "一般民眾（快速理解）":
    st.success("使用方式：先看「快速判讀」抓重點 → 再看地圖定位；可用下拉選擇行政區聚焦查看；若只想看需注意地點，勾選左側「只顯示超標點位」。")

    summary = citizen_summary(df, fetch_time_str)

    st.subheader("快速判讀")
    st.info(summary["headline"])

    left, right = st.columns([1.1, 0.9])
    with left:
        st.markdown("### 你需要留意什麼？")
        st.markdown(summary["district"])
    with right:
        st.markdown("### 看圖小抄")
        st.markdown(summary["howto"])

    st.divider()

    st.subheader("選擇行政區（聚焦查看）")
    fit_zoom = False

    if "Town" in df.columns:
        town_list = sorted([t for t in df["Town"].dropna().astype(str).unique() if t.strip() != ""])
        options = ["全市"] + town_list
        selected = st.selectbox("行政區", options, index=0)

        if selected != "全市":
            df_focus = df[df["Town"].astype(str) == selected].copy()
            if df_focus.empty:
                st.warning(f"{selected} 目前沒有可用點位資料。")
                df_focus = df.copy()
            else:
                st.info(district_stats_line(df_focus, selected))
                fit_zoom = True
        else:
            df_focus = df.copy()
            st.caption("目前顯示：全市點位")
    else:
        df_focus = df.copy()
        st.warning("資料未提供行政區（Town）欄位，暫無法使用下拉聚焦。")

    st.divider()

    st.subheader("地圖（依 PM2.5 分級上色）")
    render_map(df_focus, fit_zoom=fit_zoom)
    st.caption("提示：滑鼠移到點位上，可直接看到 PM2.5、分級、溫濕度、建議、敏感族群提醒與分級門檻。")

    st.divider()
    with st.expander("行政區摘要（平均 / 最大 / 中位數 PM2.5）", expanded=False):
        if dist_tbl.empty:
            st.info("目前資料缺少行政區（Town）或 PM2.5 欄位，暫無法產生行政區摘要。")
        else:
            st.dataframe(dist_tbl, use_container_width=True)

    st.divider()
    with st.expander("完整資料表（進階：可排序、可查詢）", expanded=False):
        st.dataframe(df_focus, use_container_width=True, hide_index=True)
        st.caption(f"系統抓取時間：{fetch_time_str}｜資料落地：{CACHE_FILE}")

    with st.expander("技術資訊（可選）", expanded=False):
        st.write("✅ 本次實際使用的 API：")
        st.code(used_url)
        st.write("✅ 資料落地：")
        st.code(CACHE_FILE)
        st.caption("註：本資料集未提供觀測時間戳；本頁以『系統抓取時間』作為更新基準顯示。")

else:
    st.subheader("地圖（點位分佈：依 PM2.5 分級上色）")
    render_map(df, fit_zoom=False)
    st.caption("提示：滑鼠移到點位上，可直接看到 PM2.5、分級、溫濕度、建議、敏感族群提醒與分級門檻。")
