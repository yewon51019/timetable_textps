"""등교 경로 조합 계산기 (FR-02 도착 목표 + FR-03 탑승편 매칭·출발/준비 시각).

API를 부르지 않는 순수 함수만 둔다 → 키 없이도 테스트할 수 있다.

경로 = 집에서 학교까지의 구간(leg) 목록
  walk   : 걷기 (분)
  subway : 1호선 (승차역 → 하차역), KRIC 시각표를 열차번호로 맞춘 SubwayTrip 목록 사용
  bus    : 경기 버스 (승차 정류소 → 하차 정류소), 배차간격·실시간 도착 사용

계산은 **도착 목표 시각에서 거꾸로**(마지막 구간부터) 한다:
  t = 첫 수업 − 수업 전 여유
  걷기   : t -= 걷는 시간
  버스   : t -= 승차 시간 + 대기 (실시간 있으면 그 값, 없으면 최대 배차간격 = 최악의 대기)
  지하철 : 하차 시각 ≤ t 인 열차 중 가장 늦게 출발하는 것 선택 → t = 그 열차 승차역 출발 − 탑승 여유
  출발 = t,  준비 시작 = 출발 − 준비 시간
"""
from dataclasses import dataclass, field
from typing import List, Optional

# KRIC exptCd(급행구분) 중 '급행'으로 보는 값.
# ⚠ 명세에 코드값 설명이 없어 실제 응답으로 확정해야 한다 (tools/check_transit_api.py 가 분포를 출력).
EXPRESS_CODES = {'1', 'Y', 'E', '급행'}


def parse_hhmm(s) -> Optional[int]:
    """'0823', '082300', '08:23', '08:23:00' → 하루 기준 분(503). 빈 값이면 None."""
    if s is None:
        return None
    d = ''.join(ch for ch in str(s) if ch.isdigit())
    if len(d) < 3:
        return None
    if len(d) in (3, 5):
        d = '0' + d
    return int(d[:2]) * 60 + int(d[2:4])


def fmt(m: Optional[int]) -> Optional[str]:
    return None if m is None else f'{(m // 60) % 24:02d}:{m % 60:02d}'


@dataclass
class SubwayTrip:
    trn_no: str
    board_dep: int      # 승차역 출발 (분)
    alight_arr: int     # 하차역 도착 (분)
    is_express: bool


def join_trips(board_rows, alight_rows) -> List[SubwayTrip]:
    """승차역·하차역 시각표를 열차번호(trnNo)로 맞춘다.

    KRIC 역 시각표는 상·하행이 섞여 오지만, '하차역 도착이 승차역 출발보다 늦은 열차'만 남기면
    가려는 방향의 열차만 남는다 (반대 방향은 하차역을 먼저 지나므로 자동으로 빠짐).
    """
    alight = {}
    for r in alight_rows:
        t = parse_hhmm(r.get('arvTm')) or parse_hhmm(r.get('dptTm'))
        if t is not None:
            alight[str(r.get('trnNo'))] = t
    trips = []
    for r in board_rows:
        no = str(r.get('trnNo'))
        dep = parse_hhmm(r.get('dptTm'))
        arr = alight.get(no)
        if dep is None or arr is None or arr <= dep:
            continue
        trips.append(SubwayTrip(no, dep, arr, str(r.get('exptCd', '')).strip() in EXPRESS_CODES))
    return sorted(trips, key=lambda x: x.board_dep)


@dataclass
class Leg:
    kind: str                          # 'walk' | 'subway' | 'bus'
    name: str = ''
    minutes: int = 0                   # walk: 걷는 시간 / bus: 승차 시간(탑승→하차)
    express_only: bool = True          # subway: 급행만 탈지
    trips: List[SubwayTrip] = field(default_factory=list)   # subway
    headway_max: Optional[int] = None  # bus: 최대 배차간격(nPeekAlloc), 계획 단계 대기 시간
    realtime_wait: Optional[int] = None  # bus: 실시간 다음 버스까지 분 (당일에만)


@dataclass
class Plan:
    status: str                        # planned | no_trip
    target_arrival: str
    depart: Optional[str] = None
    prep_start: Optional[str] = None
    steps: List[dict] = field(default_factory=list)   # 집 → 학교 순서
    warnings: List[str] = field(default_factory=list)


def plan_commute(first_class: str, legs: List[Leg], arrive_buffer_min: int,
                 board_buffer_min: int, prep_min: int) -> Plan:
    target = parse_hhmm(first_class) - arrive_buffer_min
    t = target
    steps, warnings = [], []

    for leg in reversed(legs):
        if leg.kind == 'walk':
            steps.append({'leg': leg.name or '걷기', 'start': fmt(t - leg.minutes), 'end': fmt(t)})
            t -= leg.minutes

        elif leg.kind == 'bus':
            if leg.realtime_wait is not None:
                wait, basis = leg.realtime_wait, 'realtime'
            elif leg.headway_max is not None:
                wait, basis = leg.headway_max, 'headway'
            else:
                return Plan('no_trip', fmt(target), warnings=[f'{leg.name}: 배차 정보 없음'])
            board_by = t - leg.minutes
            steps.append({'leg': leg.name, 'start': fmt(board_by - wait), 'board_by': fmt(board_by),
                          'end': fmt(t), 'wait_min': wait, 'basis': basis})
            t = board_by - wait

        elif leg.kind == 'subway':
            def latest(cands):
                ok = [x for x in cands if x.alight_arr <= t]
                return max(ok, key=lambda x: x.board_dep) if ok else None

            trip = latest([x for x in leg.trips if x.is_express]) if leg.express_only else latest(leg.trips)
            if trip is None and leg.express_only:   # AC-03-02: 급행이 없으면 일반 열차 후보
                trip = latest(leg.trips)
                if trip:
                    warnings.append(f'{leg.name}: 조건을 만족하는 급행이 없어 일반 열차로 안내')
            if trip is None:
                return Plan('no_trip', fmt(target), warnings=[f'{leg.name}: 목표 시각에 맞는 열차 없음'])
            steps.append({'leg': leg.name, 'train': trip.trn_no, 'express': trip.is_express,
                          'board': fmt(trip.board_dep), 'alight': fmt(trip.alight_arr),
                          'platform_by': fmt(trip.board_dep - board_buffer_min)})
            t = trip.board_dep - board_buffer_min
        else:
            raise ValueError(leg.kind)

    steps.reverse()
    return Plan('planned', fmt(target), fmt(t), fmt(t - prep_min), steps, warnings)


def alarm_times(base: str, count: int, interval: int) -> List[str]:
    """REQ-04-02: 기준 − (n−1)k, …, 기준."""
    b = parse_hhmm(base)
    return [fmt(b - (count - 1 - i) * interval) for i in range(count)]
