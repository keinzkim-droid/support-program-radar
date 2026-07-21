"""수집된 공고를 회사 프로필 기준으로 분류·채점한다.

두 트랙으로 나뉜다.
  A: 우리가 직접 신청하는 사업 — 지역 자격이 걸린다
  B: 고객사가 신청하고 우리는 공급기업으로 참여 — 지역 제한 없음

판정은 보수적으로 한다. 애매하면 탈락시키지 않고 사람이 보도록 남긴다.
기계가 잘못 걸러내면 기회를 통째로 잃지만, 잘못 남기면 30초 훑고 넘기면 된다.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
PROFILE = ROOT / "config" / "profile.yaml"
CURATED = ROOT / "config" / "curated.yaml"
IN = ROOT / "data" / "announcements.json"
OUT = ROOT / "data" / "classified.json"

# 제목 앞의 [울산] [전남광주] 같은 지역 태그
REGION_TAG = re.compile(r"^\s*\[([가-힣]+)\]")

# 지자체명 → 우리 기준 지역 키
REGION_ALIAS = {
    "경기": "경기", "경기도": "경기", "성남": "성남", "판교": "판교",
    "인천": "인천", "인천광역시": "인천",
}


@dataclass
class Verdict:
    track: str | None          # "A" | "B" | None(제외)
    score: int
    region: str | None         # 감지된 지역 (None이면 전국)
    conditional: str | None    # 조건부 사유 (profile.yaml의 region.conditional 값)
    reasons: list[str]


def load_yaml(path: Path, default=None):
    if not path.exists():
        return default if default is not None else {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def detect_region(item: dict) -> str | None:
    """제목 태그와 소관기관에서 지역을 뽑는다. 못 찾으면 전국(None)."""
    m = REGION_TAG.match(item["title"])
    if m:
        return REGION_ALIAS.get(m.group(1), m.group(1))
    agency = item.get("agency", "")
    for name, key in REGION_ALIAS.items():
        if name in agency:
            return key
    # 광역지자체가 소관이면 지역 사업으로 본다
    if agency.endswith(("광역시", "특별자치도", "도", "특별시")):
        return agency
    return None


def hits(text: str, words: list[str]) -> list[str]:
    return [w for w in words if w in text]


def judge(item: dict, profile: dict) -> Verdict:
    kw = profile["keywords"]
    sc = profile["scoring"]
    reg = profile["region"]
    text = f"{item['title']} {item.get('category','')} {item.get('exec_agency','')}"
    reasons: list[str] = []

    # 1) 명백히 무관한 분야는 먼저 걷어낸다
    ex = hits(text, kw["exclude"])
    if ex:
        return Verdict(None, 0, None, None, [f"제외어: {', '.join(ex)}"])

    # 2) 트랙 판정 — B(고객사향)를 먼저 본다. 스마트공장은 우리가 신청 못 한다.
    b_hit = hits(text, kw["track_b_strong"])
    a_strong = hits(text, kw["track_a_strong"])
    a_medium = hits(text, kw["track_a_medium"])
    # 트랙 판정에는 로봇 계열만 쓴다. AI는 양쪽에 다 붙어서 기준이 못 된다.
    a_decisive = hits(text, kw.get("track_a_decisive") or kw["track_a_strong"])

    if b_hit and not a_decisive:
        score = len(b_hit) * sc["strong"]
        reasons.append(f"고객사향 키워드: {', '.join(b_hit)}")
        return Verdict("B", score, detect_region(item), None, reasons)

    score = len(a_strong) * sc["strong"] + len(a_medium) * sc["medium"]
    if a_strong:
        reasons.append(f"핵심 키워드: {', '.join(a_strong)}")
    if a_medium:
        reasons.append(f"관련 키워드: {', '.join(a_medium)}")
    if score < sc["min_score"]:
        return Verdict(None, score, None, None, reasons + ["점수 미달"])

    # 3) 트랙 A는 지역 자격을 따진다
    region = detect_region(item)
    conditional = None
    if region:
        if region in reg["conditional"]:
            conditional = reg["conditional"][region]
            reasons.append(f"조건부: {conditional}")
        elif region not in reg["eligible"]:
            return Verdict(None, score, region, None,
                           reasons + [f"지역 불일치: {region}"])

    return Verdict("A", score, region, conditional, reasons)


def status_of(item: dict, today: date, soon_days: int) -> str:
    """접수 상태. 소스가 알려주면 그걸 우선한다."""
    end = item.get("apply_end")
    if not end:
        return "상시"           # '상시 접수' 등 날짜 없는 경우
    d = date.fromisoformat(end)
    if d < today:
        return "마감"
    if (d - today).days <= soon_days:
        return "마감임박"
    return "접수중"


def main() -> int:
    profile = load_yaml(PROFILE)
    curated = load_yaml(CURATED, {}) or {}
    approved = set(curated.get("approved") or [])
    rejected = set(curated.get("rejected") or [])

    raw = json.loads(IN.read_text(encoding="utf-8"))
    today = date.today()
    soon = profile["scoring"]["deadline_soon_days"]

    archive_days = profile["scoring"].get("archive_after_days", 30)
    cards, expired, candidates, dropped = [], [], [], 0

    for item in raw["items"]:
        key = f"{item['source']}:{item['source_id']}"
        if key in rejected:
            dropped += 1
            continue

        v = judge(item, profile)

        # 사람이 승인한 항목은 자동 판정보다 우선한다.
        # 기계가 탈락시켰더라도(지역 불일치·점수 미달 등) 목록에서 빼지 않는다.
        # 그러지 않으면 키워드를 손볼 때마다 확정한 목록이 흔들린다.
        if key in approved and v.track is None:
            text = f"{item['title']} {item.get('category','')}"
            is_b = bool(hits(text, profile["keywords"]["track_b_strong"]))
            v = Verdict("B" if is_b else "A", v.score, v.region, v.conditional,
                        (v.reasons or []) + ["사람이 확정"])

        if v.track is None:
            dropped += 1
            continue

        status = status_of(item, today, soon)

        # 이미 마감된 건 신규 후보로 올리지 않는다. 승인할 이유가 없다.
        if status == "마감" and key not in approved:
            dropped += 1
            continue

        # 승인된 공고가 마감되면 '마감' 탭으로 옮긴다. 바로 지우지 않는 이유는
        # 방금 놓친 공고를 확인하거나 내년 회차를 준비할 때 참고하기 때문이다.
        # 다만 무한정 쌓이면 목록이 지저분해지므로 일정 기간 뒤 화면에서 내린다.
        # (curated.yaml의 승인 기록 자체는 남아 이력을 잃지 않는다)
        archived = False
        if status == "마감":
            end = item.get("apply_end")
            if end and date.fromisoformat(end) < today - timedelta(days=archive_days):
                dropped += 1
                continue
            archived = True

        rec = dict(item)
        rec.update(
            key=key,
            track=v.track,
            score=v.score,
            region=v.region,
            conditional=v.conditional,
            reasons=v.reasons,
            status=status,
        )
        if archived:
            expired.append(rec)
        elif key in approved:
            cards.append(rec)
        else:
            candidates.append(rec)

    for bucket in (cards, candidates):
        bucket.sort(key=lambda r: (-r["score"], r["apply_end"] or "9999"))
    expired.sort(key=lambda r: r["apply_end"] or "", reverse=True)  # 최근 마감순

    OUT.write_text(json.dumps({
        "generated_at": today.isoformat(),
        "cards": cards,
        "expired": expired,
        "candidates": candidates,
        "archive_after_days": archive_days,
        "stats": {
            "collected": raw["count"],
            "cards": len(cards),
            "expired": len(expired),
            "candidates": len(candidates),
            "dropped": dropped,
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"수집 {raw['count']}건 → 카드 {len(cards)} / 마감 {len(expired)}"
          f" / 신규후보 {len(candidates)} / 제외 {dropped}")
    for r in candidates:
        flag = " [조건부]" if r["conditional"] else ""
        print(f"  [{r['track']}] {r['score']:2}점 {r['status']:5}{flag} {r['title'][:50]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
