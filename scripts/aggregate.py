"""
이세계아이돌 멜론 누적 스트리밍 합계 계산기

하는 일
  1. data/input.txt 에 적힌 곡별 누적 스트리밍 수를 읽는다.
  2. 그룹 이름(예: 이세계아이돌, 고세구)별로 합계를 낸다.
  3. --mode record  : 오늘 날짜(한국 시간) 00시 기준 기록으로 data/history.json 에 저장
     --mode preview : 기록은 하지 않고 "입력됨(아직 확정 전)" 숫자만 사이트에 보여줌
  4. 사이트가 읽는 docs/data.json 을 새로 만든다.

외부 라이브러리 없이 파이썬 기본 기능만 사용한다.
"""

import argparse
import json
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
INPUT_FILE = ROOT / "data" / "input.txt"
HISTORY_FILE = ROOT / "data" / "history.json"
SITE_DATA_FILE = ROOT / "docs" / "data.json"
SONGS_FILE = ROOT / "data" / "songs.json"  # 곡별 멜론 링크 (없어도 됨)
GOALS_FILE = ROOT / "data" / "goals.json"  # 목표치 (없어도 됨)
PACE_DAYS = 7  # 예상 달성일 계산에 쓰는 최근 기간(일)


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
        key = (group, title)
        if key in seen:
            errors.append(f"  {no}번째 줄: 같은 곡이 두 번 적혀 있어요 → {group} | {title}")
            continue
        seen.add(key)
        rows.append((group, title, count))

    if errors:
        print("❌ input.txt 에 고칠 곳이 있어요:")
        print("\n".join(errors))
        sys.exit(1)
    if not rows:
        sys.exit("❌ input.txt 에 곡이 하나도 없어요.")
    return rows


def load_history():
    if HISTORY_FILE.exists():
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        data.setdefault("snapshots", [])
        return data
    return {"snapshots": []}


def song_key(group, title):
    return f"{group}|{title}"


def build_snapshot(rows, previous, now):
    """입력값으로 한 번의 집계 결과를 만든다. 빈칸은 직전 기록 숫자를 이어 쓴다."""
    prev_songs = {song_key(s["group"], s["title"]): s["count"] for s in (previous or {}).get("songs", [])}
    songs, group_order, totals = [], [], {}
    for group, title, count in rows:
        carried = False
        if count is None:
            count = prev_songs.get(song_key(group, title))
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
        "date": now.strftime("%Y-%m-%d"),
        "taken_at": now.isoformat(timespec="seconds"),
        "group_order": group_order,
        "totals": totals,
        "songs": songs,
    }


def load_links():
    """songs.json → {(그룹, 곡 제목): 멜론 링크}"""
    if not SONGS_FILE.exists():
        return {}
    data = json.loads(SONGS_FILE.read_text(encoding="utf-8"))
    return {(g, s["title"]): s.get("melon") for g, songs in data.items() for s in songs}


def load_goals():
    if not GOALS_FILE.exists():
        return {}
    data = json.loads(GOALS_FILE.read_text(encoding="utf-8"))
    return {k: sorted(v, key=lambda g: g["target"]) for k, v in data.items() if not k.startswith("_")}


def compute_goal(steps, total, series):
    """아직 못 넘은 첫 목표까지 남은 횟수와, 최근 속도로 계산한 예상 달성일."""
    if not steps:
        return None
    passed = [g for g in steps if total >= g["target"]]
    upcoming = [g for g in steps if total < g["target"]]
    if not upcoming:
        return {"label": steps[-1]["label"], "target": steps[-1]["target"], "done": True,
                "passed": [g["label"] for g in passed]}
    goal = upcoming[0]
    remaining = goal["target"] - total
    result = {"label": goal["label"], "target": goal["target"], "done": False,
              "remaining": remaining, "progress": total / goal["target"],
              "passed": [g["label"] for g in passed],
              "avg_daily": None, "pace_days": None, "days_left": None, "eta_date": None}

    # 최근 PACE_DAYS 일 안의 기록으로 하루 평균 증가량 계산
    if len(series) >= 2:
        last = series[-1]
        last_day = datetime.fromisoformat(last["date"])
        window = [p for p in series[:-1]
                  if (last_day - datetime.fromisoformat(p["date"])).days <= PACE_DAYS]
        first = window[0] if window else series[-2]
        days = (last_day - datetime.fromisoformat(first["date"])).days
        gained = last["total"] - first["total"]
        if days > 0 and gained > 0:
            avg = gained / days
            days_left = -(-remaining // int(avg)) if avg >= 1 else None  # 올림
            result.update({
                "avg_daily": round(avg),
                "pace_days": days,
                "days_left": days_left,
                "eta_date": (last_day + timedelta(days=days_left)).strftime("%Y-%m-%d") if days_left else None,
            })
    return result


def build_site_data(history, pending, now):
    links = load_links()
    goals = load_goals()
    snaps = sorted(history["snapshots"], key=lambda s: s["date"])
    latest = snaps[-1] if snaps else None
    before = snaps[-2] if len(snaps) >= 2 else None
    base = latest or pending  # 아직 확정 기록이 하나도 없으면 입력값으로 화면을 채움

    groups = []
    for name in base["group_order"]:
        prev_counts = {}
        if latest and before:
            prev_counts = {s["title"]: s["count"] for s in before["songs"] if s["group"] == name}
        songs = []
        for s in base["songs"]:
            if s["group"] != name:
                continue
            p = prev_counts.get(s["title"])
            songs.append({
                "title": s["title"],
                "count": s["count"],
                "delta": (s["count"] - p) if p is not None else None,
                "carried": s["carried"],
                "link": links.get((name, s["title"])),
            })
        songs.sort(key=lambda s: s["count"], reverse=True)

        total = base["totals"].get(name, 0)
        prev_total = before["totals"].get(name) if (latest and before) else None
        series = [{"date": s["date"], "total": s["totals"][name]} for s in snaps if name in s["totals"]]

        pend = None
        if latest and pending and name in pending["totals"]:
            diff = pending["totals"][name] - total
            if diff != 0:
                pend = {"total": pending["totals"][name], "diff": diff}

        groups.append({
            "name": name,
            "total": total,
            "delta": (total - prev_total) if prev_total is not None else None,
            "prev_date": before["date"] if (latest and before) else None,
            "series": series,
            "songs": songs,
            "pending": pend,
            "goal": compute_goal(goals.get(name), total, series),
        })

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "confirmed_date": latest["date"] if latest else None,
        "only_pending": latest is None,
        "pending_taken_at": pending["taken_at"] if pending else None,
        "groups": groups,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["record", "preview"], default="preview")
    ap.add_argument("--now", help="테스트용 현재 시각 (예: 2026-11-26T00:05:00)")
    args = ap.parse_args()

    now = datetime.fromisoformat(args.now).replace(tzinfo=KST) if args.now else datetime.now(KST)
    rows = read_input()
    history = load_history()
    snaps = sorted(history["snapshots"], key=lambda s: s["date"])
    today = now.strftime("%Y-%m-%d")
    previous = next((s for s in reversed(snaps) if s["date"] < today), None) if args.mode == "record" \
        else (snaps[-1] if snaps else None)

    snapshot = build_snapshot(rows, previous, now)
    if not snapshot["songs"]:
        print("ℹ️  아직 입력된 숫자가 하나도 없어서 이번엔 건너뛰었어요. data/input.txt 에 숫자를 채워 주세요.")
        return

    if args.mode == "record":
        history["snapshots"] = [s for s in snaps if s["date"] != today] + [snapshot]
        history["snapshots"].sort(key=lambda s: s["date"])
        HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"✅ {today} 00시 기준으로 기록했어요.")
        pending = None
    else:
        pending = snapshot
        print("👀 미리보기만 갱신했어요 (기록은 00시에 확정돼요).")

    site = build_site_data(history, pending, now)
    SITE_DATA_FILE.write_text(json.dumps(site, ensure_ascii=False, indent=1), encoding="utf-8")

    for name, total in snapshot["totals"].items():
        print(f"   {name}: {total:,}회")


if __name__ == "__main__":
    main()
