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

import ko

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


def summary(text):
    """GitHub 실행 페이지 상단(Summary)에 표시 → 폰에서도 로그 안 뒤지고 바로 보임."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    print(text)
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n\n")


def _tg_send(token, chat, text, html_mode):
    params = {"chat_id": chat, "text": text, "disable_web_page_preview": "true"}
    if html_mode:
        params["parse_mode"] = "HTML"
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                 data=urllib.parse.urlencode(params).encode())
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()


def telegram(text):
    """실패해도 감시 자체는 죽지 않게 하고, 원인은 Summary에 남긴다."""
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat:
        summary("⚠️ 텔레그램 Secret 미설정 (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
        return False
    chunks = [text[i:i + 3800] for i in range(0, len(text), 3800)] or [""]
    for chunk in chunks:
        try:
            _tg_send(token, chat, chunk, True)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code == 400 and "parse" in body.lower():  # HTML 깨짐 → 일반 텍스트로 재전송
                try:
                    _tg_send(token, chat, re.sub(r"<[^>]+>", "", html.unescape(chunk)), False)
                    continue
                except urllib.error.HTTPError as e2:
                    e, body = e2, e2.read().decode("utf-8", "replace")
            tip = {401: "봇 토큰이 틀림", 400: "chat ID가 틀렸거나 봇에게 먼저 말을 안 걸었음",
                   403: "봇이 차단됐거나 chat ID가 다른 사람/그룹", 404: "봇 토큰 형식이 틀림"}.get(e.code, "")
            summary(f"⚠️ 텔레그램 전송 실패 {e.code} {tip}\n\n`{body[:300]}`\n"
                    f"(chat ID 길이 {len(chat)}자, 토큰에 ':' 포함={':' in token})")
            return False
    return True


# ---------------------------------------------------------------- API
_HV_STATE = {"idx": 0}


def header_variants():
    """楽天 '허용 웹사이트' 검증용 헤더 조합 후보.
    Actions IP는 매번 바뀌어 IP 허용은 불가 → 등록한 사이트를 Referer/Origin으로 보냄.
    楽天이 어느 형식으로 비교하는지 문서가 불명확해 여러 조합을 순서대로 시도한다."""
    site = (os.environ.get("RAKUTEN_ORIGIN") or "https://github.com").strip()
    if "://" not in site:  # "github.com" 처럼 도메인만 넣은 경우
        site = "https://" + site
    u = urllib.parse.urlsplit(site)
    origin = f"{u.scheme}://{u.netloc}"
    toggled = site[:-1] if site.endswith("/") else site + "/"
    cands = [
        {"Referer": site},
        {"Referer": toggled},
        {"Referer": origin + "/"},
        {"Origin": origin, "Referer": site},
        {"Origin": origin},
    ]
    out, seen = [], set()
    for c in cands:
        key = tuple(sorted(c.items()))
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def fetch_vacancies(target, checkin, checkout, adults, pattern=1):
    """조건에 맞는 모든 (호텔, 객실, 플랜) 레코드를 반환. 공실 0이면 [].
    target: 호텔번호 리스트, 또는 위치검색 파라미터 dict(latitude/longitude/searchRadius 등).
    pattern: 1=플랜·객실 단위, 0=호텔 단위(호텔 목록 파악용)."""
    params = {
        "applicationId": os.environ["RAKUTEN_APP_ID"].strip(),
        "accessKey": os.environ["RAKUTEN_ACCESS_KEY"].strip(),
        "format": "json",
        "checkinDate": checkin,
        "checkoutDate": checkout,
        "adultNum": adults,
        "roomNum": 1,
        "searchPattern": pattern,
        "datumType": 1,  # 위경도를 일반 도(WGS84) 단위로
        "hits": 30,
        "responseType": "middle",
    }
    if isinstance(target, dict):
        params.update(target)
    else:
        params["hotelNo"] = ",".join(str(n) for n in target)
    records, page = [], 1
    while True:
        params["page"] = page
        url = API_URL + "?" + urllib.parse.urlencode(params)
        data = None
        attempt = 0
        variants = header_variants()
        while True:
            hv = variants[_HV_STATE["idx"]]
            req = urllib.request.Request(url, headers={**hv, "User-Agent": "hotel-watch/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.load(r)
                break
            except urllib.error.HTTPError as e:
                payload = e.read().decode("utf-8", "replace")
                if e.code == 404:  # not_found = 공실 없음
                    return records
                if e.code == 403 and "REFERRER" in payload and _HV_STATE["idx"] + 1 < len(variants):
                    _HV_STATE["idx"] += 1  # 다른 헤더 조합으로 재시도
                    time.sleep(1.2)
                    continue
                if e.code in (429, 500, 503) and attempt < 2:
                    attempt += 1
                    time.sleep(5 * attempt)
                    continue
                hint = ""
                if e.code == 403:
                    tried = " | ".join(" ".join(f"{k}={v}" for k, v in x.items()) for x in variants)
                    hint = (f" (accessKey {len(params['accessKey'])}자, "
                            f"RAKUTEN_ORIGIN={os.environ.get('RAKUTEN_ORIGIN') or '(미설정)'}, 시도: {tried})")
                raise RuntimeError(f"Rakuten API {e.code}{hint}: {payload[:300]}")
        records.extend(parse_response(data))
        info = data.get("pagingInfo", {})
        if page >= min(int(info.get("pageCount", 1) or 1), 100):
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
                "hotelName": basic.get("hotelName") or "",
                "planListUrl": basic.get("planListUrl") or "",
                "roomName": rb.get("roomName") or "",
                "roomClass": rb.get("roomClass") or "",
                "planName": rb.get("planName") or "",
                "planId": rb.get("planId", ""),
                "reserveUrl": rb.get("reserveUrl") or "",
                "withBreakfast": rb.get("withBreakfastFlag"),
                "total": dc.get("total"),
                "address": (basic.get("address1") or "") + (basic.get("address2") or ""),
                "station": basic.get("nearestStation") or "",
                "lat": basic.get("latitude"), "lng": basic.get("longitude"),
                "review": basic.get("reviewAverage"), "reviewCount": basic.get("reviewCount"),
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
            return area, room.get("label") or name
    return None


NONREF_RE = re.compile(r"キャンセル不可|返金不可|変更不可|払い戻し不可")


def plan_rank(rec):
    """취소 가능한 플랜 우선, 그다음 싼 순."""
    return (bool(NONREF_RE.search(rec.get("planName", ""))), rec.get("total") or 9e9)


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
            q = qualifies(rec, h, cfg)
            if q is None:
                continue
            area, room_label = q
            rng = f"{ci:%m/%d}~{co:%m/%d}"
            key = f"{rec['hotelNo']}|{rng}|{rec['roomName']}"
            prev = found.get(key)
            # 같은 객실은 플랜 하나만 남김 (취소 가능 > 저렴)
            if prev is None or plan_rank(rec) < plan_rank(prev):
                found[key] = {**rec, "area": area, "range": rng, "label": label,
                              "start": f"{ci:%m/%d}", "roomLabel": room_label,
                              "displayName": h["name"], "planCount": (prev or {}).get("planCount", 0) + 1}
            else:
                prev["planCount"] += 1
        time.sleep(1.2)
    return found


# ---------------------------------------------------------------- area (다다미방) mode
def _gsearch(q):
    return "https://www.google.com/search?q=" + urllib.parse.quote(q)


def tatami_match(rec, cfg):
    """다다미 객실이면 (면적 or None, 라벨) 반환, 아니면 None."""
    a = cfg["area"]
    room = rec.get("roomName", "")
    plan = rec.get("planName", "")
    # 객실명은 넓게, 플랜명은 오탐 방지로 좁게 ('和洋バイキング朝食' 같은 것 제외)
    if not (any(k in room for k in a["room_keywords"])
            or any(k in plan for k in a.get("plan_keywords", []))):
        return None
    if any(k in room for k in a.get("exclude_keywords", [])):
        return None
    m = AREA_RE.search(room)
    area = float(m.group(1)) if m else None
    if area is not None and area < cfg["min_area_m2"]:
        return None
    label = re.sub(r"(【[^】]*】|\[[^\]]*\]|＜[^＞]*＞|<[^>]*>|[■□][^■□]*[■□])", "", room).strip() or room
    label = AREA_RE.sub("", label).strip(" ・/") or label   # 면적은 따로 표시
    return area, label[:40]


def _km(a, lat, lng):
    import math
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    dy = (lat - a["lat"]) * 111.0
    dx = (lng - a["lng"]) * 111.0 * math.cos(math.radians(a["lat"]))
    return round(math.hypot(dx, dy), 1)


def remember_info(info, rec, a):
    """호텔 위치 정보(구·최寄역·중심거리) 저장."""
    if not rec.get("hotelNo") or not rec.get("address"):
        return
    m = re.search(r"([^\s市県]+区)", rec["address"])
    info[str(rec["hotelNo"])] = {"ward": m.group(1) if m else "", "station": rec.get("station", ""),
                                 "km": _km(a, rec.get("lat"), rec.get("lng")),
                                 "lat": rec.get("lat"), "lng": rec.get("lng")}


def _chunks(xs, n):
    return [xs[i:i + n] for i in range(0, len(xs), n)]


def good_hotel(rec, a):
    """3성급 이상 대용: 楽天 평점·리뷰 수 기준 + 호스텔/캡슐 등 제외 (일본은 공식 성급이 없음)."""
    name = rec.get("hotelName", "")
    if any(k in name for k in a.get("hotel_exclude_keywords", [])):
        return False
    try:
        review, cnt = float(rec.get("review") or 0), int(rec.get("reviewCount") or 0)
    except (TypeError, ValueError):
        return False
    return review >= a.get("min_review", 0) and cnt >= a.get("min_review_count", 0)


def discover(cfg, info=None):
    """반경 내 楽天 호텔을 훑어 다다미 객실이 있는 호텔 목록 {번호: 이름} 생성."""
    a = cfg["area"]
    geo = {"latitude": a["lat"], "longitude": a["lng"], "searchRadius": a["radius_km"], "datumType": 1}
    dates = [(cfg["checkin"], cfg["checkout"])] + [tuple(x) for x in a.get("probe_ranges", [])]
    hotels = {}
    for ci, co in dates:  # 1단계: 반경 내 호텔 번호 수집 (호텔 단위 검색이라 가벼움)
        for rec in fetch_vacancies(geo, ci, co, cfg["adults"], pattern=0):
            if rec["hotelNo"] and good_hotel(rec, a):
                hotels[rec["hotelNo"]] = rec["hotelName"]
                if info is not None:
                    remember_info(info, rec, a)
        time.sleep(1.2)
    catalog = {}
    for ci, co in dates:  # 2단계: 객실 단위로 다다미 객실 존재 여부 확인
        for batch in _chunks(sorted(hotels), 15):
            for rec in fetch_vacancies(batch, ci, co, cfg["adults"]):
                if tatami_match(rec, cfg):
                    catalog[str(rec["hotelNo"])] = rec["hotelName"]
            time.sleep(1.2)
    summary(f"🔎 탐색 완료: 반경 {a['radius_km']}km 평점 기준 통과 {len(hotels)}곳 중 조건 객실 보유 {len(catalog)}곳")
    return catalog


def collect_area(cfg, state, force_discover=False):
    a = cfg["area"]
    catalog = dict(state.get("tatami_catalog", {}))
    last = state.get("last_discover", "")
    every = dt.timedelta(hours=a.get("discover_every_hours", 24))
    stale = not last or now_kst() - dt.datetime.fromisoformat(last) >= every
    info = state.setdefault("hotel_info", {})
    # 반경·키워드 설정이 바뀌면 목록을 새로 만듦 (예전 범위의 호텔이 남지 않게)
    sig = json.dumps({k: a.get(k) for k in ("lat", "lng", "radius_km", "room_keywords",
                                            "plan_keywords", "exclude_keywords", "min_review",
                                            "min_review_count", "hotel_exclude_keywords")},
                     sort_keys=True, ensure_ascii=False)
    if state.get("area_sig") != sig:
        catalog, force_discover = {}, True
        state["area_sig"] = sig
    if force_discover or not catalog or stale or any("lat" not in info.get(n, {}) for n in catalog):
        catalog.update(discover(cfg, info))  # 한 번 찾은 호텔은 계속 유지(매진돼도 목록에 남김)
        state["tatami_catalog"] = catalog
        state["last_discover"] = now_kst().isoformat(timespec="minutes")
    found = {}
    nos = sorted(int(n) for n in catalog)
    for ci, co, label in stay_ranges(cfg):
        for batch in _chunks(nos, 15):
            for rec in fetch_vacancies(batch, ci.isoformat(), co.isoformat(), cfg["adults"]):
                remember_info(info, rec, a)
                q = tatami_match(rec, cfg)
                if not q:
                    continue
                area, room_label = q
                rng = f"{ci:%m/%d}~{co:%m/%d}"
                key = f"{rec['hotelNo']}|{rng}|{rec['roomName']}"
                prev = found.get(key)
                if prev is None or plan_rank(rec) < plan_rank(prev):
                    found[key] = {**rec, "area": area, "range": rng, "label": label,
                                  "start": f"{ci:%m/%d}", "roomLabel": room_label,
                                  "displayName": catalog.get(str(rec["hotelNo"]), rec["hotelName"]),
                                  "planCount": (prev or {}).get("planCount", 0) + 1}
                else:
                    prev["planCount"] += 1
            time.sleep(1.2)
    # 렌더링용 호텔 목록 (링크 포함)
    def where(n):
        i = info.get(n, {})
        near = ko.nearby(i.get("lat"), i.get("lng"), a.get("ref_station", "하카타역"))
        if near:
            return near
        st, area_desc = ko.station(i.get("station"))
        return " · ".join(x for x in [area_desc or ko.ward(i.get("ward")), st] if x)
    def ko_name(n, name):
        k, auto = ko.hotel_name(n, name)
        return f"{k} ({ko._tidy(name)[:20]})" if auto else k
    cfg["hotels"] = [{"no": int(n), "name": ko_name(n, name), "where": where(n),
                      "kr": _gsearch(f"라쿠텐 트래블 {name}"), "official": _gsearch(f"{name} 公式")}
                     for n, name in sorted(catalog.items(), key=lambda kv: (info.get(kv[0], {}).get("km") or 99))]
    return found


# ---------------------------------------------------------------- message
def _yen(v):
    return f"¥{v['total']:,}" if isinstance(v.get("total"), int) else "가격확인"


def _link(v, text):
    url = v.get("reserveUrl") or v.get("planListUrl") or ""
    return f'<a href="{html.escape(url)}">{html.escape(text)}</a>'


def _tags(v):
    t = []
    if v.get("withBreakfast") == 1:
        t.append("조식")
    if NONREF_RE.search(v.get("planName", "")):
        t.append("⚠️취소불가")
    return f" ({', '.join(t)})" if t else ""


def render_blocks(items, cfg, show_empty=False, new_keys=()):
    """호텔별 블록 리스트. 객실마다 3박 전체가 있으면 그것만, 없으면 1박 날짜들.
    show_empty=True 면 공실 없는 호텔도 '없음'으로 맨 아래 표시."""
    nights = (dt.date.fromisoformat(cfg["checkout"]) - dt.date.fromisoformat(cfg["checkin"])).days
    order = {h["no"]: i for i, h in enumerate(cfg["hotels"])}
    hcfg = {h["no"]: h for h in cfg["hotels"]}
    by_hotel = {}
    for v in items:
        rooms = by_hotel.setdefault(v["hotelNo"], {})
        rooms.setdefault((v["roomLabel"], v["area"]), []).append(v)
    blocks = []
    for no in sorted(by_hotel, key=lambda n: order.get(n, 99)):
        rooms = by_hotel[no]
        name = hcfg.get(no, {}).get("name") or next(iter(rooms.values()))[0]["displayName"]
        lines = [f"🏨 <b>{html.escape(name)}</b>"]
        h = hcfg.get(no, {})
        if h.get("where"):
            lines.append(f"   📍 {html.escape(h['where'])}")
        alt = []
        if h.get("kr"):
            alt.append(f'<a href="{html.escape(h["kr"])}">🇰🇷楽天 한국어</a>')
        if h.get("official"):
            alt.append(f'<a href="{html.escape(h["official"])}">🏨공식</a>')
        if alt:
            lines.append("   " + " · ".join(alt))
        for (label, area), vs in sorted(rooms.items(), key=lambda kv: kv[0][1] or 0):
            full = [v for v in vs if v["label"] == "전체"]
            size = f" {area:g}㎡" if area else ""
            badge = " 🆕" if any(v.get("key") in new_keys for v in vs) else ""
            lines.append(f"• {html.escape(ko.room_name(label))}{size}{badge}")
            if full:
                v = min(full, key=plan_rank)
                lines.append(f"   ✅ {nights}박 전체 {_yen(v)}~/박{_tags(v)} → {_link(v, '楽天JP')}")
            else:
                parts = [f"{_link(v, v['start'])} {_yen(v)}{_tags(v)}"
                         for v in sorted(vs, key=lambda x: x["start"])]
                lines.append("   🌙 1박만: " + " · ".join(parts))
        blocks.append("\n".join(lines))
    if show_empty:
        empty = [h["name"] + (f" — {h['where']}" if h.get("where") else "")
                 for h in cfg["hotels"] if h["no"] not in by_hotel]
        if empty:
            if len(empty) > 30:  # 많으면 한 줄로 압축
                blocks.append(f"❌ <b>공실 없음 ({len(empty)}곳)</b>\n" + html.escape(" · ".join(empty)))
            else:
                blocks.append("❌ <b>조건 맞는 공실 없음</b>\n" + "\n".join(f"· {html.escape(n)}" for n in empty))
        if cfg.get("not_on_rakuten"):
            blocks.append("ℹ️ 楽天에 없어 감시 불가: " + html.escape(", ".join(cfg["not_on_rakuten"])))
    return blocks


def send_blocks(head, blocks, limit=3800):
    """블록 경계에서만 잘라 보냄 (링크 태그가 중간에 끊기지 않게)."""
    msg = head
    for b in blocks:
        if len(msg) + len(b) + 2 > limit:
            telegram(msg)
            msg = b
        else:
            msg += "\n\n" + b
    telegram(msg)


def main():
    cfg = load_json(CONFIG_PATH, None)
    state = load_json(STATE_PATH, {"available": [], "last_heartbeat": "", "last_error": ""})
    mode = os.environ.get("MODE", "check")
    now = now_kst()

    if now.date() > dt.date.fromisoformat(cfg["stop_after"]) and mode != "test":
        print("감시 기간 종료 → 아무것도 하지 않음")
        return 0

    q = cfg.get("quiet_hours_kst")  # 예: [22, 6] → 22시~다음날 6시 쉼
    if q and mode not in ("test", "discover") and (now.hour >= q[0] or now.hour < q[1]):
        print(f"조용한 시간({q[0]}시~{q[1]}시) → 건너뜀")
        return 0

    if mode in ("test", "discover"):  # 楽天과 무관하게 텔레그램 연결부터 따로 확인
        ok = telegram("🔔 텔레그램 연결 OK — 이어서 楽天 API를 확인할게요.")
        summary("✅ 텔레그램 연결 OK" if ok else "❌ 텔레그램 연결 실패 (위 메시지 참고)")

    try:
        if cfg.get("area"):
            found = collect_area(cfg, state, force_discover=(mode == "discover"))
        else:
            found = collect(cfg)
    except Exception as e:  # API 장애 등은 하루 한 번만 알림
        import traceback
        tb = traceback.extract_tb(e.__traceback__)[-1]
        e = f"{type(e).__name__}: {e} (check.py {tb.lineno}행 {tb.name})"
        summary(f"❌ 楽天 API 오류\n\n```\n{e}\n```")
        today = now.date().isoformat()
        if state.get("last_error") != today or mode == "test":
            telegram(f"⚠️ 숙소 감시 오류\n<code>{html.escape(str(e)[:1500])}</code>")
            state["last_error"] = today
            save_json(STATE_PATH, state)
        return 1

    prev = set(state.get("available", []))
    cur = set(found)
    new = sorted(cur - prev)

    full_hotels = sorted({found[k]["displayName"] for k in cur if found[k]["label"] == "전체"})
    period = f"{cfg['checkin'][5:].replace('-', '/')}~{cfg['checkout'][5:].replace('-', '/')}"

    for k, v in found.items():
        v["key"] = k
    n_hotels = len({found[k]["hotelNo"] for k in cur})
    kind = (cfg["area"].get("title", "") + " ") if cfg.get("area") else ""
    summary_line = f"전체 일정 가능 {len(full_hotels)}곳 / 조건 맞는 호텔 {n_hotels}곳 (감시 {len(cfg['hotels'])}곳)"
    if new and mode not in ("test", "discover"):
        head = f"🚨 <b>{kind}빈방 떴다!</b> · {period} · {now:%m/%d %H:%M} 기준\n{summary_line}\n🆕 = 새로 풀린 방"
    else:
        head = f"📋 <b>{kind}공실 현황</b> · {period} · {now:%m/%d %H:%M} 기준\n{summary_line}"
    send_every = cfg.get("send_status_every_run", True)
    if mode in ("test", "discover") or new or send_every:
        send_blocks(head, render_blocks([found[k] for k in cur], cfg, show_empty=True, new_keys=set(new)))

    state["available"] = sorted(cur)
    save_json(STATE_PATH, state)  # 내용이 바뀐 경우에만 워크플로가 커밋함
    summary(f"✅ 楽天 API OK — 공실 {len(cur)}건, 신규 {len(new)}건 / 통과한 헤더: {header_variants()[_HV_STATE['idx']]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
