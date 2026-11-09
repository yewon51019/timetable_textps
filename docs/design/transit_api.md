# 교통 API 결정서 v0.1 — 무엇을, 언제, 어떻게 부르고 어떻게 조합하나

근거: PRD v0.1 FR-02·FR-03, 6.2 "실제 API 선정(1호선 급행·버스), 요청·응답 포맷".
명세는 공공데이터포털·철도산업정보센터(KRIC) 페이지에서 직접 확인했습니다 (2026-11-09).

## 1. 결정 요약

| 구간 | 쓰는 API | 이유 |
|---|---|---|
| **1호선 (급행 포함) 시각표** | **KRIC `trainUseInfo/subwayTimetableExp`** (열차별 운행시각표, 급행 표시) | 응답에 **급행구분 `exptCd`** 와 **열차번호 `trnNo`** 가 있는 유일한 후보. 열차번호로 승차역·하차역 시각을 맞출 수 있음 |
| 1호선 역 코드 | **KRIC `trainUseInfo/subwayRouteInfo`** | 위 API의 `stinCd`(역코드)·`railOprIsttCd`(운영기관) 획득 |
| **버스 정류소·노선 찾기** | **경기도 `busstationservice/v2`** (정류소 조회) | 안양 마을버스·시내버스는 경기도 버스정보 시스템 소관 |
| 버스 경유 정류소 순서 | **경기도 `busrouteservice/v2/getBusRouteStationListv2`** | 승차→하차 정류장 수, 도착정보 조회에 필요한 `staOrder` |
| **버스 배차 (계획용)** | **경기도 `busrouteservice/v2/getBusRouteInfoItemv2`** | 버스는 **정시 시각표가 없음** → 첫차·막차·최대 배차간격으로 계획 |
| **버스 실시간 (당일)** | **경기도 `busarrivalservice/v2/getBusArrivalItemv2`** | 다음 버스 2대 도착 예정(초 단위) |

### 쓰지 않기로 한 후보
| 후보 | 제외 이유 |
|---|---|
| 국토교통부 TAGO 지하철정보 `1613000/SubwayInfo/GetSubwaySttnAcctoSchdulList` | 응답에 **급행 여부·열차번호가 없음** (`depTime`, `arrTime`, `endSubwayStationNm` 뿐) → 급행 구분도, 하차역 시각 연결도 불가. KRIC 장애 시 "일반 열차 시각"용 예비로만 고려 |
| 서울교통공사 역별 시간표 (data.go.kr 15057982 등) | 서울교통공사 운영 구간만 제공. **안양·금정·명학은 한국철도(코레일) 구간** |
| 서울시 실시간 지하철 도착정보 | 급행 표시가 있지만 MVP 범위(시각표 기반 계획)를 넘음 → Should 기능으로 보류 |
| 길찾기(경로 탐색) API | PRD상 사용자가 **선호 경로를 직접 등록**하므로 경로 탐색이 필요 없음. 구간별 API로 충분 |

## 2. 호출 시점별 흐름

```
[설정 화면 — 경로 등록 시 1회]
  지하철: subwayRouteInfo(mreaWideCd=01, lnCd=1) → 역 목록에서 승차역·하차역 선택 → stinCd 저장
  버스  : getBusStationListv2(keyword) → 승차 정류소 선택
          getBusStationViaRouteListv2(stationId) → 노선 선택 (staOrder 같이 저장)
          getBusRouteStationListv2(routeId) → 승차 순번 이후 정류소 중 하차 정류소 선택
          → 승차 시간 기본값 = 정거장 수 × 1.5분 (사용자가 수정 가능)
  걷기  : 사용자가 분 단위로 입력 (집→역, 역→정류소, 정류소→강의실)
  결과는 route_legs 테이블에 구간별로 저장

[스케줄러 — 매일 03:00, 다음 수업일 기준]
  subwayTimetableExp(stinCd=승차역, dayCd) + subwayTimetableExp(stinCd=하차역, dayCd)
    → subway_timetables 캐시 (역·요일구분당 하루 1회면 충분, 시각표는 자주 안 바뀜)
  getBusRouteInfoItemv2(routeId) → bus_routes 캐시 (배차·첫차·막차)
  planner.plan_commute() → commute_plans + notifications

[당일 — 출발 알림 20분 전, 버스 구간이 있을 때만]
  getBusArrivalItemv2(stationId, routeId, staOrder) → 다음 버스 도착 예정
    → 계획보다 버스가 늦게 오면 화면에 "N분 뒤 버스" 안내 (알림 시각은 앞당기기만, 늦추지 않음)
```

### 요청 조건(파라미터) 정리
| 오퍼레이션 | 필수 파라미터 | 값을 어디서 얻나 |
|---|---|---|
| `subwayRouteInfo` | `mreaWideCd=01`(수도권), `lnCd` | 고정값 (1호선) |
| `subwayTimetableExp` | `railOprIsttCd`, `lnCd`, `stinCd`, `dayCd` | 앞 응답 + **dayCd: 8 평일 / 7 토 / 9 휴일** (수업일은 평일 → 8, 공휴일 판정은 미정) |
| `getBusStationListv2` | `keyword` | 사용자 검색어 |
| `getBusStationViaRouteListv2` | `stationId` | 앞 응답 |
| `getBusRouteStationListv2` | `routeId` | 앞 응답 |
| `getBusRouteInfoItemv2` | `routeId` | route_legs |
| `getBusArrivalItemv2` | `stationId`, `routeId`, `staOrder` | route_legs |

공통: KRIC `serviceKey`, `format=json` / 경기도 `serviceKey`(data.go.kr 일반 인증키), `format=json`.
두 기관 모두 키가 **쿼리 파라미터**이므로 서버 환경변수(`KRIC_KEY`, `DATA_GO_KR_KEY`)에 두고 앱에는 절대 넣지 않습니다.

### 응답에서 쓰는 필드
| API | 쓰는 필드 |
|---|---|
| `subwayTimetableExp` | `trnNo`(조인 키), `dptTm`(승차역 출발), `arvTm`(하차역 도착), `exptCd`(급행구분) |
| `getBusRouteInfoItemv2` | `upFirstTime`, `upLastTime`, `peekAlloc`, `nPeekAlloc`(최대 배차 → 최악 대기) |
| `getBusArrivalItemv2` | `predictTimeSec1/2`, `locationNo1/2`, `flag` |

## 3. 조합 방법 — 도착 목표에서 거꾸로 계산

구현: `transit/planner.py` (`plan_commute`), 테스트: `tests/test_planner.py`.

**① 지하철 승차역·하차역 시각 연결.** KRIC 시각표는 역 하나의 모든 열차(상·하행 섞임)를 줍니다. 승차역과 하차역 시각표를 `trnNo`로 조인하고 `하차역 도착 > 승차역 출발`인 열차만 남기면, 가려는 방향 열차만 남습니다. 반대 방향 열차는 하차역을 먼저 지나기 때문입니다. 방향 파라미터가 필요 없습니다.

**② 마지막 구간부터 거꾸로.**
```
t = 첫 수업 − 수업 전 여유                                (도착 목표)
걷기   : t −= 걷는 시간
버스   : t −= 승차 시간 + 대기     대기 = 당일 실시간 값, 없으면 최대 배차간격(최악)
지하철 : 하차 시각 ≤ t 인 급행 중 승차역 출발이 가장 늦은 열차
         (급행이 없으면 일반 열차로 대체하고 경고 — AC-03-02)
         t = 그 열차 출발 − 탑승 여유
출발 = t,  준비 시작 = 출발 − 준비 시간
```

**예시** (첫 수업 09:00, 여유 15분, 집→금정 10분, 탑승 여유 3분, 안양역→강의실 20분, 준비 30분)
```
도착 목표 08:45 → 안양 하차 ≤ 08:25 → 08:10 금정 출발 급행(08:25 안양) 선택
→ 승강장 08:07 → 출발 07:57 → 준비 07:27        (PRD AC-03-04와 일치)
```

버스가 들어간 경로(예: 1호선 급행 → 안양역 → 마을버스 → 안양대)도 같은 규칙으로 구간을 이어 붙입니다. 버스는 시각표가 없어서 계획 단계에서는 **최대 배차간격만큼 기다린다고 가정**하고(늦지 않는 쪽), 당일 실시간 도착 정보로 안내만 보정합니다.

## 4. 실패·대체 처리 (PRD 3.2, FR-03 오류 처리)
| 상황 | 처리 |
|---|---|
| KRIC 호출 실패 | 캐시(`subway_timetables`)의 같은 요일구분 시각표 사용, `info_basis='timetable'` + 캐시 시각 표시 |
| 캐시도 없음 | 계산 보류, "교통정보를 불러올 수 없어요" 안내 (`status='no_trip'`) |
| 버스 배차 정보 실패 | 캐시된 `bus_routes` 사용, 없으면 계산 보류 |
| 버스 실시간 실패 | 배차 기반 계획 그대로 유지 (`info_basis='headway'`) |
| 급행 없음 | 일반 열차 후보로 안내 + 경고 (AC-03-02) |
| 막차 이후·첫차 이전 | 버스 구간 `up_first_time`~`up_last_time` 밖이면 해당 경로 불가 안내 |

## 5. 호출 확인 현황

`python -m tools.check_transit_api --probe` (키 없이) / 키가 있으면 `python -m tools.check_transit_api`

| 항목 | 상태 | 확인 방법 |
|---|---|---|
| 경기도 v2 오퍼레이션 5개 존재 | ✅ 확인 | 가짜 키로 호출 시 `키 미등록` 오류, 대조군(없는 오퍼레이션)은 `NO_OPENAPI_SERVICE_ERROR` |
| TAGO 지하철 시간표 오퍼레이션 존재 | ✅ 확인 | 같은 방법 (실제 경로는 `.../1613000/SubwayInfo/...`) |
| KRIC 엔드포인트 응답 | ⚠️ 부분 | 서버 응답(`resultCode 30 등록되지 않은 서비스키`)은 오지만, 없는 오퍼레이션에도 같은 응답이라 존재 여부는 키 필요 |
| 경로 조합 계산 | ✅ 확인 | 단위 테스트 6개 (AC-02-03, 03-02, 03-04, 04-04) + PostgreSQL 조인 쿼리 |
| **`exptCd` 값의 의미** | ❌ 키 필요 | 명세에 코드 설명 없음. 스크립트가 급행이 서지 않는 **명학역**의 값 분포와 비교해 판정 |
| 금정·안양·명학 `stinCd`, 1호선 `lnCd` 값 | ❌ 키 필요 | `subwayRouteInfo` 응답 |
| 안양역 → 안양대 버스 노선·정류소 ID | ❌ 키 필요 | 커뮤니티 자료상 마을버스 2번(안양역 경유)·10-1번(명학역 경유) 후보 → API로 확인 |
| 실제 JSON 감싸는 구조 | ❌ 키 필요 | 클라이언트는 구조를 유연하게 파싱하도록 작성, 응답 샘플을 `api_samples/`에 저장해 확정 |

### 키 발급 (팀원 1명이 발급해 공유하지 말고 각자 발급 권장)
1. **data.go.kr**: 회원가입 → "경기도_정류소 조회", "경기도_버스노선 조회", "경기도_버스도착정보 조회" 활용신청 → 마이페이지의 **일반 인증키(Decoding)**
2. **KRIC**: openapi.kric.go.kr 회원가입 → "열차별 운행시각표(급행)", 노선 구성역 정보(`subwayRouteInfo`) 활용신청 → 서비스키
3. `.env` 또는 셸에 `DATA_GO_KR_KEY`, `KRIC_KEY` 설정 (깃에 올리지 않기)
