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

-- FR-02 경로·시간 설정 (MVP: 사용자당 경로 1개)
CREATE TABLE routes (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             BIGINT      NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    origin_name         VARCHAR(100) NOT NULL,
    mode                VARCHAR(16) NOT NULL CHECK (mode IN ('subway_express', 'bus')),
    line_id             VARCHAR(32) NOT NULL,                 -- 공공 API의 노선 ID
    stop_id             VARCHAR(32) NOT NULL,                 -- 승차역·정류장 ID
    direction           VARCHAR(32) NOT NULL,
    home_to_stop_min    SMALLINT    NOT NULL CHECK (home_to_stop_min   >= 0),
    ride_to_school_min  SMALLINT    NOT NULL DEFAULT 0 CHECK (ride_to_school_min >= 0),
    board_buffer_min    SMALLINT    NOT NULL DEFAULT 3 CHECK (board_buffer_min  >= 0),
    arrive_buffer_min   SMALLINT    NOT NULL CHECK (arrive_buffer_min  >= 0),
    prep_min            SMALLINT    NOT NULL CHECK (prep_min           >= 0),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
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

-- FR-03 교통 시각표 캐시: API 실패 시 유효 기간 내 시각표로 대체 (PRD 3.2 안정성)
-- 사용자와 무관한 공용 데이터라 routes와 FK로 묶지 않고 (line_id, stop_id, direction)으로 조회한다
CREATE TABLE transit_trips (
    id              BIGSERIAL PRIMARY KEY,
    line_id         VARCHAR(32) NOT NULL,
    stop_id         VARCHAR(32) NOT NULL,
    direction       VARCHAR(32) NOT NULL,
    day_type        VARCHAR(8)  NOT NULL CHECK (day_type IN ('weekday', 'sat', 'holiday')),
    departure_time  TIME        NOT NULL,       -- 승차 지점 출발
    arrival_time    TIME        NOT NULL,       -- 학교 쪽 하차
    is_express      BOOLEAN     NOT NULL DEFAULT FALSE,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_until     TIMESTAMPTZ NOT NULL,
    UNIQUE (line_id, stop_id, direction, day_type, departure_time)
);
CREATE INDEX ix_trips_lookup ON transit_trips(line_id, stop_id, direction, day_type, arrival_time);

-- FR-03 결과: 사용자 × 날짜 1행
CREATE TABLE commute_plans (
    id                BIGSERIAL PRIMARY KEY,
    user_id           BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    service_date      DATE        NOT NULL,
    timetable_day_id  BIGINT      REFERENCES timetable_days(id) ON DELETE SET NULL,
    trip_id           BIGINT      REFERENCES transit_trips(id) ON DELETE SET NULL,
    target_arrival    TIME,
    boarding_time     TIME,
    depart_time       TIME,
    prep_start_time   TIME,
    info_basis        VARCHAR(16) CHECK (info_basis IN ('realtime', 'timetable', 'none')),
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
