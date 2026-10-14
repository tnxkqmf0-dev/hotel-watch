#!/usr/bin/env python3
"""후쿠오카 숙소 공실 감시 → 텔레그램 알림.

楽天トラベル 空室検索API(VacantHotelSearch)로 지정 호텔의 빈 객실을 조회하고,
config.json 조건(트윈 2베드, 20㎡ 이상, 최소 객실 제외)에 맞는 방이
'새로' 나타나면 텔레그램으로 알린다. 표준 라이브러리만 사용.

환경변수
  RAKUTEN_APP_ID, RAKUTEN_ACCESS_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  MODE = "check"(기본) | "test"(텔레그램 테스트 + 현재 결과 요약 전송)
"""
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")
STATE_PATH = os.path.join(ROOT, "state.json")
API_URL = "https://openapi.rakuten.co.jp/engine/api/Travel/VacantHotelSearch/20170426"
KST = dt.timezone(dt.timedelta(hours=9))
AREA_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:㎡|m²|m2|平米|平方メートル)")


# ---------------------------------------------------------------- utils
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def now_kst():
    return dt.datetime.now(KST)


def telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("[telegram 미설정] " + text)
        return
    # 텔레그램 메시지 한도(4096자) 대비 분할
    chunks = [text[i:i + 3800] for i in range(0, len(text), 3800)] or [""]
    for chunk in chunks:
        body = urllib.parse.urlencode({
            "chat_id": chat,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=body)
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()


# ---------------------------------------------------------------- API
def fetch_vacancies(hotel_nos, checkin, checkout, adults):
    """조건에 맞는 모든 (호텔, 객실, 플랜) 레코드를 반환. 공실 0이면 []."""
    params = {
        "applicationId": os.environ["RAKUTEN_APP_ID"],
        "accessKey": os.environ["RAKUTEN_ACCESS_KEY"],
        "format": "json",
        "hotelNo": ",".join(str(n) for n in hotel_nos),
        "checkinDate": checkin,
        "checkoutDate": checkout,
        "adultNum": adults,
        "roomNum": 1,
        "searchPattern": 1,  # 플랜·객실 단위
        "hits": 30,
        "responseType": "middle",
    }
    records, page = [], 1
    while True:
        params["page"] = page
        url = API_URL + "?" + urllib.parse.urlencode(params)
        # 楽天 앱의 '허용 웹사이트' 검증용 (Actions IP는 매번 바뀌어서 IP 허용 방식은 불가)
        origin = os.environ.get("RAKUTEN_ORIGIN") or "https://github.com"
        req = urllib.request.Request(url, headers={
            "Origin": origin, "Referer": origin.rstrip("/") + "/",
            "User-Agent": "hotel-watch/1.0"})
        data = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.load(r)
                break
            except urllib.error.HTTPError as e:
                payload = e.read().decode("utf-8", "replace")
                if e.code == 404:  # not_found = 공실 없음
                    return records
                if e.code in (429, 500, 503) and attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise RuntimeError(f"Rakuten API {e.code}: {payload[:300]}")
        records.extend(parse_response(data))
        info = data.get("pagingInfo", {})
        if page >= int(info.get("pageCount", 1) or 1):
            return records
        page += 1
        time.sleep(1.2)


def _items(obj, key):
    """{key: {...}} 또는 {key: [...]} 양쪽 포맷 대응."""
    v = obj.get(key)
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def parse_response(data):
    out = []
    for h in data.get("hotels", []):
        parts = h.get("hotel", h) if isinstance(h, dict) else h
        if isinstance(parts, dict):
            parts = [parts]
        basic, rooms = {}, []
        for part in parts:
            if "hotelBasicInfo" in part:
                basic = part["hotelBasicInfo"]
            for ri in _items(part, "roomInfo"):
                rooms.append(ri)
        # roomInfo 는 [{roomBasicInfo}, {dailyCharge}] 쌍 또는 하나의 dict
        merged, cur = [], None
        for ri in rooms:
            if isinstance(ri, list):
                d = {}
                for x in ri:
                    d.update(x)
                merged.append(d)
                continue
            if "roomBasicInfo" in ri:
                cur = {"roomBasicInfo": ri["roomBasicInfo"]}
                if "dailyCharge" in ri:
                    cur["dailyCharge"] = ri["dailyCharge"]
                merged.append(cur)
            elif "dailyCharge" in ri and cur is not None:
                cur["dailyCharge"] = ri["dailyCharge"]
        for m in merged:
            rb = m.get("roomBasicInfo", {})
            dc = m.get("dailyCharge", {})
            out.append({
                "hotelNo": int(basic.get("hotelNo", 0) or 0),
                "hotelName": basic.get("hotelName", ""),
                "planListUrl": basic.get("planListUrl", ""),
                "roomName": rb.get("roomName", ""),
                "roomClass": rb.get("roomClass", ""),
                "planName": rb.get("planName", ""),
                "planId": rb.get("planId", ""),
                "reserveUrl": rb.get("reserveUrl", ""),
                "withBreakfast": rb.get("withBreakfastFlag"),
                "total": dc.get("total"),
            })
    return out


# ---------------------------------------------------------------- filter
def qualifies(rec, hotel_cfg, cfg):
    name = rec["roomName"]
    for kw in cfg["exclude_keywords"]:
        if kw in name:
            return None
    for room in hotel_cfg["rooms"]:
        if re.search(room["match"], name):
            area = room.get("area")
            m = AREA_RE.search(name)
            if m:
                area = float(m.group(1))
            if area is not None and area < cfg["min_area_m2"]:
                return None
            limit = cfg.get("max_total_per_night_yen")
            if limit and rec["total"] and rec["total"] > limit:
                return None
            return area
    return None


def stay_ranges(cfg):
    ci = dt.date.fromisoformat(cfg["checkin"])
    co = dt.date.fromisoformat(cfg["checkout"])
    ranges = [(ci, co, "전체")]
    if cfg.get("check_partial_nights") and (co - ci).days > 1:
        d = ci
        while d < co:
            ranges.append((d, d + dt.timedelta(days=1), "1박"))
            d += dt.timedelta(days=1)
    return ranges


def collect(cfg):
    hotels = {h["no"]: h for h in cfg["hotels"]}
    found = {}  # key -> info
    for ci, co, label in stay_ranges(cfg):
        recs = fetch_vacancies(list(hotels), ci.isoformat(), co.isoformat(), cfg["adults"])
        for rec in recs:
            h = hotels.get(rec["hotelNo"])
            if not h:
                continue
            area = qualifies(rec, h, cfg)
            if area is None:
                continue
            rng = f"{ci:%m/%d}~{co:%m/%d}"
            key = f"{rec['hotelNo']}|{rng}|{rec['roomName']}"
            prev = found.get(key)
            # 같은 객실은 가장 싼 플랜 하나만 남김
            if prev is None or (rec["total"] or 9e9) < (prev["total"] or 9e9):
                found[key] = {**rec, "area": area, "range": rng, "label": label,
                              "displayName": h["name"], "planCount": (prev or {}).get("planCount", 0) + 1}
            else:
                prev["planCount"] += 1
        time.sleep(1.2)
    return found


# ---------------------------------------------------------------- message
def fmt_item(v):
    yen = f"¥{v['total']:,}" if isinstance(v.get("total"), int) else "가격확인"
    link = v.get("reserveUrl") or v.get("planListUrl")
    bf = " · 조식포함" if v.get("withBreakfast") == 1 else ""
    more = f" (외 플랜 {v['planCount'] - 1}개)" if v.get("planCount", 1) > 1 else ""
    return (f"🏨 <b>{html.escape(v['displayName'])}</b>\n"
            f"📅 {v['range']} ({v['label']})\n"
            f"🛏 {html.escape(v['roomName'])} · {v['area']}㎡\n"
            f"💴 1박째 {yen}{bf}{more}\n"
            f"📝 {html.escape(v['planName'][:60])}\n"
            f"👉 <a href=\"{html.escape(link)}\">예약하러 가기</a>")


def main():
    cfg = load_json(CONFIG_PATH, None)
    state = load_json(STATE_PATH, {"available": [], "last_heartbeat": "", "last_error": ""})
    mode = os.environ.get("MODE", "check")
    now = now_kst()

    if now.date() > dt.date.fromisoformat(cfg["stop_after"]) and mode != "test":
        print("감시 기간 종료 → 아무것도 하지 않음")
        return 0

    try:
        found = collect(cfg)
    except Exception as e:  # API 장애 등은 하루 한 번만 알림
        print(f"ERROR: {e}", file=sys.stderr)
        today = now.date().isoformat()
        if state.get("last_error") != today or mode == "test":
            telegram(f"⚠️ 숙소 감시 오류\n<code>{html.escape(str(e))[:500]}</code>")
            state["last_error"] = today
            save_json(STATE_PATH, state)
        return 1

    prev = set(state.get("available", []))
    cur = set(found)
    new = sorted(cur - prev)

    if mode == "test":
        lines = [f"✅ 테스트 성공! 현재 조건 맞는 공실 {len(cur)}건"]
        lines += [fmt_item(found[k]) for k in sorted(cur)[:10]]
        telegram("\n\n".join(lines))
    elif new:
        head = f"🚨 <b>빈방 떴다! ({len(new)}건)</b>  {now:%m/%d %H:%M}"
        telegram("\n\n".join([head] + [fmt_item(found[k]) for k in new]))

    # 하루 한 번 생존 신고
    hb_hour = cfg.get("heartbeat_hour_kst")
    today = now.date().isoformat()
    if mode != "test" and hb_hour is not None and now.hour >= hb_hour and state.get("last_heartbeat") != today:
        full = sum(1 for k in cur if found[k]["label"] == "전체")
        telegram(f"🫡 감시 중 ({cfg['checkin'][5:]}~{cfg['checkout'][5:]})\n"
                 f"현재 조건 맞는 공실: 전체 일정 {full}건 / 1박 단위 {len(cur) - full}건\n"
                 f"{cfg['stop_after'][5:]}까지 계속 확인할게요.")
        state["last_heartbeat"] = today

    state["available"] = sorted(cur)
    save_json(STATE_PATH, state)  # 내용이 바뀐 경우에만 워크플로가 커밋함
    print(f"공실 {len(cur)}건, 신규 {len(new)}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
