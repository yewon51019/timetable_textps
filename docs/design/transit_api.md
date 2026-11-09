# 교통 API 결정서 v0.2 — 무엇을, 언제, 어떻게 부르고 어떻게 조합하나

근거: PRD v0.1 FR-02·FR-03, 6.2 "실제 API 선정(1호선 급행·버스), 요청·응답 포맷".
명세는 공공데이터포털·철도산업정보센터(KRIC)·ODsay 레퍼런스 페이지와 서울시 활용가이드에서 직접 확인했습니다 (2026-11-09).

> v0.2 변경: 출발지가 사용자 지정 장소(서울·경기 어디든)일 수 있어서 **경로 검색은 ODsay**, **서울 버스 API 추가**, 버스 구간마다 서울/경기 분기 (6절).

## 1. 결정 요약

| 구간 | 쓰는 API | 이유 |
|---|---|---|
| **1호선 (급행 포함) 시각표** | **KRIC `trainUseInfo/subwayTimetableExp`** (열차별 운행시각표, 급행 표시) | 응답에 **급행구분 `exptCd`** 와 **열차번호 `trnNo`** 가 있는 유일한 후보. 열차번호로 승차역·하차역 시각을 맞출 수 있음 |
| 지하철 (1호선 외 노선) 시각표 | 같은 KRIC `subwayTimetableExp` | 수도권 운영기관 전체(코레일·서울교통공사 등)를 `railOprIsttCd`로 구분해 같은 방식으로 조회 |
| 지하철 역 코드 | **KRIC `trainUseInfo/subwayRouteInfo`** | 위 API의 `stinCd`(역코드)·`railOprIsttCd`(운영기관) 획득 |
| **경로 검색 (등록 시 1회)** | **ODsay `searchPubTransPathT`** | 좌표 → 경로 후보. 구간마다 **지역 BIS의 노선ID·정류소ID**(`busLocalBlID`, `startLocalStationID`)를 함께 줘서 아래 기관 API로 바로 이어짐 |
| **서울 버스 배차·실시간** | **서울시 `busRouteInfo/getRouteInfo`, `getStaionByRoute`, `arrive/getArrInfoByRoute`** | 서울 시내버스 구간 (ODsay `busCityCode = 1000`) |
| **버스 정류소·노선 찾기 (경로 수동 수정용)** | **경기도 `busstationservice/v2`** (정류소 조회) | 안양 마을버스·시내버스는 경기도 버스정보 시스템 소관 |
| 버스 경유 정류소 순서 | **경기도 `busrouteservice/v2/getBusRouteStationListv2`** | 승차→하차 정류장 수, 도착정보 조회에 필요한 `staOrder` |
| **버스 배차 (계획용)** | **경기도 `busrouteservice/v2/getBusRouteInfoItemv2`** | 버스는 **정시 시각표가 없음** → 첫차·막차·최대 배차간격으로 계획 |
| **버스 실시간 (당일)** | **경기도 `busarrivalservice/v2/getBusArrivalItemv2`** | 다음 버스 2대 도착 예정(초 단위) |

### 쓰지 않기로 한 후보
| 후보 | 제외 이유 |
|---|---|
| 국토교통부 TAGO 지하철정보 `1613000/SubwayInfo/GetSubwaySttnAcctoSchdulList` | 응답에 **급행 여부·열차번호가 없음** (`depTime`, `arrTime`, `endSubwayStationNm` 뿐) → 급행 구분도, 하차역 시각 연결도 불가. KRIC 장애 시 "일반 열차 시각"용 예비로만 고려 |
| 서울교통공사 역별 시간표 (data.go.kr 15057982 등) | 서울교통공사 운영 구간만 제공. **안양·금정·명학은 한국철도(코레일) 구간** |
| 서울시 실시간 지하철 도착정보 | 급행 표시가 있지만 MVP 범위(시각표 기반 계획)를 넘음 → Should 기능으로 보류 |
| 서울시 대중교통환승경로 `pathinfo/getPathInfoByBusNSub` | 공공·무료지만 응답이 구간별 `routeId·fid·tid` 뿐이고 **도보 시간·노선 소속 구분이 없음**. 서울 버스정보 기반이라 경기 마을버스 포함 여부도 불확실 → ODsay 장애 시 예비 |
| 길찾기를 매일 호출 | ODsay는 **경로 등록·변경 때만** 호출. 매일 계산은 구간별 기관 API(시각표·배차·실시간)로 → 호출량이 적어 무료 등급으로 충분할 가능성이 큼 (정확한 무료 한도는 ODsay 콘솔에서 확인) |

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
1. **data.go.kr**: 회원가입 → "경기도_정류소 조회", "경기도_버스노선 조회", "경기도_버스도착정보 조회", "서울특별시_노선정보조회 서비스", "서울특별시_버스도착정보조회 서비스" 활용신청 → 마이페이지의 **일반 인증키(Decoding)**
2. **KRIC**: openapi.kric.go.kr 회원가입 → "열차별 운행시각표(급행)", 노선 구성역 정보(`subwayRouteInfo`) 활용신청 → 서비스키
3. **ODsay**: lab.odsay.com 가입 → 애플리케이션 등록, 플랫폼 "서버"에 호출할 서버 IP 등록 → API Key
4. `.env` 또는 셸에 `DATA_GO_KR_KEY`, `KRIC_KEY`, `ODSAY_KEY` 설정 (깃에 올리지 않기)

## 6. 경로 검색과 서울·경기 분기 (v0.2 추가)

### 6.1 경로 검색은 ODsay로, 한 번만
```
① 출발지: 사용자가 지도에서 찍거나 장소를 검색 → 좌표(경도, 위도)
   (장소 검색은 앱의 지도 SDK 또는 카카오 로컬 API 등 — 좌표만 얻으면 됨)
② 도착지: 안양대 좌표 고정
③ ODsay searchPubTransPathT(SX, SY, EX, EY, SearchPathType=0) → 경로 후보 목록
④ 사용자가 후보 하나 선택 → transit/route_search.py 의 legs_from_path() 로 route_legs 생성
⑤ 지하철 구간: KRIC subwayRouteInfo 로 역 이름 → stinCd·운영기관 코드 채움 (resolve_subway_codes)
   버스 구간  : 노선 경유정류소 API로 승차 정류소 순번(staOrder / ord) 채움 (find_sta_order)
```
- ODsay 키는 **서버 IP 등록 방식**(플랫폼 '서버', 최대 5개)이라 FastAPI 서버에서만 호출합니다.
- ODsay `subPath[]` 구간: `trafficType` 1 지하철 / 2 버스 / 3 도보, `sectionTime`(분), `stationCount`, `intervalTime`(평균 배차)
  - 버스: `lane[].busNo`, `busLocalBlID`(지역 노선ID), `busCityCode`, `startLocalStationID`, `endLocalStationID`, `startArsID`
  - 지하철: `lane[].name`, `subwayCode`, `startName`, `endName`, `wayCode`(1 상행 / 2 하행)

### 6.2 버스 구간: 어느 기관 API를 부를지는 **노선 소속**으로 정한다
ODsay 레퍼런스: *"수도권: busCityCode 가 1000 인 경우 서울, 그 외에는 경기 실시간 사용"*.
**정류소가 서울에 있느냐가 아니라 그 노선이 어느 BIS 소속이냐**가 기준입니다.

| 경우 | 예 | 부를 API |
|---|---|---|
| 서울 버스, 서울 안에서만 | 서울 집 → 사당역 | 서울 |
| 서울 버스가 경기로 들어옴 | 서울 시내버스로 안양 진입 | **서울** (`busCityCode = 1000`) |
| 경기 버스가 서울에서 출발 | 사당역(서울)에서 경기 광역·시내버스 탑승 | **경기** (`busCityCode ≠ 1000`) |
| 경기 버스, 경기 안에서만 | 안양역 → 안양대 마을버스 | 경기 |

| 단계 | 경기 (`bus_provider = gyeonggi`) | 서울 (`bus_provider = seoul`) |
|---|---|---|
| 노선ID | ODsay `busLocalBlID` = 경기 `routeId` | ODsay `busLocalBlID` = 서울 `busRouteId` |
| 정류소 순번 (등록 시) | `getBusRouteStationListv2(routeId)` → `stationSeq` | `getStaionByRoute(busRouteId)` → `seq` (`station` = 정류소 ID) |
| 배차 (매일) | `getBusRouteInfoItemv2(routeId)` → `peekAlloc`, `nPeekAlloc` | `getRouteInfo(busRouteId)` → `term` |
| 실시간 (당일) | `getBusArrivalItemv2(stationId, routeId, staOrder)` → `predictTimeSec1/2` | `getArrInfoByRoute(stId, busRouteId, ord)` → `exps1/2`(초) |
| 키 | data.go.kr 일반 인증키 (서울·경기 같은 키, 서비스별 활용신청) | 〃 |
| 프로토콜 | HTTPS | **HTTP만** (`ws.bus.go.kr`) |

순환 노선은 같은 정류소가 두 번 나오므로, **하차 정류소보다 앞에 있는 순번**을 고릅니다 (`find_sta_order`).

### 6.3 지하철 구간: 서울·경기 구분 없이 KRIC 하나로
KRIC는 수도권 운영기관을 모두 다루고, 역마다 `railOprIsttCd`(KR 한국철도, S1 서울교통공사 등)가 붙습니다. 그래서 지하철 구간마다:
```
승차역 (운영기관 A, stinCd) 시각표 + 하차역 (운영기관 B, stinCd) 시각표 → trnNo 로 조인
```
- **환승**은 ODsay가 지하철 구간을 따로 끊어 주므로(예: 4호선 사당→금정, 도보 2분, 1호선 금정→안양) 구간마다 위 조인을 하고, 사이의 도보 구간을 그대로 이어 붙입니다.
- **같은 노선에서 운영기관이 바뀌는 구간**(예: 1호선 서울역 쪽 서울교통공사 → 코레일, 4호선 서울 구간 → 과천선)은 승차역·하차역 운영기관이 다를 수 있어서 `route_legs`에 `board_rail_opr_cd`, `alight_rail_opr_cd`를 따로 둡니다.
  - ⚠ 운영기관이 다른 두 역의 `trnNo`가 같은 열차를 같은 번호로 부르는지는 **키 발급 후 확인 필요**. 다르면 그 구간은 "승차역 출발 + ODsay `sectionTime`"으로 하차 시각을 추정하는 방식으로 대체합니다.
- 급행 우선(`express_only`)은 MVP에서 1호선만 켭니다.

### 6.4 예시 두 가지
**서울 → 경기** (서울 집 → 서울 버스 → 사당 → 4호선 → 금정 → 1호선 급행 → 안양 → 경기 마을버스 → 안양대)
```
등록 시  ODsay 1회 → 구간 8개
         KRIC subwayRouteInfo (4호선·1호선 역코드), 서울 getStaionByRoute, 경기 getBusRouteStationListv2
매일     KRIC subwayTimetableExp × 4역 (사당, 금정[4호선], 금정[1호선], 안양)
         서울 getRouteInfo, 경기 getBusRouteInfoItemv2
당일     서울 getArrInfoByRoute (첫 버스), 경기 getBusArrivalItemv2 (마을버스)
계산     안양대 도착 목표에서 거꾸로: 도보 → 마을버스(대기=최대 배차) → 도보 → 1호선 열차 선택 → 도보 → 4호선 열차 선택 → 도보 → 서울 버스(대기) → 도보
```
**경기 → 경기** (경기 집 → 경기 버스 → 안양대)
```
등록 시  ODsay 1회, 경기 getBusRouteStationListv2
매일     경기 getBusRouteInfoItemv2
당일     경기 getBusArrivalItemv2
```

### 6.5 확인 현황 (v0.2 추가분)
| 항목 | 상태 |
|---|---|
| ODsay `searchPubTransPathT` 엔드포인트 | ✅ 존재 (가짜 키 → `ApiKeyAuthFailed`) |
| ODsay 응답 필드·서울/경기 구분 규칙 | ✅ 레퍼런스 문서에서 확인 |
| 서울 버스 API 3종 파라미터·응답 필드 | ✅ 활용가이드(docx)에서 확인 / ❌ 호출은 키 필요 + HTTP 전용이라 이 개발 환경 프록시로는 불가 |
| ODsay `busLocalBlID`가 실제 경기 `routeId`와 같은 값인지 | ❌ 키 필요 (문서상 "경기 실시간 사용") |
| 운영기관이 다른 역 사이 `trnNo` 일치 여부 | ❌ 키 필요 |
| 변환·분기 로직 | ✅ 단위 테스트 5개 (`tests/test_route_search.py`) |
