"""
이세계아이돌 멜론 누적 스트리밍 합계 계산기

하는 일
  1. data/input.txt 에 적힌 곡별 누적 스트리밍 수를 읽는다.
  2. 실수 막기: 이전 기록보다 숫자가 줄어든 곡이 있으면 멈추고 알려 준다.
  3. 그룹 이름(예: 이세계아이돌, 고세구)별 합계를 내고 data/history.json 에 날짜별로 기록한다.
     (같은 날 여러 번 고치면 마지막 숫자로 덮어씀)
  4. 사이트가 읽는 docs/data.json, 엑셀용 docs/history.csv 를 만든다.
  5. 디스코드 웹후크 주소(DISCORD_WEBHOOK_URL)가 있으면 요약 알림을 보낸다.

  --mode record  : 기록 저장 + 사이트 갱신 + 디스코드 알림 (GitHub Actions 가 사용)
  --mode preview : 기록은 저장하지 않고 사이트 파일만 만들어 봄 (내 컴퓨터 테스트용)

외부 라이브러리 없이 파이썬 기본 기능만 사용한다.
"""

import argparse
import csv
import json
import os
import sys
import unicodedata
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
INPUT_FILE = ROOT / "data" / "input.txt"
HISTORY_FILE = ROOT / "data" / "history.json"
SONGS_FILE = ROOT / "data" / "songs.json"   # 곡별 앨범·발매일·멜론 링크 (없어도 됨)
GOALS_FILE = ROOT / "data" / "goals.json"   # 목표치 (없어도 됨)
SITE_DATA_FILE = ROOT / "docs" / "data.json"
CSV_FILE = ROOT / "docs" / "history.csv"

PACE_DAYS = 7               # 예상 날짜 계산에 쓰는 최근 기간(일)
GROUP_STEP = 10_000_000     # 그룹 합계는 1천만 회마다 '돌파' 축하
JUMP_WARN = 0.05            # 하루에 5% 넘게 늘면 경고(멈추지는 않음)


# ───────────────────────── 작은 도구들 ─────────────────────────

def clean_number(text):
    """'12,345,678회', '１２３４', ' 1 234 ' 같은 입력을 정수로 바꾼다. 비어 있으면 None."""
    text = unicodedata.normalize("NFKC", text)  # 전각 숫자 → 반각
    for junk in (",", " ", " ", "회", "번", "+"):
        text = text.replace(junk, "")
    if text == "":
        return None
    if not text.isdigit():
        raise ValueError(text)
    return int(text)


def korean(n):
    """635790026 → '6억 3,579만' (만 단위 아래는 버림)"""
    eok, man = divmod(int(n) // 10_000, 10_000)
    if eok and man:
        return f"{eok}억 {man:,}만"
    if eok:
        return f"{eok}억"
    if man:
        return f"{man:,}만"
    return f"{int(n):,}"


def milestone_label(n):
    """80000000 → '8천만', 640000000 → '6억 4천만', 200000 → '20만'"""
    eok, rest = divmod(int(n), 100_000_000)
    man = rest // 10_000
    if man and man % 1000 == 0:
        man_txt = f"{man // 1000}천만"
    elif man:
        man_txt = f"{man:,}만"
    else:
        man_txt = ""
    return " ".join(x for x in (f"{eok}억" if eok else "", man_txt) if x) or f"{n:,}"


def song_step(n):
    """곡의 다음 돌파 단위: 13만→10만 단위, 706만→100만 단위, 7,948만→1천만 단위, 1억 이상→1억 단위"""
    digits = len(str(max(int(n), 1)))
    return min(10 ** (digits - 1), 100_000_000) if digits > 1 else 10


def next_milestone(n, step=None):
    step = step or song_step(n)
    return (n // step + 1) * step


def crossed(prev, cur, step=None):
    """prev → cur 사이에 넘은 가장 큰 단위 (없으면 None)"""
    if prev is None or cur <= prev:
        return None
    step = step or song_step(prev)
    m = (cur // step) * step
    return m if m > prev else None


def is_inst(title):
    return "(inst" in title.lower()


def d(s):
    return date.fromisoformat(s[:10])


def value_at(points, day):
    """points=[(날짜문자열, 값)...] 에서 day 당일 또는 그 이전 마지막 값"""
    v = None
    for ds, val in points:
        if d(ds) <= day:
            v = val
        else:
            break
    return v


def pace(points):
    """최근 PACE_DAYS 일 동안의 하루 평균 증가량과 실제로 쓴 기간(일)"""
    if len(points) < 2:
        return None, None
    last_d, last_v = d(points[-1][0]), points[-1][1]
    window = [p for p in points[:-1] if (last_d - d(p[0])).days <= PACE_DAYS]
    first = window[0] if window else points[-2]
    days = (last_d - d(first[0])).days
    gained = last_v - first[1]
    if days <= 0 or gained <= 0:
        return None, None
    return gained / days, days


def eta(remaining, avg, from_day):
    if not avg or avg < 1:
        return None, None
    days = -(-remaining // int(avg))  # 올림
    return days, (from_day + timedelta(days=days)).isoformat()


def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


# ───────────────────────── 입력 읽기 ─────────────────────────

def read_input():
    """input.txt → [(그룹, 곡 제목, 숫자 또는 None), ...] (적힌 순서 유지)"""
    if not INPUT_FILE.exists():
        sys.exit(f"❌ 입력 파일이 없어요: {INPUT_FILE.relative_to(ROOT)}")

    rows, errors, seen = [], [], set()
    for no, raw in enumerate(INPUT_FILE.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) == 2:  # 숫자 칸의 | 를 빼먹은 경우도 허용
            parts.append("")
        if len(parts) != 3 or not parts[0] or not parts[1]:
            errors.append(f"  {no}번째 줄: '그룹 | 곡 제목 | 숫자' 모양이 아니에요 → {raw}")
            continue
        group, title, num_text = parts
        try:
            count = clean_number(num_text)
        except ValueError:
            errors.append(f"  {no}번째 줄: 숫자를 읽을 수 없어요 → '{num_text}'")
            continue
        if (group, title) in seen:
            errors.append(f"  {no}번째 줄: 같은 곡이 두 번 적혀 있어요 → {group} | {title}")
            continue
        seen.add((group, title))
        rows.append((group, title, count))

    if errors:
        print("❌ input.txt 에 고칠 곳이 있어요:")
        print("\n".join(errors))
        sys.exit(1)
    if not rows:
        sys.exit("❌ input.txt 에 곡이 하나도 없어요.")
    return rows


def check_mistakes(rows, previous, allow_decrease):
    """실수 막기: 이전 기록보다 줄어든 곡은 멈추고, 너무 많이 늘어난 곡은 경고만."""
    if not previous:
        return
    prev = {(s["group"], s["title"]): s["count"] for s in previous["songs"]}
    down, jump = [], []
    for group, title, count in rows:
        p = prev.get((group, title))
        if count is None or p is None:
            continue
        if count < p:
            down.append(f"  · {group} | {title}: {p:,} → {count:,} ({count - p:,})")
        elif p and (count - p) / p > JUMP_WARN:
            jump.append(f"  · {group} | {title}: {p:,} → {count:,} (+{(count - p) / p:.1%})")
    if jump:
        print(f"⚠️  이전 기록({previous['date']})보다 많이 늘어난 곡이 있어요. 오타가 아닌지 확인해 주세요:")
        print("\n".join(jump))
    if down:
        print(f"❌ 이전 기록({previous['date']})보다 숫자가 줄어든 곡이 있어요. 오타일 가능성이 높아요:")
        print("\n".join(down))
        if allow_decrease:
            print("   → '줄어든 숫자도 그대로 기록' 이 켜져 있어서 그대로 기록할게요.")
        else:
            print("   → input.txt 를 고쳐서 다시 저장해 주세요.")
            print("     정말 줄어든 게 맞다면 Actions → 누적 합계 갱신 → Run workflow 에서")
            print("     '줄어든 숫자도 그대로 기록' 을 체크하고 실행하면 돼요.")
            sys.exit(1)


def build_snapshot(rows, previous, now, input_at):
    """입력값으로 한 번의 기록을 만든다. 빈칸은 직전 기록 숫자를 이어 쓴다."""
    prev_songs = {(s["group"], s["title"]): s["count"] for s in (previous or {}).get("songs", [])}
    songs, group_order, totals = [], [], {}
    for group, title, count in rows:
        carried = False
        if count is None:
            count = prev_songs.get((group, title))
            if count is None:
                print(f"⚠️  '{group} | {title}' 숫자가 비어 있고 이전 기록도 없어서 이번 합계에서 뺐어요.")
                continue
            carried = True
        if group not in totals:
            totals[group] = 0
            group_order.append(group)
        totals[group] += count
        songs.append({"group": group, "title": title, "count": count, "carried": carried})
    return {
        "date": input_at.strftime("%Y-%m-%d"),
        "input_at": input_at.isoformat(timespec="seconds"),
        "taken_at": now.isoformat(timespec="seconds"),
        "group_order": group_order,
        "totals": totals,
        "songs": songs,
    }


# ───────────────────────── 사이트용 데이터 ─────────────────────────

def load_song_meta():
    """songs.json → {(그룹, 곡 제목): {album, released, melon}}"""
    data = load_json(SONGS_FILE, {})
    return {(g, s["title"]): s for g, songs in data.items() if not g.startswith("_") for s in songs}


def load_goals():
    data = load_json(GOALS_FILE, {})
    return {k: sorted(v, key=lambda g: g["target"]) for k, v in data.items() if not k.startswith("_")}


def compute_goal(steps, total, points):
    """아직 못 넘은 첫 목표까지 남은 횟수와, 최근 속도로 계산한 예상 달성일."""
    if not steps:
        return None
    passed = [g["label"] for g in steps if total >= g["target"]]
    upcoming = [g for g in steps if total < g["target"]]
    if not upcoming:
        return {"label": steps[-1]["label"], "target": steps[-1]["target"], "done": True, "passed": passed}
    goal = upcoming[0]
    remaining = goal["target"] - total
    avg, used = pace(points)
    days_left, eta_date = eta(remaining, avg, d(points[-1][0])) if points else (None, None)
    return {"label": goal["label"], "target": goal["target"], "done": False,
            "remaining": remaining, "progress": total / goal["target"], "passed": passed,
            "avg_daily": round(avg) if avg else None, "pace_days": used,
            "days_left": days_left, "eta_date": eta_date}


def period_summary(points):
    """최근 7일 vs 그 전 7일, 이번 달 vs 지난달 증가량 (데이터가 모자라면 None)"""
    if not points:
        return None
    L = d(points[-1][0])
    t = lambda day: value_at(points, day)
    cur = points[-1][1]
    w1, w2 = t(L - timedelta(days=7)), t(L - timedelta(days=14))
    this_m_start = L.replace(day=1)
    prev_m_end = this_m_start - timedelta(days=1)
    prev_m_start = prev_m_end.replace(day=1)
    pm, ppm = t(prev_m_end), t(prev_m_start - timedelta(days=1))
    return {
        "week": cur - w1 if w1 is not None else None,
        "prev_week": w1 - w2 if (w1 is not None and w2 is not None) else None,
        "month": cur - pm if pm is not None else None,
        "month_label": f"{L.month}월",
        "prev_month": pm - ppm if (pm is not None and ppm is not None) else None,
        "prev_month_label": f"{prev_m_end.month}월",
    }


FRIDAY = 4  # 월=0 … 금=4


def weekly_summary(points, song_points, song_titles):
    """금요일~금요일을 1주로 본 상승량.
    마지막 기록이 금요일이면 '지난 금 → 이번 금'(완료), 아니면 '지난 금 → 오늘'(진행 중)."""
    if len(points) < 2:
        return None
    L = d(points[-1][0])
    start = L - timedelta(days=7) if L.weekday() == FRIDAY else L - timedelta(days=(L.weekday() - FRIDAY) % 7)
    # 시작 금요일 당일(또는 그 전) 기록을 기준으로, 없으면 그 뒤 첫 기록부터
    base = next(((ds, v) for ds, v in reversed(points) if d(ds) <= start), None)
    partial = base is None
    if partial:
        base = next(((ds, v) for ds, v in points if d(ds) > start), None)
    if not base or d(base[0]) >= L:
        return None
    base_day = d(base[0])
    prev_base = value_at(points, start - timedelta(days=7))
    prev_gain = base[1] - prev_base if (not partial and prev_base is not None and d(base[0]) == start) else None
    songs = []
    for title in song_titles:
        pts = song_points.get(title, [])
        b = value_at(pts, base_day)
        if pts and b is not None:
            songs.append({"title": title, "gain": pts[-1][1] - b})
    songs.sort(key=lambda x: x["gain"], reverse=True)
    days = (L - base_day).days
    return {
        "start": start.isoformat(), "end": L.isoformat(), "base_date": base_day.isoformat(),
        "complete": L.weekday() == FRIDAY and not partial and base_day == start,
        "partial": partial or base_day != start, "days": days,
        "gain": points[-1][1] - base[1], "per_day": round((points[-1][1] - base[1]) / days),
        "prev_gain": prev_gain, "top": songs[:5],
    }


def build_site_data(snaps, now, site_url):
    meta = load_song_meta()
    goals = load_goals()
    latest = snaps[-1]
    before = snaps[-2] if len(snaps) >= 2 else None
    L = d(latest["date"])
    gap = (L - d(before["date"])).days if before else None

    # 곡별 날짜 기록
    song_points = {}
    for s in snaps:
        for x in s["songs"]:
            song_points.setdefault((x["group"], x["title"]), []).append((s["date"], x["count"]))

    groups, events, releases = [], [], {}
    for name in latest["group_order"]:
        points = [(s["date"], s["totals"][name]) for s in snaps if name in s["totals"]]
        total = latest["totals"][name]
        prev_total = before["totals"].get(name) if before else None

        songs = []
        for order, x in enumerate(s for s in latest["songs"] if s["group"] == name):
            key = (name, x["title"])
            m = meta.get(key, {})
            pts = song_points.get(key, [])
            prev_count = value_at(pts[:-1], L - timedelta(days=1)) if len(pts) >= 2 else None
            delta = x["count"] - prev_count if prev_count is not None else None
            avg, _ = pace(pts)
            target = next_milestone(x["count"])
            days, eta_date = eta(target - x["count"], avg, L)
            songs.append({
                "order": order, "title": x["title"], "album": m.get("album"),
                "released": m.get("released"), "link": m.get("melon"), "inst": is_inst(x["title"]),
                "count": x["count"], "prev_count": prev_count, "carried": x["carried"],
                "delta": delta, "delta_per_day": round(delta / gap) if (delta is not None and gap) else None,
                "avg_daily": round(avg) if avg else None,
                "next": {"target": target, "label": milestone_label(target),
                         "remaining": target - x["count"], "days": days, "eta": eta_date},
            })
            hit = crossed(prev_count, x["count"])
            if hit:
                events.append({"group": name, "title": x["title"], "target": hit,
                               "text": f"「{x['title']}」 {milestone_label(hit)} 회 돌파!"})
            if m.get("album") and m.get("released"):
                releases.setdefault((m["album"], m["released"]), name)

        # 그룹 합계 돌파 / 목표 달성
        hit = crossed(prev_total, total, GROUP_STEP)
        if hit:
            events.insert(0, {"group": name, "title": None, "target": hit,
                              "text": f"{name} 합계 {milestone_label(hit)} 회 돌파!"})
        for g in goals.get(name, []):
            if prev_total is not None and prev_total < g["target"] <= total:
                events.insert(0, {"group": name, "title": None, "target": g["target"], "goal": True,
                                  "text": f"🏆 {name} {g['label']} 달성! ({milestone_label(g['target'])} 회)"})

        # 앨범별 합계와 비중
        albums = {}
        for s in songs:
            a = albums.setdefault(s["album"] or "기타", {"album": s["album"] or "기타", "released": s["released"],
                                                         "total": 0, "inst_total": 0, "songs": 0})
            a["total"] += s["count"]
            a["songs"] += 1
            if s["inst"]:
                a["inst_total"] += s["count"]
        album_list = sorted(albums.values(), key=lambda a: a["total"], reverse=True)
        for a in album_list:
            a["share"] = a["total"] / total if total else 0
        inst_total = sum(s["count"] for s in songs if s["inst"])

        # 집중 스밍 추천곡: 원곡 중 다음 돌파가 가장 빠른 곡
        originals = [s for s in songs if not s["inst"]]
        with_eta = [s for s in originals if s["next"]["days"]]
        focus = min(with_eta, key=lambda s: s["next"]["days"]) if with_eta else \
            (min(originals, key=lambda s: s["next"]["remaining"] / song_step(s["count"])) if originals else None)

        groups.append({
            "name": name, "total": total, "prev_total": prev_total,
            "prev_date": before["date"] if before else None, "gap_days": gap,
            "delta": total - prev_total if prev_total is not None else None,
            "delta_per_day": round((total - prev_total) / gap) if (prev_total is not None and gap) else None,
            "series": [{"date": s["date"], "at": s.get("input_at") or s["taken_at"], "total": s["totals"][name]}
                       for s in snaps if name in s["totals"]],
            "summary": period_summary(points),
            "weekly": weekly_summary(points, {t: p for (gname, t), p in song_points.items() if gname == name},
                                     [x["title"] for x in latest["songs"] if x["group"] == name]),
            "goal": compute_goal(goals.get(name), total, points),
            "albums": album_list, "inst_share": inst_total / total if total else 0,
            "focus": {"title": focus["title"], "next": focus["next"], "count": focus["count"]} if focus else None,
            "songs": songs,
        })

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "confirmed_date": latest["date"],
        "basis_at": latest.get("input_at") or latest["taken_at"],
        "site_url": site_url,
        "events": events,
        "releases": sorted([{"album": a, "date": r, "group": g} for (a, r), g in releases.items()],
                           key=lambda x: x["date"]),
        "groups": groups,
    }


def write_csv(snaps):
    """엑셀에서 바로 열리는 CSV (날짜별 그룹 합계 + 곡별 숫자)"""
    order = []
    for s in reversed(snaps):  # 최근 입력 순서를 우선
        for x in s["songs"]:
            k = (x["group"], x["title"])
            if k not in order:
                order.append(k)
    groups = []
    for s in snaps:
        for g in s["group_order"]:
            if g not in groups:
                groups.append(g)
    with CSV_FILE.open("w", encoding="utf-8-sig", newline="") as f:  # utf-8-sig: 엑셀 한글 깨짐 방지
        w = csv.writer(f)
        w.writerow(["날짜", "입력 시각"] + [f"{g} 합계" for g in groups] + [f"{g} | {t}" for g, t in order])
        for s in snaps:
            counts = {(x["group"], x["title"]): x["count"] for x in s["songs"]}
            at = (s.get("input_at") or s["taken_at"])[11:16]
            w.writerow([s["date"], at] + [s["totals"].get(g, "") for g in groups]
                       + [counts.get(k, "") for k in order])


# ───────────────────────── 디스코드 알림 ─────────────────────────

def send_discord(site, replaced):
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        print("ℹ️  디스코드 웹후크 주소가 없어서 알림은 건너뛰었어요.")
        return
    at = datetime.fromisoformat(site["basis_at"]).astimezone(KST)
    lines = []
    for g in site["groups"]:
        delta = ""
        if g["delta"] is not None:
            delta = f" (**+{g['delta']:,}**"
            delta += f", {g['gap_days']}일치)" if g["gap_days"] and g["gap_days"] > 1 else ")"
        lines.append(f"**{g['name']}** {g['total']:,}회{delta}")
        goal = g.get("goal")
        if goal and not goal["done"]:
            txt = f"🎯 {goal['label']}까지 {goal['progress']:.1%} · 남은 {korean(goal['remaining'])}회"
            if goal.get("eta_date"):
                txt += f" · 예상 {goal['eta_date']} (D-{goal['days_left']})"
            lines.append(txt)
        lines.append("")
    main = site["groups"][0] if site["groups"] else None
    wk = main.get("weekly") if main else None
    if wk and wk["complete"]:   # 금요일 기록이면 주간 결산 한 줄
        s, e = date.fromisoformat(wk["start"]), date.fromisoformat(wk["end"])
        line = f"📅 **주간 결산** ({s.month}/{s.day} 금 ~ {e.month}/{e.day} 금) **+{wk['gain']:,}회**"
        if wk.get("prev_gain"):
            ch = (wk["gain"] - wk["prev_gain"]) / wk["prev_gain"]
            line += f" · 지난주 대비 {'▲' if ch >= 0 else '▼'}{abs(ch):.0%}"
        lines.append(line)
        if wk["top"]:
            lines.append("　이번 주 TOP3: " + " · ".join(f"{x['title']} +{x['gain']:,}" for x in wk["top"][:3]))
        lines.append("")
    if main:
        risers = sorted([s for s in main["songs"] if s["delta"]], key=lambda s: s["delta"], reverse=True)[:3]
        if risers:
            lines.append("📈 많이 오른 곡: " + " · ".join(f"{s['title']} +{s['delta']:,}" for s in risers))
        if main.get("focus"):
            f = main["focus"]
            txt = f"🔥 집중 추천: 「{f['title']}」 {f['next']['label']}까지 {f['next']['remaining']:,}회"
            if f["next"].get("days"):
                txt += f" (약 {f['next']['days']}일)"
            lines.append(txt)
    if site["events"]:
        lines.append("")
        lines += [f"🎉 {e['text']}" for e in site["events"][:8]]

    payload = {
        "username": "이세돌 멜론 집계",
        "embeds": [{
            "title": f"📊 {at.month}월 {at.day}일 {at.hour}시 기준 멜론 누적 스트리밍" + (" (수정)" if replaced else ""),
            "url": site.get("site_url") or None,
            "description": "\n".join(lines).strip()[:4000],
            "color": 0x52D1F9,
        }],
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "isedol-melon-total (GitHub Actions)"})
    try:
        urllib.request.urlopen(req, timeout=15).read()
        print("✅ 디스코드에 알림을 보냈어요.")
    except Exception as e:  # 알림 실패는 기록 저장을 막지 않음
        print(f"⚠️  디스코드 알림을 보내지 못했어요: {e}")


# ───────────────────────── 실행 ─────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["record", "preview"], default="preview")
    ap.add_argument("--now", help="테스트용 현재 시각 (예: 2026-11-26T00:05:00)")
    ap.add_argument("--input-time", help="input.txt 를 마지막으로 고친 시각 (GitHub Actions 가 넣어 줌)")
    ap.add_argument("--allow-decrease", action="store_true", help="이전보다 줄어든 숫자도 그대로 기록")
    args = ap.parse_args()

    now = datetime.fromisoformat(args.now).replace(tzinfo=KST) if args.now else datetime.now(KST)
    if args.input_time:
        t = datetime.fromisoformat(args.input_time.replace("Z", "+00:00"))
        input_at = (t if t.tzinfo else t.replace(tzinfo=KST)).astimezone(KST)
    else:
        input_at = now

    rows = read_input()
    history = load_json(HISTORY_FILE, {"snapshots": []})
    snaps = sorted(history.get("snapshots", []), key=lambda s: s["date"])
    today = input_at.strftime("%Y-%m-%d")
    previous = next((s for s in reversed(snaps) if s["date"] < today), None)
    replaced = any(s["date"] == today for s in snaps)

    check_mistakes(rows, previous, args.allow_decrease)
    snapshot = build_snapshot(rows, previous, now, input_at)
    if not snapshot["songs"]:
        print("ℹ️  아직 입력된 숫자가 하나도 없어서 이번엔 건너뛰었어요. data/input.txt 에 숫자를 채워 주세요.")
        return

    snaps = [s for s in snaps if s["date"] != today] + [snapshot]
    snaps.sort(key=lambda s: s["date"])

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    site_url = f"https://{repo.split('/')[0].lower()}.github.io/{repo.split('/')[1]}/" if "/" in repo else ""
    site = build_site_data(snaps, now, site_url)

    if args.mode == "record":
        HISTORY_FILE.write_text(json.dumps({"snapshots": snaps}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"✅ {input_at:%Y-%m-%d %H}시 입력 숫자로 {today} 기록을 저장했어요.")
    else:
        print("👀 미리보기만 만들었어요 (기록은 저장하지 않았어요).")

    SITE_DATA_FILE.write_text(json.dumps(site, ensure_ascii=False, indent=1), encoding="utf-8")
    write_csv(snaps)
    for name, total in snapshot["totals"].items():
        print(f"   {name}: {total:,}회")
    for e in site["events"]:
        print(f"   🎉 {e['text']}")

    if args.mode == "record":
        send_discord(site, replaced)


if __name__ == "__main__":
    main()
