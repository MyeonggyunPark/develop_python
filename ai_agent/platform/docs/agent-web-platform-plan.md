# 나만의 에이전트 웹 플랫폼 설계안

## 문서 목적
- 여러 자동화 에이전트를 한 웹에서 등록, 실행, 모니터링, 운영하기 위한 상위 플랫폼 설계를 정의한다.
- `agents/webtoon/docs/project-plan.md`는 웹툰 자동화 에이전트 단일 설계 문서로 유지하고, 이 문서는 공통 웹 플랫폼 설계를 다룬다.

## 현재 구현 메모
- 현재 구현은 `FastAPI API 서버 + React 프론트엔드` 구조를 기준으로 진행한다.
- 과거 서버사이드 템플릿 예시는 더 이상 구현 기준이 아니며, 화면 라우팅은 `platform/frontend`에서 담당한다.

## 목표
- 하나의 웹에서 여러 자동화 에이전트를 메뉴 단위로 관리한다.
- 대시보드에서 전체 에이전트 상태, 최근 실행 이력, API 사용량, 비용을 한눈에 확인할 수 있다.
- 새로운 자동화 에이전트를 계속 추가해도 같은 운영 구조와 UI 패턴으로 확장 가능해야 한다.

## 1차 구현 범위
- 웹 플랫폼 1차 버전은 내부 운영용 관리 화면으로 시작한다.
- 인증은 초기에는 단일 운영자 기준으로 단순화하고, 이후 다중 사용자 권한으로 확장한다.
- 첫 번째 연동 대상은 `웹툰 자동화 에이전트` 1개로 제한한다.
- 공통 기능은 아래 범위까지 우선 구현한다.
  - Dashboard
  - 에이전트 목록 및 메뉴
  - 에이전트 상세 화면
  - 실행 이력 조회
  - 비용/사용량 집계
  - 수동 실행 트리거
  - 기본 설정 화면

## 권장 기술 방향
### 웹 스택
- 백엔드: `FastAPI`
- 프론트엔드: `React + TypeScript + Vite`
- 스타일링: React 컴포넌트 기반 CSS 또는 추후 도입할 UI 시스템
- 데이터 저장: `SQLite`로 시작하고 이후 `PostgreSQL`로 확장 가능하게 설계
- 스케줄 실행 연동: 기존 Python 에이전트 실행기와 직접 연결

### 기술 선택 이유
- 백엔드는 Python 중심으로 유지하면서 프론트엔드는 독립적으로 확장할 수 있다.
- Dashboard, 실행 이력, 승인 플로우, 비용 차트는 SPA 구조가 상호작용 확장에 유리하다.
- API 계층을 먼저 분리해두면 프론트 배포와 백엔드 배포를 독립적으로 운영할 수 있다.

## 정보 구조 및 화면 설계
### 전역 메뉴 구조
- `Dashboard`
- `Agents`
- `Settings`

### 공통 UI 원칙
- 좌측 사이드바 + 상단 헤더 + 메인 콘텐츠 구조를 기본 레이아웃으로 사용한다.
- 상태는 색상 배지로 통일해서 표시한다.
  - `idle`, `disabled`: 회색
  - 진행 중 상태: 파란색
  - `approved`, `posted`, 성공 상태: 초록색
  - `failed`, `rejected`, `skipped`: 빨간색 또는 주황색
- 비용과 사용량은 항상 `요약 카드 + 차트 + 상세 표` 조합으로 보여준다.
- 표는 기본적으로 검색, 정렬, 기간 필터, 상태 필터를 제공한다.
- Dashboard와 상세 페이지 모두 "최근 실행", "최근 실패", "최근 비용" 3가지를 빠르게 확인할 수 있어야 한다.

### 전역 레이아웃
- 좌측 사이드바
  - 서비스 이름
  - `Dashboard`
  - `Agents`
  - `Settings`
- 상단 헤더
  - 페이지 제목
  - 기간 필터
  - 알림 아이콘
  - 운영자 메뉴
- 메인 콘텐츠
  - 요약 카드 영역
  - 차트 영역
  - 표, 로그, 상세 패널 영역

### 공통 컴포넌트
- `StatusBadge`
- `SummaryCard`
- `FilterBar`
- `RunTable`
- `CostChartCard`
- `UsageChartCard`
- `LogTimeline`
- `ArtifactList`
- `AgentActionPanel`

### 페이지 목록
- `/`
  - Dashboard
- `/agents`
  - 전체 에이전트 목록
- `/agents/{agent_slug}`
  - 에이전트 상세 페이지
- `/agents/{agent_slug}/runs`
  - 실행 이력 목록
- `/runs/{run_id}`
  - 실행 상세 로그와 산출물
- `/costs`
  - 전체 비용 및 사용량 페이지
- `/settings`
  - 공통 설정 페이지

### 프론트엔드 페이지 라우트 구조
- `/`
  - Dashboard 페이지
- `/agents`
  - 전체 에이전트 목록 페이지
- `/agents/{agent_slug}`
  - 에이전트 상세 Overview 페이지
- `/agents/{agent_slug}/runs`
  - 에이전트 실행 이력 목록 페이지
- `/runs/{run_id}`
  - 실행 상세 페이지
- `/costs`
  - 비용 및 사용량 페이지
- `/settings`
  - 플랫폼 설정 페이지

### URL별 API 라우트 구조
- `GET /api/dashboard/summary`
  - Dashboard KPI 카드 데이터 반환
- `GET /api/dashboard/costs`
  - Dashboard 비용 차트 데이터 반환
- `GET /api/dashboard/recent-runs`
  - 최근 실행 목록 반환
- `GET /api/agents`
  - 등록된 에이전트 목록 반환
- `GET /api/agents/{agent_slug}`
  - 단일 에이전트 상세 정보 반환
- `POST /api/agents/{agent_slug}/runs`
  - 수동 실행 트리거
- `GET /api/agents/{agent_slug}/runs`
  - 에이전트 실행 이력 반환
- `GET /api/runs/{run_id}`
  - 실행 상세 데이터 반환
- `GET /api/runs/{run_id}/logs`
  - 실행 로그 반환
- `GET /api/runs/{run_id}/artifacts`
  - 실행 산출물 반환
- `GET /api/costs/summary`
  - 전체 비용 요약 반환
- `GET /api/costs/by-agent`
  - 에이전트별 비용 집계 반환
- `GET /api/costs/by-provider`
  - provider별 비용 집계 반환

### Dashboard 상세 구성
- 1행: KPI 카드
  - 총 에이전트 수
  - 활성 에이전트 수
  - 최근 7일 총 실행 수
  - 최근 7일 실패 수
  - 최근 7일 총 비용
- 2행: 차트
  - 일별 비용 추이
  - 에이전트별 비용 비중
- 3행: 실행 현황 패널
  - 최근 실행 목록
  - 실패 실행 목록
  - 승인 대기 실행 목록
- 4행: 사용량 카드
  - LLM 사용량
  - 이미지 생성 사용량
  - OCR 사용량
  - Google API 사용량

### Dashboard 사용자 액션
- 기간 변경
- 에이전트별 필터 적용
- 실패 실행 클릭 후 실행 상세 페이지 이동
- 비용 카드 클릭 후 비용 페이지 이동
- 승인 대기 항목 클릭 후 에이전트 상세 페이지 이동

### Agents 목록 페이지
- 목적
  - 등록된 모든 자동화 에이전트를 한 번에 확인한다.
- 이 페이지에서 현재 등록된 에이전트 목록을 보고, 각 항목을 클릭해 상세 페이지로 이동한다.
- 표시 항목
  - 에이전트 이름
  - 설명
  - 상태
  - 최근 실행 시각
  - 최근 7일 실행 수
  - 최근 7일 실패 수
  - 최근 30일 비용
  - 상세 보기 버튼

### 에이전트 상세 화면 공통 탭 구조
- `Overview`
- `Runs`
- `Costs`
- `Artifacts`
- `Settings`

### 에이전트 접근 흐름
1. 사용자는 좌측 메뉴에서 `Agents`를 클릭한다.
2. `Agents` 페이지에서 현재 등록된 에이전트 목록을 확인한다.
3. 목록에서 특정 에이전트를 클릭하면 해당 에이전트 상세 페이지로 이동한다.
4. 에이전트 상세 페이지에서 상태 확인, 실행 이력 조회, 비용 확인, 수동 실행을 수행한다.

### 에이전트 상세 화면 공통 구성
- 에이전트 개요
- 현재 상태
- 최근 실행 목록
- 실행 상세 로그
- 입력 설정
- 출력 산출물 링크
- 비용, 토큰, API 사용량
- 수동 실행 버튼
- 스케줄 설정

### 에이전트 상세 화면 탭별 구성
#### Overview
- 상태 카드
- 최근 실행 요약
- 최근 실패 요약
- 최근 비용 카드
- 바로 실행 버튼
- 최신 산출물 미리보기

#### Runs
- 실행 목록 표
- 상태, 기간, trigger_type 필터
- 각 실행의 상세 보기 링크

#### Costs
- 기간별 총 비용 카드
- provider별 비용 차트
- service_type별 사용량 표
- 실행 단위 비용 표

#### Artifacts
- 최근 산출물 목록
- 미리보기 가능한 이미지
- JSON 다운로드 링크
- 외부 저장 링크

#### Settings
- 에이전트 활성/비활성
- 스케줄 설정
- 알림 채널 설정
- 에이전트별 기본 파라미터 설정

### 웹툰 자동화 에이전트 상세 화면 추가 요소
- 이번 주 주제 입력 폼
- `"주제 추천해줘"` 실행 버튼
- 추천 후보 리스트
- 스크립트 승인 또는 반려 UI
- 최종 승인 및 게시 상태 카드
- 최신 산출물 미리보기

### 웹툰 자동화 에이전트 전용 페이지 흐름
- `Overview`
  - 이번 주 상태 카드
  - 이번 주 주제 입력
  - 추천 요청 버튼
  - 승인 대기 요약
- `Topic Planning`
  - 직접 주제 입력
  - 추천 후보 리스트
  - 후보 선택 버튼
  - 중복 점수 및 제외 근거
- `Script Review`
  - 4컷 스크립트 카드
  - 컷별 대사와 장면 설명
  - 승인, 재생성, 직접 수정 액션
- `Publish`
  - 최종 승인 이미지 미리보기
  - 게시 상태
  - 게시 결과 링크
  - 지연 알림 여부

### 실행 상세 페이지(`/runs/{run_id}`)
- 상단 요약
  - 에이전트 이름
  - 상태
  - 시작 시각
  - 종료 시각
  - 총 소요 시간
  - 총 비용
- 오류 정보
  - `error_stage`
  - `error_type`
  - `error_message`
- 단계별 타임라인
- provider별 비용 상세
- 산출물 패널
- 구조화 로그 패널

### 비용 페이지(`/costs`)
- 요약 카드
  - 오늘 비용
  - 최근 7일 비용
  - 최근 30일 비용
  - 예상 월 비용
- 차트
  - 일별 총 비용
  - 에이전트별 비용
  - provider별 비용
- 상세 표
  - 날짜
  - 에이전트
  - provider
  - service_type
  - usage_amount
  - cost_amount
  - run_id

### 설정 페이지(`/settings`)
- 플랫폼 공통 설정
  - 기본 타임존
  - 비용 통화
  - 기본 알림 채널
- 외부 서비스 설정
  - OpenAI
  - 이미지 생성 API
  - OCR API
  - Google API
  - Instagram API
- 에이전트 등록 관리
  - 새 에이전트 등록
  - 활성/비활성 관리
  - 메뉴 노출 순서

### 반응형 기준
- 데스크톱 우선 설계
- 태블릿에서는 사이드바를 축소형으로 전환
- 모바일에서는 카드와 표를 세로 스택으로 재배치
- 긴 표와 로그는 접이식 섹션으로 제공

## 백엔드 구조
### 핵심 모듈
- `platform/app/main.py`
  - FastAPI 엔트리포인트
- `platform/app/api/`
  - JSON API 라우트
- `platform/app/services/`
  - 에이전트 실행, 비용 집계, 대시보드 집계 로직
- `platform/app/repositories/`
  - DB 접근 계층
- `platform/frontend/`
  - React 프론트엔드 애플리케이션

### 라우트 파일 구조
```text
platform/app/api/
  dashboard.py
  agents.py
  runs.py
  costs.py
platform/frontend/src/
  App.tsx
  lib/api.ts
  styles.css
```

### 공통 서비스 역할
- `agent_registry_service`
  - 등록된 에이전트 목록 관리
- `agent_run_service`
  - 수동 실행, 상태 조회, 실행 로그 조회
- `cost_summary_service`
  - provider별 사용량과 비용 집계
- `dashboard_service`
  - Dashboard 카드/차트 데이터 생성
- `settings_service`
  - 공통 설정값 조회 및 저장

## 공통 API 초안
### Agent API
- `GET /api/agents`
- `GET /api/agents/{agent_slug}`
- `POST /api/agents/{agent_slug}/runs`
- `GET /api/agents/{agent_slug}/runs`

### Run API
- `GET /api/runs/{run_id}`
- `GET /api/runs/{run_id}/logs`
- `GET /api/runs/{run_id}/artifacts`

### Dashboard API
- `GET /api/dashboard/summary`
- `GET /api/dashboard/costs`
- `GET /api/dashboard/recent-runs`

### Cost API
- `GET /api/costs/summary`
- `GET /api/costs/by-agent`
- `GET /api/costs/by-provider`

## 플랫폼 공통 데이터 모델
### Agent
- `agent_id`
- `agent_name`
- `agent_slug`
- `description`
- `status`
- `is_enabled`
- `created_at`
- `updated_at`

### AgentRun
- `run_id`
- `agent_id`
- `status`
- `started_at`
- `ended_at`
- `trigger_type`
- `error_stage`
- `error_message`
- `total_duration_ms`
- `total_cost`

### AgentArtifact
- `artifact_id`
- `run_id`
- `artifact_type`
- `file_path`
- `file_url`
- `version`
- `created_at`

### AgentCost
- `cost_id`
- `agent_id`
- `run_id`
- `provider`
- `service_type`
- `usage_unit`
- `usage_amount`
- `cost_amount`
- `recorded_at`

### PlatformSetting
- `setting_key`
- `setting_value`
- `updated_at`

## DB 스키마 초안
### 설계 원칙
- 1차 버전은 `SQLite`를 사용하되, 스키마는 이후 `PostgreSQL`로 무리 없이 이전 가능하게 설계한다.
- 공통 식별자는 문자열 UUID 또는 slug 기반 키를 사용한다.
- 실행 이력, 비용 이력, 산출물 이력은 삭제보다 누적 저장을 우선한다.
- 필터와 집계가 자주 필요한 컬럼은 별도 컬럼으로 두고, 부가 정보는 JSON 컬럼으로 저장한다.

### `agents`
- 목적
  - 등록된 자동화 에이전트의 기본 정보를 저장한다.
- 컬럼
  - `agent_id` TEXT PRIMARY KEY
  - `agent_slug` TEXT NOT NULL UNIQUE
  - `agent_name` TEXT NOT NULL
  - `description` TEXT
  - `status` TEXT NOT NULL
  - `is_enabled` INTEGER NOT NULL DEFAULT 1
  - `menu_order` INTEGER NOT NULL DEFAULT 0
  - `agent_type` TEXT NOT NULL
  - `owner` TEXT
  - `created_at` DATETIME NOT NULL
  - `updated_at` DATETIME NOT NULL
- 인덱스
  - UNIQUE(`agent_slug`)
  - INDEX(`is_enabled`, `menu_order`)

### `agent_runs`
- 목적
  - 각 에이전트 실행 단위를 저장한다.
- 컬럼
  - `run_id` TEXT PRIMARY KEY
  - `agent_id` TEXT NOT NULL
  - `status` TEXT NOT NULL
  - `trigger_type` TEXT NOT NULL
  - `started_at` DATETIME
  - `ended_at` DATETIME
  - `total_duration_ms` INTEGER
  - `total_cost` REAL NOT NULL DEFAULT 0
  - `error_stage` TEXT
  - `error_type` TEXT
  - `error_message` TEXT
  - `summary_json` TEXT
  - `created_at` DATETIME NOT NULL
  - `updated_at` DATETIME NOT NULL
- 관계
  - FOREIGN KEY(`agent_id`) REFERENCES `agents`(`agent_id`)
- 인덱스
  - INDEX(`agent_id`, `started_at`)
  - INDEX(`agent_id`, `status`)
  - INDEX(`status`, `started_at`)

### `agent_run_logs`
- 목적
  - 실행 중 구조화 로그를 단계별로 저장한다.
- 컬럼
  - `log_id` TEXT PRIMARY KEY
  - `run_id` TEXT NOT NULL
  - `agent_id` TEXT NOT NULL
  - `stage` TEXT NOT NULL
  - `status` TEXT NOT NULL
  - `message` TEXT
  - `error_type` TEXT
  - `duration_ms` INTEGER
  - `log_data_json` TEXT
  - `created_at` DATETIME NOT NULL
- 관계
  - FOREIGN KEY(`run_id`) REFERENCES `agent_runs`(`run_id`)
  - FOREIGN KEY(`agent_id`) REFERENCES `agents`(`agent_id`)
- 인덱스
  - INDEX(`run_id`, `created_at`)
  - INDEX(`agent_id`, `created_at`)

### `agent_artifacts`
- 목적
  - 실행 결과 산출물과 링크를 저장한다.
- 컬럼
  - `artifact_id` TEXT PRIMARY KEY
  - `run_id` TEXT NOT NULL
  - `agent_id` TEXT NOT NULL
  - `artifact_type` TEXT NOT NULL
  - `artifact_name` TEXT NOT NULL
  - `version` INTEGER NOT NULL DEFAULT 1
  - `file_path` TEXT
  - `file_url` TEXT
  - `mime_type` TEXT
  - `metadata_json` TEXT
  - `created_at` DATETIME NOT NULL
- 관계
  - FOREIGN KEY(`run_id`) REFERENCES `agent_runs`(`run_id`)
  - FOREIGN KEY(`agent_id`) REFERENCES `agents`(`agent_id`)
- 인덱스
  - INDEX(`run_id`, `artifact_type`)
  - INDEX(`agent_id`, `artifact_type`)

### `agent_costs`
- 목적
  - provider 및 service_type별 사용량과 비용을 저장한다.
- 컬럼
  - `cost_id` TEXT PRIMARY KEY
  - `agent_id` TEXT NOT NULL
  - `run_id` TEXT
  - `provider` TEXT NOT NULL
  - `service_type` TEXT NOT NULL
  - `usage_unit` TEXT NOT NULL
  - `usage_amount` REAL NOT NULL DEFAULT 0
  - `cost_amount` REAL NOT NULL DEFAULT 0
  - `currency` TEXT NOT NULL DEFAULT 'USD'
  - `recorded_at` DATETIME NOT NULL
  - `metadata_json` TEXT
- 관계
  - FOREIGN KEY(`agent_id`) REFERENCES `agents`(`agent_id`)
  - FOREIGN KEY(`run_id`) REFERENCES `agent_runs`(`run_id`)
- 인덱스
  - INDEX(`agent_id`, `recorded_at`)
  - INDEX(`provider`, `recorded_at`)
  - INDEX(`service_type`, `recorded_at`)

### `platform_settings`
- 목적
  - 플랫폼 전역 설정을 key-value 형태로 저장한다.
- 컬럼
  - `setting_key` TEXT PRIMARY KEY
  - `setting_value` TEXT NOT NULL
  - `setting_type` TEXT NOT NULL DEFAULT 'string'
  - `description` TEXT
  - `updated_at` DATETIME NOT NULL

### `agent_settings`
- 목적
  - 에이전트별 설정값을 저장한다.
- 컬럼
  - `agent_setting_id` TEXT PRIMARY KEY
  - `agent_id` TEXT NOT NULL
  - `setting_key` TEXT NOT NULL
  - `setting_value` TEXT NOT NULL
  - `setting_type` TEXT NOT NULL DEFAULT 'string'
  - `updated_at` DATETIME NOT NULL
- 관계
  - FOREIGN KEY(`agent_id`) REFERENCES `agents`(`agent_id`)
- 인덱스
  - UNIQUE(`agent_id`, `setting_key`)

### `notifications`
- 목적
  - 플랫폼 알림 이력을 저장한다.
- 컬럼
  - `notification_id` TEXT PRIMARY KEY
  - `agent_id` TEXT
  - `run_id` TEXT
  - `channel` TEXT NOT NULL
  - `notification_type` TEXT NOT NULL
  - `title` TEXT NOT NULL
  - `message` TEXT NOT NULL
  - `is_sent` INTEGER NOT NULL DEFAULT 0
  - `sent_at` DATETIME
  - `created_at` DATETIME NOT NULL
- 관계
  - FOREIGN KEY(`agent_id`) REFERENCES `agents`(`agent_id`)
  - FOREIGN KEY(`run_id`) REFERENCES `agent_runs`(`run_id`)
- 인덱스
  - INDEX(`agent_id`, `created_at`)
  - INDEX(`run_id`, `created_at`)
  - INDEX(`notification_type`, `created_at`)

### 테이블 관계 요약
- `agents` 1:N `agent_runs`
- `agent_runs` 1:N `agent_run_logs`
- `agent_runs` 1:N `agent_artifacts`
- `agent_runs` 1:N `agent_costs`
- `agents` 1:N `agent_settings`
- `agents` 1:N `notifications`

### JSON 컬럼 사용 원칙
- `summary_json`
  - 실행 요약 데이터 저장
- `log_data_json`
  - 단계별 부가 로그 데이터 저장
- `metadata_json`
  - 산출물 메타데이터 또는 비용 부가 정보 저장
- JSON 컬럼은 검색 핵심 조건으로 사용하지 않고, 주요 조회 필드는 별도 컬럼으로 분리한다.

## 비용 및 사용량 대시보드 요구사항
- 에이전트별 API 사용량을 provider 단위로 집계한다.
- 비용은 실행 단위와 일/주/월 단위로 집계한다.
- 최소 표시 항목:
  - `OpenAI/LLM 사용량`
  - `이미지 생성 API 사용량`
  - `OCR/Vision API 사용량`
  - `Google API 호출량`
  - `총 비용`
- 비용 차트는 전체 합계와 에이전트별 상세를 모두 제공한다.

## 비용 집계 규칙
- 비용 데이터는 `run_id` 기준으로 먼저 저장하고, Dashboard에서는 집계해서 보여준다.
- 한 실행에서 여러 provider를 사용할 수 있으므로 비용은 provider별 row로 저장한다.
- Dashboard 표시는 아래 단위로 제공한다.
  - 오늘
  - 최근 7일
  - 최근 30일
  - 에이전트별 누적
- 비용 카드에는 `총 비용`과 함께 `예상 월 비용`도 표시한다.

## 확장 원칙
- 새 에이전트는 공통 `Agent`, `AgentRun`, `AgentCost` 구조를 그대로 사용한다.
- 각 에이전트는 자체 입력/출력 스키마를 가질 수 있지만, 실행 상태와 비용 기록 방식은 플랫폼 공통 규칙을 따른다.
- 메뉴 추가 시 대시보드, 상세 화면, 실행 이력, 비용 집계가 자동으로 연결될 수 있어야 한다.

## 웹툰 자동화 에이전트와의 관계
- 웹툰 자동화 에이전트는 이 플랫폼에 등록되는 첫 번째 에이전트다.
- 웹툰 자동화 에이전트의 상세 설계는 `agents/webtoon/docs/project-plan.md`에서 관리한다.
- 플랫폼은 웹툰 자동화 에이전트의 실행 상태, 로그, 비용, 산출물 링크를 수집하고 표시한다.

## 플랫폼과 에이전트의 책임 분리
- 플랫폼은 메뉴, 화면, 실행 이력, 비용 집계, 공통 설정을 담당한다.
- 개별 에이전트는 입력 처리, 실제 자동화 로직, 산출물 생성, 에이전트 전용 상태 모델을 담당한다.
- 플랫폼은 에이전트 내부 로직을 직접 구현하지 않고, 실행 서비스나 공통 인터페이스를 통해 호출한다.

## 초기 폴더 구조 제안
```text
ai_agent/
  platform/
    app/
      main.py
      api/
      services/
      repositories/
    frontend/
      src/
      package.json
      vite.config.ts
    docs/
      agent-web-platform-plan.md
  agents/
    webtoon/
      main.py
      docs/
        project-plan.md
        run-metadata-template.json
```

## 단계별 구현 계획
1. `platform/app` 기본 구조와 FastAPI 엔트리포인트 생성
2. React 기반 Dashboard, Agents 목록, Agent 상세 페이지 구현
3. `Agent`, `AgentRun`, `AgentCost`, `AgentArtifact`, `PlatformSetting` 저장 구조 구현
4. 웹툰 자동화 에이전트를 첫 번째 등록 에이전트로 연결
5. 비용 집계와 최근 실행 대시보드 구현
6. 수동 실행 버튼과 실행 상세 로그 화면 구현
7. 이후 신규 에이전트 추가 규칙 문서화
