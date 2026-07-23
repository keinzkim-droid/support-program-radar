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
DECISIONS = ROOT / "config" / "decisions"
IN = ROOT / "data" / "announcements.json"
OUT = ROOT / "data" / "classified.json"

# 제목 앞의 [울산] [전남광주] 같은 지역 태그
REGION_TAG = re.compile(r"^\s*\[([가-힣]+)\]")

# 지자체명 → 우리 기준 지역 키
REGION_ALIAS = {
    "경기": "경기", "경기도": "경기", "성남": "성남", "판교": "판교",
    "인천": "인천", "인천광역시": "인천",
    # '[서울]' 표기와 소관기관 '서울특별시'가 같은 지역으로 잡히게 한다
    "서울": "서울", "서울특별시": "서울",
}

# 비수도권 지역명. 제목에 이 말이 있으면 우리 지역이 아니라고 본다.
# K-Startup 창업보육센터 공고는 '[지역]' 표기 없이 기관명만 있어(충남대·세종…)
# 제목 본문까지 봐야 지방 건을 걸러낼 수 있다.
NON_METRO = ["부산", "대구", "광주", "대전", "울산", "세종",
             "강원", "춘천", "원주", "충북", "청주", "충남", "천안", "아산",
             "전북", "전주", "익산", "전남", "여수", "순천",
             "경북", "포항", "구미", "경남", "창원", "김해", "제주"]


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


def load_decisions() -> dict[str, str]:
    """웹페이지 버튼으로 만들어진 결정 파일을 읽는다.

    결정 하나당 파일 하나이고 파일명이 타임스탬프로 시작한다.
    같은 공고에 결정이 여러 개면 나중 것이 이긴다 — 되돌리기를 위해서다.

    curated.yaml을 직접 고치지 않는 이유: 웹에서 기존 파일의 특정 위치에
    내용을 끼워 넣게 만들 방법이 없다. 새 파일 생성은 내용을 미리 채워줄 수
    있어서 클릭 두 번으로 끝난다.
    """
    out: dict[str, tuple[str, str | None]] = {}
    if not DECISIONS.exists():
        return out
    for f in sorted(DECISIONS.glob("*.yml")):          # 파일명 = 시간순
        try:
            d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if d.get("key") and d.get("decision") in ("approved", "rejected", "pending"):
            # 결정 날짜는 '제외' 목록을 언제 정리할지 판단하는 데 쓴다.
            out[d["key"]] = (d["decision"], str(d.get("decided_at") or ""))
    return out


def detect_region(item: dict) -> str | None:
    """제목 태그와 소관기관에서 지역을 뽑는다. 못 찾으면 전국(None)."""
    # 사무실 공고는 상세에서 확인한 실제 소재지를 최우선으로 본다.
    # 목록의 지역은 주관기관 기준이라 실제 위치와 다르다 —
    # '국토교통 창업지원센터'는 목록상 전국이지만 사무실은 판교에 있다.
    loc = item.get("location") or ""
    if loc:
        # 주소는 앞부분이 광역시·도다. 뒷부분 건물명에 지역명이 섞이는 경우가
        # 있어(예: '제주 … 제주 월드컵 경기장' → '경기') 앞 25자만 본다.
        head = loc[:25]
        # 지방을 먼저 본다. 수도권 단어가 주소 뒤에 우연히 들어가는 일이 있다.
        for name in NON_METRO:
            if name in head:
                return name
        for name, key in REGION_ALIAS.items():
            if name in head:
                return key

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

    # 여기까지 못 찾으면 제목 본문에서 지역명을 찾는다.
    # 수도권 표기가 있으면 그것을 우선하고(예: '서울창업허브'),
    # 없이 지방 지역명만 있으면 지방 사업으로 본다(예: '충남대학교 창업보육센터').
    title = item["title"]
    for name, key in REGION_ALIAS.items():
        if name in title:
            return key
    for name in NON_METRO:
        if name in title:
            return name
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

    # 3) 사무실·공간(트랙 C). 로봇 사업과 성격이 달라 따로 본다.
    #    지역 기준도 달라서 서울까지 넓게 잡는다(이전을 검토 중이므로).
    c_hit = hits(text, kw.get("track_c_strong") or [])
    if c_hit and not a_decisive:
        region = detect_region(item)
        conditional = None
        if region:
            if region in reg["conditional"]:
                conditional = reg["conditional"][region]
            elif region not in (reg.get("office_eligible") or reg["eligible"]):
                return Verdict(None, 0, region, None,
                               [f"사무실 사업이나 지역 불일치: {region}"])
        reasons.append(f"사무실·공간 키워드: {', '.join(c_hit)}")
        if conditional:
            reasons.append(f"조건부: {conditional}")
        return Verdict("C", len(c_hit) * sc["strong"], region, conditional, reasons)

    score = len(a_strong) * sc["strong"] + len(a_medium) * sc["medium"]
    if a_strong:
        reasons.append(f"핵심 키워드: {', '.join(a_strong)}")
    if a_medium:
        reasons.append(f"관련 키워드: {', '.join(a_medium)}")
    if score < sc["min_score"]:
        return Verdict(None, score, None, None, reasons + ["점수 미달"])

    # 4) 트랙 A는 지역 자격을 따진다
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


def should_auto_promote(rec: dict, profile: dict) -> bool:
    """사람 승인 없이 목록에 올려도 되는가.

    점수만으로는 판단할 수 없다. 점수는 '관련성'이 아니라 '키워드가 몇 개
    겹쳤나'를 재기 때문에, 대전·부산 대학의 창업보육센터가 9점으로 최상위에
    온다(입주기업+창업보육+보육센터). 그래서 지역 확인을 함께 요구한다.
    """
    cfg = (profile.get("scoring") or {}).get("auto_promote") or {}
    if not cfg:
        return False

    # 조건부 지역(인천 등)은 자격이 아직 없으므로 자동으로 올리지 않는다.
    if rec.get("conditional"):
        return False

    text = f"{rec['title']} {rec.get('exec_agency', '')}"
    if any(w in text for w in cfg.get("block") or []):
        return False

    region = rec.get("region")

    # 사무실은 위치가 본질이라 지역을 반드시 확인한다.
    if rec["track"] == "C":
        if cfg.get("office_require_region", True) and not region:
            return False
        return rec["score"] >= cfg.get("office_min_score", 3)

    # 트랙 B(고객사 제안)는 지역 제한이 없어 전국 공고가 다 들어온다.
    # 자동으로 올리는 것은 수도권·전국만 하고, 타지역은 사람이 본다.
    # 그 지역에 고객사가 있을 때만 의미가 있어 일괄 판단할 수 없다.
    if region and region not in (profile["region"].get("office_eligible")
                                 or profile["region"]["eligible"]):
        return False

    # 우리 지역이면 낮은 점수도 올리고(지자체 사업은 키워드가 적게 걸린다),
    # 지역 표기가 없는 전국구 사업이면 점수로 확실한 것만 올린다.
    floor = (cfg.get("local_min_score", 3) if region
             else cfg.get("nationwide_min_score", 6))
    return rec["score"] >= floor


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

    # 웹페이지에서 누른 결정이 curated.yaml보다 우선한다(최신 의사이므로).
    # decided는 '사람이 이미 판단한 건' — 자동 분류가 이를 덮어쓰면 안 된다.
    # 특히 되돌리기(pending)를 눌렀는데 다시 자동 승격되면 되돌릴 방법이 없어진다.
    decisions = load_decisions()
    decided: set[str] = set(decisions) | set(curated.get("rejected") or [])
    rejected_at: dict[str, str] = {}
    for key, (decision, when) in decisions.items():
        approved.discard(key)
        rejected.discard(key)
        if decision == "approved":
            approved.add(key)
        elif decision == "rejected":
            rejected.add(key)
            rejected_at[key] = when
        # pending이면 양쪽 어디에도 넣지 않아 '새로 찾은 공고'로 돌아간다

    raw = json.loads(IN.read_text(encoding="utf-8"))
    today = date.today()
    soon = profile["scoring"]["deadline_soon_days"]

    archive_days = profile["scoring"].get("archive_after_days", 30)
    cards, expired, candidates, excluded, dropped = [], [], [], [], 0

    for item in raw["items"]:
        key = f"{item['source']}:{item['source_id']}"

        # 제외한 공고도 일정 기간은 보여준다. 실수로 눌렀을 때 되돌릴 방법이
        # 없으면 안 되기 때문이다. 기간이 지나면 화면에서 내린다.
        if key in rejected:
            when = rejected_at.get(key)
            if when and when >= (today - timedelta(days=archive_days)).isoformat():
                rec = dict(item)
                rec.update(key=key, track="-", score=0, region=None,
                           conditional=None, reasons=["사람이 제외함"],
                           status=status_of(item, today, soon),
                           rejected_at=when)
                excluded.append(rec)
            else:
                dropped += 1
            continue

        v = judge(item, profile)

        # 사람이 승인한 항목은 자동 판정보다 우선한다.
        # 기계가 탈락시켰더라도(지역 불일치·점수 미달 등) 목록에서 빼지 않는다.
        # 그러지 않으면 키워드를 손볼 때마다 확정한 목록이 흔들린다.
        if key in approved and v.track is None:
            text = f"{item['title']} {item.get('category','')}"
            kw = profile["keywords"]
            if hits(text, kw.get("track_c_strong") or []):
                track = "C"
            elif hits(text, kw["track_b_strong"]):
                track = "B"
            else:
                track = "A"
            v = Verdict(track, v.score, v.region, v.conditional,
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
        # 자동 승격 — 조건을 만족하면 사람 승인 없이 목록에 올린다.
        # 사람이 이미 판단한 건(approved/rejected/pending 결정 파일)은 건드리지 않는다.
        auto = False
        if key not in approved and key not in decided:
            auto = should_auto_promote(rec, profile)
            if auto:
                rec["reasons"] = (rec.get("reasons") or []) + ["자동 분류"]
                rec["auto"] = True

        if archived:
            expired.append(rec)
        elif key in approved or auto:
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
        "excluded": excluded,
        "archive_after_days": archive_days,
        # 화면 문구를 실제 수집 소스에서 만들기 위해 넘긴다.
        # 하드코딩해두면 소스를 추가할 때마다 문구가 낡는다.
        "sources": sorted({i["source"] for i in raw["items"]}),
        "stats": {
            "collected": raw["count"],
            "cards": len(cards),
            "expired": len(expired),
            "candidates": len(candidates),
            "excluded": len(excluded),
            "dropped": dropped,
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"수집 {raw['count']}건 → 카드 {len(cards)} / 마감 {len(expired)}"
          f" / 신규후보 {len(candidates)} / 제외목록 {len(excluded)}"
          f" / 미표시 {dropped}")
    for r in candidates:
        flag = " [조건부]" if r["conditional"] else ""
        print(f"  [{r['track']}] {r['score']:2}점 {r['status']:5}{flag} {r['title'][:50]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
