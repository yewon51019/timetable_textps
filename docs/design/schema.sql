-- 대중교통 알리미 AI — 서비스 DB 스키마 v0.1 (PostgreSQL 16 기준)
-- 원칙
--   1) 시간표 이미지는 저장하지 않는다 (PRD 3.2 개인정보: 인식 직후 삭제). 해시·처리 기록만 남긴다.
--   2) 모든 시각은 한국 시간 기준. 하루 안의 시각은 TIME, 특정 순간은 TIMESTAMPTZ.
--   3) 상태값은 CHECK 제약으로 막아서 잘못된 값이 들어가지 않게 한다.

-- 사용자: 로그인 없이 기기 단위 익명 ID
CREATE TABLE users (
    id          BIGSERIAL PRIMARY KEY,
    device_id   VARCHAR(64)  NOT NULL UNIQUE,
    timezone    VARCHAR(32)  NOT NULL DEFAULT 'Asia/Seoul',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- FR-01 시간표: 업로드 1회 = 1행. 새로 확정하면 이전 것은 replaced
CREATE TABLE timetables (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status        VARCHAR(16) NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft', 'confirmed', 'replaced')),
    theme         VARCHAR(16) CHECK (theme IN ('light', 'dark', 'background')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    confirmed_at  TIMESTAMPTZ
);
-- 사용자당 확정 시간표는 하나만
CREATE UNIQUE INDEX uq_timetables_one_confirmed ON timetables(user_id) WHERE status = 'confirmed';

-- 요일별 첫 수업: 시간표 1개당 5행(월~금)
CREATE TABLE timetable_days (
    id                BIGSERIAL PRIMARY KEY,
    timetable_id      BIGINT      NOT NULL REFERENCES timetables(id) ON DELETE CASCADE,
    weekday           SMALLINT    NOT NULL CHECK (weekday BETWEEN 0 AND 4),   -- 0=월 … 4=금
    first_class_time  TIME,
    status            VARCHAR(16) NOT NULL CHECK (status IN ('class', 'free', 'needs_check')),
    source            VARCHAR(8)  NOT NULL DEFAULT 'ocr' CHECK (source IN ('ocr', 'user')),
    confidence        REAL,
    UNIQUE (timetable_id, weekday),
    -- 수업 있음이면 시각 필수, 공강·확인 필요면 시각 없음 (PRD AC-01-03/04)
    CHECK ((status = 'class') = (first_class_time IS NOT NULL))
);

-- FR-02 경로 설정 (MVP: 사용자당 경로 1개). 시간 값은 경로 전체에 공통
CREATE TABLE routes (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             BIGINT      NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    name                VARCHAR(50) NOT NULL DEFAULT '등교',
    arrive_buffer_min   SMALLINT    NOT NULL CHECK (arrive_buffer_min >= 0),   -- 수업 전 여유
    board_buffer_min    SMALLINT    NOT NULL DEFAULT 3 CHECK (board_buffer_min >= 0),  -- 열차 탑승 여유
    prep_min            SMALLINT    NOT NULL CHECK (prep_min >= 0),            -- 준비 시간
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 경로를 이루는 구간: 집 → 학교 순서(seq). 예) 걷기 → 1호선 급행 → 걷기 → 마을버스 → 걷기
-- 설정 화면에서 사용자가 역·정류소·노선을 고르면 API로 얻은 ID를 그대로 저장한다 (transit_api.md 참고)
CREATE TABLE route_legs (
    id                  BIGSERIAL PRIMARY KEY,
    route_id            BIGINT      NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
    seq                 SMALLINT    NOT NULL CHECK (seq >= 1),
    kind                VARCHAR(8)  NOT NULL CHECK (kind IN ('walk', 'subway', 'bus')),
    minutes             SMALLINT    CHECK (minutes >= 0),   -- walk: 걷는 시간 / bus: 승차~하차 시간
    -- subway (KRIC subwayRouteInfo 의 코드)
    rail_opr_cd         VARCHAR(4),                         -- KR = 한국철도
    ln_cd               VARCHAR(4),                         -- 1 = 1호선
    board_stin_cd       VARCHAR(8),
    board_stin_nm       VARCHAR(30),
    alight_stin_cd      VARCHAR(8),
    alight_stin_nm      VARCHAR(30),
    express_only        BOOLEAN     DEFAULT TRUE,
    -- bus (경기도 버스 v2 의 ID)
    bus_route_id        VARCHAR(12),
    bus_route_name      VARCHAR(20),
    board_station_id    VARCHAR(12),
    board_sta_order     SMALLINT,                           -- 도착정보 조회에 필요한 정류소 순번
    alight_station_id   VARCHAR(12),
    UNIQUE (route_id, seq),
    CHECK (kind <> 'walk'   OR minutes IS NOT NULL),
    CHECK (kind <> 'subway' OR (ln_cd IS NOT NULL AND board_stin_cd IS NOT NULL AND alight_stin_cd IS NOT NULL)),
    CHECK (kind <> 'bus'    OR (minutes IS NOT NULL AND bus_route_id IS NOT NULL
                                AND board_station_id IS NOT NULL AND board_sta_order IS NOT NULL))
);

-- FR-04 단계별 알림 설정 (prep, depart 각 1행)
CREATE TABLE alarm_settings (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stage         VARCHAR(8)  NOT NULL CHECK (stage IN ('prep', 'depart')),
    repeat_count  SMALLINT    NOT NULL CHECK (repeat_count >= 1),
    interval_min  SMALLINT    NOT NULL CHECK (interval_min >= 1),
    UNIQUE (user_id, stage)
);

-- FR-03 지하철 시각표 캐시 = KRIC subwayTimetableExp 응답 그대로 (역 1개 × 요일구분)
-- 승차역·하차역 행을 trn_no 로 조인해 "이 열차를 타면 몇 시에 내리는지"를 얻는다
CREATE TABLE subway_timetables (
    rail_opr_cd   VARCHAR(4)  NOT NULL,
    ln_cd         VARCHAR(4)  NOT NULL,
    stin_cd       VARCHAR(8)  NOT NULL,
    day_cd        CHAR(1)     NOT NULL CHECK (day_cd IN ('7', '8', '9')),   -- 7 토 / 8 평일 / 9 휴일
    trn_no        VARCHAR(10) NOT NULL,
    arv_tm        TIME,
    dpt_tm        TIME,
    is_express    BOOLEAN     NOT NULL,     -- exptCd 를 해석한 값
    expt_cd       VARCHAR(4),               -- 원본 코드 (해석 규칙 바뀌면 재계산)
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (rail_opr_cd, ln_cd, stin_cd, day_cd, trn_no)
);

-- FR-03 버스 노선 정보 캐시 = 경기 getBusRouteInfoItemv2 (첫차·막차·배차). 버스는 시각표가 없어 배차로 계획
CREATE TABLE bus_routes (
    bus_route_id   VARCHAR(12) PRIMARY KEY,
    route_name     VARCHAR(20) NOT NULL,
    up_first_time  TIME,
    up_last_time   TIME,
    peek_alloc     SMALLINT,    -- 평일 최소 배차(분)
    npeek_alloc    SMALLINT,    -- 평일 최대 배차(분) → 최악 대기 시간으로 사용
    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- FR-03 결과: 사용자 × 날짜 1행. 구간별 상세(열차번호, 버스 대기 근거)는 steps 에 그대로 저장
CREATE TABLE commute_plans (
    id                BIGSERIAL PRIMARY KEY,
    user_id           BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    service_date      DATE        NOT NULL,
    timetable_day_id  BIGINT      REFERENCES timetable_days(id) ON DELETE SET NULL,
    target_arrival    TIME,
    depart_time       TIME,
    prep_start_time   TIME,
    subway_trn_no     VARCHAR(10),          -- 선택한 열차 (없으면 NULL)
    steps             JSONB,                -- planner.Plan.steps
    info_basis        VARCHAR(16) CHECK (info_basis IN ('realtime', 'timetable', 'headway', 'none')),
    info_fetched_at   TIMESTAMPTZ,              -- 화면에 "정보 기준 시각"으로 표시 (AC-03-03)
    status            VARCHAR(16) NOT NULL DEFAULT 'planned'
                      CHECK (status IN ('planned', 'prep_done', 'departed', 'no_trip', 'free_day', 'needs_check')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, service_date)
);

-- FR-04 알림 예약 목록. (plan, stage, seq) 유일 → 재계산해도 중복 예약 없음 (AC-04-06)
CREATE TABLE notifications (
    id             BIGSERIAL PRIMARY KEY,
    plan_id        BIGINT      NOT NULL REFERENCES commute_plans(id) ON DELETE CASCADE,
    stage          VARCHAR(8)  NOT NULL CHECK (stage IN ('prep', 'depart')),
    seq            SMALLINT    NOT NULL CHECK (seq >= 1),
    scheduled_at   TIMESTAMPTZ NOT NULL,
    status         VARCHAR(12) NOT NULL DEFAULT 'scheduled'
                   CHECK (status IN ('scheduled', 'sent', 'cancelled', 'failed')),
    cancel_reason  VARCHAR(32),                 -- prep_confirmed | departed | recalculated
    UNIQUE (plan_id, stage, seq)
);
CREATE INDEX ix_notifications_due ON notifications(scheduled_at) WHERE status = 'scheduled';

-- 인식 기록: 실패 사례 분석·응답 시간 측정용 (원본 이미지 없음)
CREATE TABLE recognition_logs (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT      REFERENCES users(id) ON DELETE SET NULL,
    timetable_id    BIGINT      REFERENCES timetables(id) ON DELETE SET NULL,
    image_sha256    CHAR(64)    NOT NULL,
    theme           VARCHAR(16),
    processing_ms   INTEGER,
    result_status   VARCHAR(12) NOT NULL CHECK (result_status IN ('ok', 'needs_check', 'error')),
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
