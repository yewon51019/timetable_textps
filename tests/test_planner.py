"""경로 조합 계산기 테스트 — PRD 수용 기준 예시값을 그대로 사용한다."""
from transit.planner import Leg, alarm_times, join_trips, plan_commute

# 가상의 KRIC 응답 (필드 이름은 subwayTimetableExp 명세 그대로)
BOARD = [  # 승차역 (예: 금정)
    {'trnNo': 'K101', 'dptTm': '081000', 'arvTm': '080930', 'exptCd': '1'},  # 급행
    {'trnNo': 'K103', 'dptTm': '082000', 'arvTm': '081930', 'exptCd': '0'},  # 일반
    {'trnNo': 'K105', 'dptTm': '083000', 'arvTm': '082930', 'exptCd': '1'},  # 급행
    {'trnNo': 'K102', 'dptTm': '081500', 'arvTm': '081430', 'exptCd': '0'},  # 반대 방향
]
ALIGHT = [  # 하차역 (안양)
    {'trnNo': 'K101', 'arvTm': '082500', 'dptTm': '082530'},
    {'trnNo': 'K103', 'arvTm': '084000', 'dptTm': '084030'},
    {'trnNo': 'K105', 'arvTm': '084500', 'dptTm': '084530'},
    {'trnNo': 'K102', 'arvTm': '080000', 'dptTm': '080030'},   # 하차역을 먼저 지남 → 제외돼야 함
]


def subway_route(trips, walk_after=20):
    return [Leg('walk', '집→승차역', 10),
            Leg('subway', '1호선', trips=trips),
            Leg('walk', '안양역→강의실', walk_after)]


def test_join_trips_keeps_only_forward_direction():
    trips = join_trips(BOARD, ALIGHT)
    assert [t.trn_no for t in trips] == ['K101', 'K103', 'K105']
    assert trips[0].board_dep == 8 * 60 + 10 and trips[0].alight_arr == 8 * 60 + 25
    assert [t.is_express for t in trips] == [True, False, True]


def test_ac_03_04_departure_and_prep():
    # 첫 수업 09:00, 여유 15분 → 도착 목표 08:45 (AC-02-03)
    # 08:10 급행 → 08:25 안양 + 20분 = 08:45 OK / 08:30 급행 → 08:45 + 20 = 09:05 늦음
    # 출발 = 08:10 − 3 − 10 = 07:57, 준비 = 07:27 (AC-03-04)
    p = plan_commute('09:00', subway_route(join_trips(BOARD, ALIGHT)), 15, 3, 30)
    assert p.status == 'planned'
    assert p.target_arrival == '08:45'
    assert p.steps[1]['train'] == 'K101'
    assert (p.depart, p.prep_start) == ('07:57', '07:27')


def test_ac_03_02_falls_back_to_local_train():
    only_local = [t for t in join_trips(BOARD, ALIGHT) if not t.is_express]   # K103 (08:20→08:40)
    p = plan_commute('09:10', subway_route(only_local), 0, 3, 30)  # 목표 09:10, 하차 ≤ 08:50
    assert p.status == 'planned' and p.steps[1]['train'] == 'K103'
    assert p.warnings and '일반 열차' in p.warnings[0]


def test_no_trip_when_nothing_fits():
    p = plan_commute('08:30', subway_route(join_trips(BOARD, ALIGHT)), 15, 3, 30)  # 하차 ≤ 07:55
    assert p.status == 'no_trip'


def test_bus_route_uses_worst_case_headway_then_realtime():
    legs = [Leg('walk', '집→정류소', 5), Leg('bus', '마을버스 2', minutes=12, headway_max=10),
            Leg('walk', '정류소→강의실', 5)]
    # 목표 08:45 → 강의실 도보 5 → 08:40 하차 → 승차 08:28 → 최악 대기 10분 → 정류소 08:18 → 출발 08:13
    p = plan_commute('09:00', legs, 15, 3, 30)
    assert p.depart == '08:13' and p.steps[1]['basis'] == 'headway'
    legs[1].realtime_wait = 4    # 당일 실시간: 4분 뒤 도착 → 6분 늦게 나가도 됨
    assert plan_commute('09:00', legs, 15, 3, 30).depart == '08:19'


def test_ac_04_04_alarm_times():
    assert alarm_times('08:00', 3, 5) == ['07:50', '07:55', '08:00']
