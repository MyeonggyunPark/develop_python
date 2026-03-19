# 독일생활 고양이 네컷 웹툰 자동화 에이전트 설계안

## 문서 범위
- 이 문서는 **독일생활 고양이 네컷 웹툰 자동화 에이전트** 단일 에이전트의 설계만 다룬다.
- 여러 자동화 에이전트를 통합 관리하는 웹 플랫폼 설계는 별도 문서에서 관리한다.

## 목표
- **입력 이미지(검은 고양이 + 회색 고양이)**와 **기본 프롬프트**는 항상 고정한다.
- 사용자가 주간 기준일에 주제를 입력하거나 추천 주제를 선택하면, 그 뒤의 네컷 웹툰 생성 및 게시 파이프라인을 자동 진행한다.
- 인스타그램 피드 업로드 전, 주제 중복과 텍스트 오타를 자동 점검한다.

## 성공 기준
### MVP 성공 기준
- 사용자가 주제를 직접 입력하면 해당 주제로 4컷 스크립트 초안과 웹툰 산출물을 생성할 수 있다.
- 사용자가 `"주제 추천해줘"`라고 요청한 경우에만 Google Sheets의 기존 이력과 비교하여 중복 가능성이 낮은 주제 후보를 추천할 수 있다.
- 생성 결과물은 정해진 JSON 스키마와 파일 규칙에 맞춰 저장된다.
- 사용자는 최종 승인 전까지 수정 횟수 제한 없이 수동으로 내용을 조정할 수 있다.

### 운영 성공 기준
- 주제 추천 시 전체 이력을 기준으로 유사도를 비교하고, 최근 8주 이력은 더 강하게 회피한다.
- 주제 추천 결과에는 추천 주제, 중복 점수, 제외 근거가 함께 기록된다.
- 최종 승인본 기준으로 치명적 오탈자 없이 게시 가능한 품질을 만족한다.
- 실행 이력, 사용한 주제, 추천 여부, 최종 승인 결과가 모두 기록된다.

## 전체 구조 (3 에이전트 + 오케스트레이터)

### 0) 오케스트레이터 (스케줄/상태관리)
- 실행 주기: 주 1회 기준일 관리 + 리마인드 알림 + 게시 마감 알림
- 역할
  - 파이프라인 시작/종료
  - 주제 입력 마감 및 게시 마감 스케줄 관리
  - 리마인드 알림 및 지연 알림 발송
  - 에이전트 간 입력/출력 전달
  - 실패 시 재시도 및 알림
  - 결과물(이미지, 캡션, 로그) 저장

#### 주간 실행 규칙
- 사용자는 주 1회의 기준일을 정한다. 예: 매주 화요일
- 사용자는 기준일에 주제를 직접 입력하거나 `"주제 추천해줘"` 요청 후 추천 후보 중 하나를 선택한다.
- 주제가 확정되면 이후 스크립트 생성, 이미지 생성, 교정, 승인, 게시 파이프라인을 자동 진행한다.
- 사용자는 최종 게시 시간도 별도로 정한다.
- 기준일 하루 전 오전에 주제 입력 리마인드 알림을 보낸다.
- 기준일 당일 오전에 주제 입력 최종 알림을 보낸다.
- 사용자가 정한 최종 게시 시간이 지났는데 `posted` 상태가 아니면 지연 알림을 보낸다.

#### 주간 스케줄 설정값
- `topic_due_day`: 사용자가 정한 주간 주제 입력 기준일
- `topic_due_time`: 기준일의 주제 입력 마감 시각
- `publish_deadline_time`: 사용자가 정한 최종 게시 마감 시각
- `week_end_time`: 기준일이 속한 주의 종료 시각
- `notification_channel`: 리마인드 및 지연 알림 채널

#### 주제 입력 마감 초과 규칙
- `topic_due_time`까지 주제가 확정되지 않으면 `awaiting_topic_input`, `topic_requested`, `topic_recommended` 상태를 모두 `topic_overdue`로 전환하고 지연 알림을 발송한다.
- 기준일이 지난 뒤라도 같은 주가 끝나기 전까지는 사용자가 주제를 입력하거나 추천 주제를 선택하면 해당 주차 실행을 계속 진행할 수 있다.
- 기준일이 속한 주가 끝날 때까지 주제가 확정되지 않으면 해당 주차 실행은 `skipped`로 종료한다.
- `skipped` 처리된 주차는 게시 파이프라인을 시작하지 않고, 스프레드시트에 사유를 기록한다.

#### 오케스트레이터 상태 모델
**상태값**
- `idle`: 아직 이번 주 작업이 시작되지 않은 상태
- `awaiting_topic_input`: 사용자의 주제 입력 또는 추천 선택을 기다리는 상태
- `topic_overdue`: 주제 입력 마감 시각을 넘겼지만 같은 주 안에서 입력을 기다리는 상태
- `topic_requested`: 사용자가 `"주제 추천해줘"`를 요청한 상태
- `topic_recommended`: 추천 후보가 생성되어 사용자 선택을 기다리는 상태
- `topic_selected`: 이번 주 주제가 확정된 상태
- `script_generated`: 4컷 스크립트와 캡션 초안 생성이 완료된 상태
- `script_review`: 사용자가 4컷 스크립트와 캡션 초안을 검토하는 상태
- `script_approved`: 이미지 생성 가능한 스크립트가 확정된 상태
- `image_generated`: 웹툰 이미지 생성이 완료된 상태
- `corrected`: OCR 및 텍스트 교정이 완료된 상태
- `editing`: 사용자가 수동 수정 중인 상태
- `approved`: 최종 승인 완료 상태
- `rejected`: 최종 검토에서 게시 불가로 반려된 상태
- `skipped`: 주간 종료 시점까지 주제가 확정되지 않아 이번 주 실행을 건너뛴 상태
- `posted`: 인스타 업로드 완료 상태
- `failed`: 자동 처리 실패로 중단된 상태

**상태 전이**
1. `idle -> awaiting_topic_input`
   - 주간 기준일 관리가 시작되고 사용자 입력을 기다리는 경우
2. `awaiting_topic_input -> topic_overdue`
   - `topic_due_time`을 넘겼지만 같은 주 안에서 입력을 계속 기다리는 경우
3. `awaiting_topic_input -> topic_selected`
   - 사용자가 주제를 직접 입력한 경우
4. `awaiting_topic_input -> topic_requested -> topic_recommended -> topic_selected`
   - 사용자가 추천을 요청하고 최종 주제를 선택한 경우
5. `topic_requested -> topic_overdue`
   - 추천 요청 후 주제가 확정되지 않은 채 `topic_due_time`을 넘긴 경우
6. `topic_recommended -> topic_overdue`
   - 추천 후보가 생성되었지만 선택되지 않은 채 `topic_due_time`을 넘긴 경우
7. `topic_overdue -> topic_selected`
   - 기준일이 지난 뒤라도 같은 주가 끝나기 전에 사용자가 주제를 직접 입력하거나 기존 추천 후보 중 하나를 선택한 경우
8. `topic_overdue -> topic_requested -> topic_recommended -> topic_selected`
   - 기준일이 지난 뒤라도 같은 주가 끝나기 전에 사용자가 추천을 요청하고 주제를 확정한 경우
9. `topic_recommended -> skipped`
   - 추천 후보가 생성되었지만 같은 주가 끝날 때까지 최종 선택되지 않은 경우
10. `topic_overdue -> skipped`
   - 기준일이 속한 주가 끝날 때까지 주제가 확정되지 않은 경우
11. `topic_selected -> script_generated -> script_review -> script_approved -> image_generated -> corrected -> editing -> approved -> posted`
12. 자동 단계에서 복구 불가 오류가 발생하면 `failed`로 전이
13. `script_review -> script_generated`
   - 사용자가 스크립트 재생성을 요청한 경우
14. `script_review -> script_approved`
   - 사용자가 스크립트를 승인하거나 직접 수정 후 확정한 경우
15. `editing -> script_generated` 또는 `editing -> image_generated`
   - 수동 수정 범위에 따라 스크립트 재생성 또는 이미지 재생성을 수행
16. `editing -> approved`
   - 단순 문구 수정이나 승인만 필요한 경우 바로 승인 처리
17. `editing -> rejected`
   - 최종 검토에서 게시 불가 판정을 받은 경우
18. `failed -> topic_selected` 또는 직전 성공 상태
   - 수동 재실행 시 해당 단계부터 재개

**재시도 정책**
- 주제 추천 실패: 1회 자동 재시도 후 실패 처리
- 스크립트 생성 실패: 2회 자동 재시도 후 실패 처리
- 스크립트 검토 단계: 자동 재시도 없이 사용자 입력 대기
- 이미지 생성 실패: 2회 자동 재시도 후 실패 처리
- OCR/교정 실패: 2회 자동 재시도 후 실패 처리
- 인스타 업로드 실패: 1회 자동 재시도 후 수동 확인 대기

#### 실행 식별자 및 재실행 규칙
**식별자**
- `week_key`: 운영 주차 식별자. 예: `2026-W11`
- `run_id`: 같은 주차 내 개별 실행 식별자. 예: `2026-W11-run-001`
- `attempt_no`: 같은 `run_id` 안에서 자동 재시도 또는 단계 재처리 횟수를 나타내는 번호
- 같은 주차에는 여러 `run_id`가 존재할 수 있지만, 최종 승인 및 게시 대상은 1개의 `active` 실행만 유지한다.

**재실행 원칙**
1. 같은 주차에 재실행하더라도 기존 산출물을 기본값으로 덮어쓰지 않는다.
2. 사용자가 다시 실행을 시작하면 새 `run_id`를 발급하고 새 산출물 세트로 저장한다.
3. 자동 재시도는 같은 `run_id`를 유지하고 `attempt_no`만 증가시킨다.
4. 최종 승인 직후 게시 전에 해당 실행을 `is_active=true`로 확정한다.
5. 같은 `week_key`의 다른 실행은 모두 `is_active=false`로 유지한다.
6. `approved` 상태이면서 `is_active=true`인 실행만 게시 대상으로 승격한다.
7. 이미 `posted` 상태이거나 `instagram_post_id`가 기록된 `run_id`는 동일 실행 기준으로 재게시하지 않는다.

---

### 1) 주제 입력/추천 에이전트 (중복 방지 포함)
**입력**
- 사용자 입력 주제 또는 `"주제 추천해줘"` 요청
- 기존 기획 이력 스프레드시트(주제, 키워드, 업로드 날짜)
- 계절/이벤트 컨텍스트(독일 공휴일, 월별 생활 이슈)

**출력**
- 이번 주 확정 주제 1개
- 추천 모드일 경우 추천 후보 3~5개
- 중복 점수 및 제외 근거

**핵심 로직**
1. 사용자가 주제를 직접 입력하면 해당 주제를 우선 사용하고, 이력 대비 중복 위험도만 점검한다.
2. 사용자가 `"주제 추천해줘"`라고 요청한 경우에만 후보 주제 10~20개를 생성한다.
3. 기존 기획 스프레드시트 전체 이력과 유사도 비교를 수행한다.
   - 텍스트 임베딩 코사인 유사도 + 키워드 겹침 점수
   - 최근 8주 이력은 가중치를 높여 더 강하게 회피한다.
4. 임계치 이상(예: 0.82) 주제 제거 후 추천 후보 3~5개와 제외 근거를 제시한다.
5. 추천 모드에서는 사용자가 최종 주제 1개를 선택한다.

#### 주제 추천 입력 데이터 규격
**사용자 입력**
- `user_topic`: 사용자가 직접 입력한 주제. 값이 있으면 추천 생성 없이 이 주제를 우선 사용한다.
- `recommendation_request`: 사용자가 `"주제 추천해줘"`라고 요청한 경우 `true`

**Google Sheets 이력 데이터**
- `week_key`: 게시 주차
- `topic`: 최종 사용 주제
- `keywords`: 보조 키워드 목록
- `summary`: 웹툰 내용 요약 1~2문장
- `posted_at`: 실제 게시일
- `status`: 게시 완료 여부
- 추천 비교 대상은 `status=approved` 또는 `status=posted`인 이력만 사용한다.

**계절/이벤트 컨텍스트**
- 1차 버전에서는 외부 API를 사용하지 않는다.
- 별도 시트 `seasonal_context`에서 수동 관리한다.
- 권장 컬럼:
  - `month`
  - `event_name`
  - `event_type`
  - `keywords`
  - `priority`
  - `notes`

**유사도 계산 필드와 가중치**
- `topic` 임베딩 유사도: 0.50
- `keywords` 겹침 점수: 0.20
- `summary` 임베딩 유사도: 0.20
- `posted_at` 최근성 가중치: 0.10
- 최종 점수 = `0.50 * topic + 0.20 * keywords + 0.20 * summary + 0.10 * recency`

#### 주제 중복 판정 규칙
1. 전체 게시 이력을 비교 대상으로 사용한다.
2. 최근 8주 이력은 강한 회피 구간으로 간주한다.
3. 최근 8주 내 동일하거나 매우 유사한 생활 상황은 추천 후보에서 제외한다.
4. 8주가 지난 이력은 관점이 다르면 재사용 가능하다.
5. 계절성 또는 반복성 주제는 직전 1년 내 동일 관점 반복을 피한다.
6. 아래 조건을 모두 만족하면 다른 주제로 허용한다.
   - 핵심 상황이 다르다.
   - 감정선 또는 갈등 구조가 다르다.
   - 전달 포인트가 다르다.
7. 최종 중복 점수 기준
   - `0.82 이상`: 중복으로 간주하고 제외
   - `0.70 이상 0.82 미만`: 후보 유지 가능, 단 제외 근거와 함께 수동 검토
   - `0.70 미만`: 통과

**주제 예시 풀**
- 독일 장보기/마트 문화
- 분리수거/재활용 습관
- 대중교통 지연, DB 앱 확인
- 겨울철 난방비/환기
- 휴일(성탄절 마켓, 부활절, 카니발)

---

### 2) 네컷 웹툰 생성 에이전트
**입력**
- 고정 캐릭터 이미지 1장(두 고양이)
- 고정 시스템 프롬프트
- 이번 주 주제

**출력**
- 4컷 스크립트 초안
- 4컷 구성 이미지(1080x1350 권장)
- 각 컷 대사 텍스트(메타데이터 JSON)
- 인스타 캡션 초안

**핵심 로직**
1. 컷 시나리오 및 캡션 초안 생성
   - 1컷: 상황 제시
   - 2컷: 갈등/문화 차이
   - 3컷: 반전/공감 포인트
   - 4컷: 따뜻한 마무리/한줄 교훈
2. 생성된 4컷 스크립트와 캡션 초안을 사용자에게 먼저 검토 요청
3. 사용자는 승인, 재생성 요청, 직접 수정 중 하나를 선택한다.
4. 승인된 스크립트 기준으로 이미지 생성 시 캐릭터 일관성 강제
   - 고정 참조 이미지 + 고정 베이스 프롬프트 항상 포함
   - 스타일 프롬프트(귀여운 벡터풍, 표정 강조, 가독성 높은 말풍선)
5. 말풍선 텍스트 길이 제한(컷당 20~35자)
6. 생성 에이전트의 기본 출력 파일은 `webtoon_composited_vN.png`로 저장하고, 최종 승인 대상 파일은 `webtoon_final_vN.png`로 관리한다.

#### 중간 승인 체크포인트
- 고비용 이미지 생성 전에 사용자가 4컷 스크립트와 캡션 초안을 먼저 검토한다.
- 사용자는 아래 셋 중 하나를 선택할 수 있다.
  - 승인: 이미지 생성 단계로 진행
  - 수정 요청: 스크립트만 다시 생성
  - 직접 수정: 대사/구성을 수동으로 고친 뒤 이미지 생성 진행

#### 스크립트 승인 체크리스트
- 4컷 흐름이 자연스러운가
- 주제가 요청 의도와 맞는가
- 대사 길이가 말풍선에 무리 없는가
- 문화적 표현과 톤이 어색하지 않은가
- 캐릭터 성격과 톤앤매너에 맞는가

#### 생성 산출물 JSON 스키마
**스크립트 산출물**
- `week_key`: 운영 주차 식별자
- `run_id`: 실행 식별자
- `topic`: 최종 주제
- `caption_draft`: 인스타 캡션 초안
- `panels`: 4컷 배열
  - `panel_no`: 컷 번호
  - `scene_description`: 장면 설명
  - `speaker`: 주요 화자
  - `emotion`: 감정 톤
  - `dialogue`: 말풍선 문구
  - `bubble_box`: 말풍선 렌더링 영역 좌표
  - `visual_notes`: 표정, 소품, 배경 메모

예시:
```json
{
  "week_key": "2026-W11",
  "run_id": "2026-W11-run-001",
  "topic": "독일 마트에서 병 보증금 환불하기",
  "caption_draft": "독일 마트에서 처음 만나는 판트 시스템",
  "panels": [
    {
      "panel_no": 1,
      "scene_description": "고양이 두 마리가 마트 입구에서 빈 병을 들고 서 있다.",
      "speaker": "black_cat",
      "emotion": "curious",
      "dialogue": "이 병도 돈으로 바뀐다고?",
      "bubble_box": {
        "x": 120,
        "y": 90,
        "w": 260,
        "h": 120
      },
      "visual_notes": "마트 입구, 판트 기계 보이기"
    }
  ]
}
```

**고정 프롬프트 템플릿(예시)**
- `SYSTEM_FIXED_PROMPT`: 캐릭터 생김새, 톤앤매너, 금지사항(캐릭터 변경 금지)
- `STYLE_FIXED_PROMPT`: 4컷 레이아웃, 한국어 대사, 인스타 업로드 비율
- `TOPIC_VARIABLE`: 이번 주 주제만 치환

#### 프롬프트 및 모델 버전 관리 정책
- 모든 생성 및 교정 실행에는 프롬프트 버전과 모델 버전을 명시적으로 기록한다.
- 고정 프롬프트는 파일로 분리하고 버전 번호를 부여한다.
- 실행 결과에는 아래 메타데이터를 함께 저장한다.
  - `prompt_version`
  - `system_prompt_version`
  - `style_prompt_version`
  - `topic_prompt_version`
  - `generator_model`
  - `generator_model_version`
  - `generator_params`
  - `ocr_model`
  - `ocr_model_version`
  - `correction_model`
  - `correction_model_version`

#### 프롬프트 관리 원칙
- `SYSTEM_FIXED_PROMPT`, `STYLE_FIXED_PROMPT`, `CORRECTION_PROMPT`는 별도 파일로 저장한다.
- 프롬프트 수정 시 기존 파일을 덮어쓰기보다 버전 증가를 우선한다.
- 권장 경로 예시:
  - `prompts/system_prompt_v1.md`
  - `prompts/style_prompt_v1.md`
  - `prompts/correction_prompt_v1.md`

#### 실행 기록 원칙
- 각 `run_id`는 어떤 프롬프트 및 모델 조합으로 생성되었는지 메타데이터 JSON에 기록한다.
- 품질 비교 시에는 `run_id`별 산출물과 메타데이터를 함께 검토한다.
- 문제가 생기면 직전 안정 버전의 프롬프트와 모델 조합으로 롤백 가능해야 한다.
- `run_metadata.json`의 기본 구조는 `agents/webtoon/docs/run-metadata-template.json`을 템플릿으로 사용한다.
- 구현 시 새 실행이 시작되면 `agents/webtoon/docs/run-metadata-template.json` 구조를 기준으로 각 `run_id` 폴더에 `run_metadata.json`을 생성한다.

#### 캐릭터 참조 이미지 운영 정책
- 웹툰에 사용할 고정 캐릭터 참조 이미지는 프로젝트 폴더에 우선 저장하고, Google Drive에는 백업본을 유지한다.
- 기본 경로 예시:
  - `assets/characters/black_cat.png`
  - `assets/characters/gray_cat.png`
  - `assets/characters/character_sheet.png`
- 생성 시마다 위 파일을 읽어 참조 이미지로 전달한다.
- 1차 버전에서는 로컬 프로젝트 폴더의 파일을 우선 사용하고, Google Drive는 운영 백업 및 공유 저장소로만 사용한다.
- 가능하면 캐릭터별 단일 이미지 1장보다 정면, 측면, 표정 변화가 포함된 캐릭터 시트 2~3장을 사용한다.

#### 캐릭터 일관성 PoC 정책
- 웹툰 자동화 본구현 전에 캐릭터 일관성 검증용 PoC를 먼저 수행한다.
- 검증 대상:
  - 고정 참조 이미지 1장만으로 충분한지
  - 고정 프롬프트 조합만으로 캐릭터 특징 유지가 가능한지
  - 4컷 내 일관성과 주차 간 일관성을 모두 만족하는지
- 테스트 방법:
  - 동일 주제로 3회 생성
  - 서로 다른 주제로 3회 생성
  - 각 결과에서 검은 고양이와 회색 고양이의 외형, 색상, 얼굴 특징, 체형, 역할 유지 여부를 확인
- 통과 기준:
  - 두 캐릭터가 사람 검수 기준으로 명확히 구분 가능해야 한다.
  - 4컷 내부에서 캐릭터 혼동이 없어야 한다.
  - 재생성 간에도 핵심 외형 특징이 유지되어야 한다.
- 실패 시 대안:
  - 참조 이미지를 1장에서 2~3장으로 늘린다.
  - 이미지 생성 단계와 텍스트 렌더링 단계를 분리한다.
  - 필요하면 캐릭터 시트 기반 수동 보정 또는 템플릿 기반 합성 방식으로 전환한다.

#### 캐릭터 일관성 검수 체크리스트
- 검은 고양이와 회색 고양이가 항상 같은 외형 특징을 유지하는가
- 눈, 귀, 체형, 털색 구분이 유지되는가
- 컷이 달라져도 캐릭터 역할이 뒤바뀌지 않는가
- 표정 변화가 있어도 동일 캐릭터로 인식되는가

---

### 3) 텍스트 교정 에이전트 (오타/자연스러움)
**입력**
- 웹툰 이미지
- 컷별 원문 대사(JSON)

**출력**
- 교정된 대사(JSON)
- 수정 내역(diff)
- 필요 시 재렌더링 요청 플래그

**핵심 로직**
1. OCR로 이미지 내 텍스트 추출
2. 원문 스크립트와 OCR 결과 비교
3. 맞춤법/띄어쓰기/독일 고유명사 표기 교정
4. 교정 후 글자 수 제한 초과 시 축약
5. 변경 발생 시 텍스트 없는 컷 이미지는 유지하고, 후처리 텍스트 합성본만 다시 렌더링한다.

#### 교정 산출물 JSON 스키마
**교정 결과**
- `week_key`
- `run_id`
- `source_script_version`
- `corrected_panels`
  - `panel_no`
  - `original_dialogue`
  - `ocr_text`
  - `corrected_dialogue`
  - `correction_reason`
  - `char_count`
  - `rerender_required`

**수정 diff**
- `week_key`
- `run_id`
- `changes`
  - `panel_no`
  - `field`
  - `before`
  - `after`
  - `reason`
  - `changed_by`
  - `changed_at`

#### 텍스트 재합성 구조
- 1차 버전에서는 웹툰 이미지 생성과 텍스트 렌더링을 분리한다.
- 이미지 생성 모델은 캐릭터, 배경, 표정, 소품이 포함된 텍스트 없는 컷 이미지만 생성한다.
- 말풍선과 텍스트는 후처리 렌더러가 별도로 합성한다.
- 각 컷 메타데이터에는 아래 정보를 저장한다.
  - `bubble_box`
  - `font_size`
  - `text_align`
  - `max_chars`
  - `line_break_rule`
- OCR 또는 수동 수정으로 대사가 바뀌면 전체 이미지 재생성 없이 텍스트만 다시 렌더링한다.

#### 재렌더링 규칙
1. `dialogue`만 바뀐 경우
   - 동일 컷 이미지 위에 텍스트만 다시 합성한다.
2. 수정된 텍스트가 `bubble_box` 범위를 넘는 경우
   - 축약 시도 후 다시 렌더링한다.
3. 축약 후에도 범위를 넘는 경우
   - `rerender_required=true`로 표시하고 스크립트 단계로 되돌린다.
4. 장면 설명, 표정, 구도 변경이 필요한 경우
   - 텍스트 수정이 아니라 이미지 재생성 대상으로 처리한다.

#### 구현 원칙
- 생성 모델 출력 이미지는 텍스트 없는 컷 이미지 자산으로 보관한다.
- 최종 게시 이미지는 컷 이미지와 말풍선, 텍스트 합성본으로 만든다.
- 교정본은 기존 컷 이미지를 재사용하고 텍스트만 교체한다.

## 데이터 저장소 설계 (Google Drive + Spreadsheet)
### 1) 이미지 저장: Google Drive 폴더 구조
- 루트 폴더 예시: `GermanLifeCatWebtoon`
- 하위 구조(연/월/주차/실행):
  - `{YYYY}/`
    - `{MM}/`
      - `{YYYY}-W{WW}/`
        - `{run_id}/`
          - `script_v1.json`
          - `script_v2.json`
          - `correction_v1.json`
          - `diff_v1.json`
          - `panel_1_base_v1.png`
          - `panel_2_base_v1.png`
          - `panel_3_base_v1.png`
          - `panel_4_base_v1.png`
          - `webtoon_composited_v1.png`
          - `webtoon_final_v2.png`
          - `approval_v1.json`
          - `run_metadata.json`
          - `publish_result_v1.json`

예시:
- `GermanLifeCatWebtoon/2026/03/2026-W11/2026-W11-run-001/webtoon_final_v2.png`

#### 산출물 버전 관리 규칙
- 모든 산출물은 `week_key/run_id` 폴더 아래에서 버전 단위로 관리한다.
- 버전 번호는 수정 또는 재생성 시 1씩 증가한다.
- 실행 산출물 파일명은 `week_key` 기반 단일 파일명이 아니라 `역할 + 버전` 규칙으로 통일한다.
- 동일 주차의 여러 실행은 `run_id` 폴더로 구분하고, 파일은 버전으로 관리한다.
- 파일 역할은 접두어로 구분한다.
  - `script_vN.json`: 스크립트 초안 또는 수정본
  - `correction_vN.json`: OCR 및 교정 결과
  - `diff_vN.json`: 변경 이력
  - `panel_*_base_vN.png`: 텍스트 없는 컷 원본
  - `webtoon_composited_vN.png`: 텍스트 합성본
  - `webtoon_final_vN.png`: 최종 승인 후보 또는 최종 승인본
  - `approval_vN.json`: 최종 승인 정보
  - `publish_result_vN.json`: 게시 결과 정보
- `webtoon_YYYY_WW.png` 형식의 단일 파일명 규칙은 사용하지 않는다.

#### 산출물 관계 정의
- `script_vN.json`은 해당 실행의 기준 스크립트다.
- `correction_vN.json`은 특정 `script_vN.json`을 입력으로 생성한다.
- `diff_vN.json`은 어떤 필드가 어떻게 바뀌었는지 기록한다.
- `panel_*_base_vN.png`는 텍스트 없는 컷 원본이다.
- `webtoon_composited_vN.png`는 특정 스크립트 버전과 컷 원본을 합성한 결과다.
- `webtoon_final_vN.png`는 승인 직전 또는 승인 완료 상태의 게시 후보 이미지다.
- `approval_vN.json`은 어떤 산출물을 최종 승인했는지 기록한다.
- `publish_result_vN.json`은 어떤 승인본이 실제 게시되었는지 기록한다.

#### 메타데이터 연결 규칙
- 각 산출물 JSON에는 아래 참조 필드를 포함한다.
  - `source_script_version`
  - `source_image_version`
  - `source_correction_version`
  - `approved_output_file`
  - `published_output_file`
- `run_metadata.json`은 각 실행의 최신 활성 버전과 파일 관계를 요약한다.

#### 승인 및 게시 규칙
- 게시 가능한 파일은 `approval_vN.json`에서 승인된 `webtoon_final_vN.png` 1개만 허용한다.
- 게시 후에는 `publish_result_vN.json`에 승인본 파일명, 게시 시각, 게시 ID를 기록한다.

### 2) 기획/운영 기록: Google Spreadsheet
- 시트 이름: `weekly_planning`
- 권장 컬럼:
  - `week_key` (예: 2026-W11)
  - `run_id` (예: 2026-W11-run-001)
  - `attempt_no`
  - `topic_due_day`
  - `topic_due_time`
  - `publish_deadline_time`
  - `week_end_time`
  - `notification_channel`
  - `input_mode` (`manual_topic` 또는 `recommended_topic`)
  - `recommended_candidates_json`
  - `prompt_version`
  - `generator_model`
  - `generator_params`
  - `ocr_model`
  - `correction_model`
  - `topic`
  - `keywords`
  - `duplicate_score`
  - `excluded_topics`
  - `panel_script_json`
  - `caption`
  - `drive_folder_url`
  - `composited_image_file_url`
  - `final_image_file_url`
  - `is_active`
  - `status` (`idle`, `awaiting_topic_input`, `topic_overdue`, `topic_requested`, `topic_recommended`, `topic_selected`, `script_generated`, `script_review`, `script_approved`, `image_generated`, `corrected`, `editing`, `approved`, `rejected`, `skipped`, `posted`, `failed`)
  - `error_stage`
  - `error_message`
  - `error_type`
  - `retry_count`
  - `approved_by`
  - `approved_at`
  - `approval_reason`
  - `approved_script_version`
  - `approved_image_version`
  - `stage_duration_json`
  - `total_duration_ms`
  - `total_cost`
  - `alert_sent`
  - `last_error_at`
  - `instagram_post_id`
  - `instagram_post_url`
  - `published_file_url`
  - `posted_at`
  - `last_updated_at`
  - `notes`

#### `weekly_planning` 운영 원칙
- `weekly_planning`은 단순 기획 메모가 아니라 주차별 실행 원장으로 사용한다.
- `weekly_planning.status`는 오케스트레이터 상태 모델의 상태값 집합과 동일하게 유지한다.
- 상태 변경이 발생할 때마다 `status`, `last_updated_at`, `error_stage`, `retry_count`를 함께 갱신한다.
- 주간 기준일과 게시 마감 시각은 `topic_due_day`, `topic_due_time`, `publish_deadline_time`, `week_end_time`에 기록한다.
- 추천 모드 실행은 `input_mode=recommended_topic`, 직접 입력 실행은 `input_mode=manual_topic`으로 구분한다.
- 최종 승인 시 `approved_by`, `approved_at`, `approved_script_version`, `approved_image_version`을 기록한다.
- 최종 승인 직후 게시 전에 선택된 실행만 `is_active=true`로 설정하고, 같은 주차의 다른 실행은 `is_active=false`로 갱신한다.
- 승인 또는 반려 시 `approval_reason`을 함께 기록한다.
- 단계 종료 시 `stage_duration_json`, `total_duration_ms`, `total_cost`를 갱신한다.
- 실패 또는 반려 알림을 보낸 경우 `alert_sent`, `last_error_at`을 기록한다.
- `composited_image_file_url`은 `webtoon_composited_vN.png`, `final_image_file_url`은 `webtoon_final_vN.png`를 가리키도록 유지한다.
- 게시 완료 시 `instagram_post_id`, `instagram_post_url`, `published_file_url`, `posted_at`을 기록한다.

#### 활성 실행 확정 규칙
- `is_active`는 현재 주차에서 실제 게시 대상으로 선택된 승인 실행을 의미한다.
- 사용자가 최종 승인하면 게시 전에 해당 `run_id`를 `is_active=true`로 설정한다.
- 같은 `week_key`의 다른 실행은 모두 `is_active=false`로 유지한다.
- 인스타 업로드는 `approved` 상태이면서 `is_active=true`인 실행에 대해서만 수행한다.

#### 최종 승인 체크리스트
- 캐릭터 일관성이 유지되는가
- 말풍선과 텍스트 가독성이 충분한가
- 오탈자, 띄어쓰기, 고유명사 표기에 문제가 없는가
- 주제가 요청 의도와 맞는가
- 문화적 표현이 과도하게 어색하거나 민감하지 않은가
- 최종 이미지 비율과 품질이 인스타 게시용으로 적합한가

#### 승인 결과 규칙
- 사용자는 아래 셋 중 하나를 선택한다.
  - `approved`: 바로 게시 가능
  - `needs_edit`: 수동 수정 후 다시 승인 필요
  - `rejected`: 이번 실행은 게시 불가

#### 반려 처리 규칙
- `needs_edit`인 경우 `editing` 상태로 되돌린다.
- 텍스트만 수정하면 되는 경우 텍스트 재합성 단계로 처리한다.
- 장면, 구도, 표정 수정이 필요하면 이미지 재생성 단계로 되돌린다.
- `rejected`인 경우 `rejected` 상태와 반려 사유를 기록한다.
- 승인 또는 반려 시 `approved_by`, `approved_at`, `approval_reason`을 기록한다.

- 시트 이름: `text_corrections`
- 권장 컬럼:
  - `week_key`
  - `run_id`
  - `script_version`
  - `correction_version`
  - `panel_no`
  - `before`
  - `after`
  - `reason`
  - `changed_by`
  - `rerender_required`
  - `applied_to_image_version`

## 외부 서비스 인증 및 설정 관리
#### 인증 및 비밀값 관리 원칙
- 모든 외부 서비스 자격 증명은 코드에 하드코딩하지 않는다.
- 비밀값은 기존 `.env` 파일에서 관리하고, 운영 환경에서는 동일한 키 이름으로 주입한다.
- 서비스별 인증 방식은 아래와 같이 고정한다.
  - Google Drive API: OAuth 2.0 사용자 인증 사용
  - Google Sheets API: OAuth 2.0 사용자 인증 사용
  - Gemini LLM / 이미지 생성 / OCR API: 공통 API Key 사용
  - Instagram Graph API: 장기 토큰 사용

#### 필수 환경변수
- `GOOGLE_OAUTH_CLIENT_SECRET_FILE`
- `GOOGLE_OAUTH_TOKEN_FILE`
- `GOOGLE_DRIVE_ROOT_FOLDER_ID`
- `GOOGLE_SHEETS_SPREADSHEET_ID`
- `GEMINI_API_KEY`
- `INSTAGRAM_ACCESS_TOKEN`
- `INSTAGRAM_BUSINESS_ACCOUNT_ID`
- `APPROVAL_DEFAULT_USER`

#### 설정 관리 원칙
- `.env`에는 비밀값과 리소스 ID만 저장한다.
- `settings.example.yaml`은 현재 필수 파일이 아니며, 비밀값이 아닌 구조화 설정이 많아질 때만 추가한다.
- 예를 들어 주간 실행 시간, 기본 타임존, 파일명 규칙, 추천 후보 개수, 중복 임계치처럼 비밀이 아닌 설정이 늘어나면 `settings.example.yaml`로 분리한다.
- 현재 단계에서는 문서에 정의된 기본값과 `.env` 환경변수만으로 운영한다.

#### 권한 및 리소스 관리 원칙
- Google Drive 루트 폴더와 Google Sheets 문서는 OAuth 인증에 사용하는 본인 계정이 접근 가능해야 한다.
- 운영에 사용하는 폴더 ID, 시트 ID, 인스타 계정 ID는 환경변수로 주입한다.
- 권한 부족 또는 인증 실패 시 즉시 `failed` 상태로 전이하고 `error_stage`와 `error_message`를 기록한다.

#### 운영 체크포인트
- 구현 시작 전에 Google OAuth 클라이언트 생성과 사용자 인증을 완료한다.
- Instagram 비즈니스 계정 연결 여부를 사전 점검한다.
- 각 API별 테스트 호출로 인증 성공 여부를 먼저 확인한다.

## 운영 플로우 (주 1회)
1. 주간 기준일과 게시 마감 시각을 설정한다.
2. 기준일 하루 전 오전에 주제 입력 리마인드 알림을 보낸다.
3. 기준일 당일 오전에 주제 입력 최종 알림을 보낸다.
4. `topic_due_time`까지 주제가 확정되지 않으면 `topic_overdue`로 전환하고 지연 알림을 보낸다.
5. 기준일이 지난 뒤라도 같은 주가 끝나기 전까지는 사용자가 주제를 직접 입력하거나 `"주제 추천해줘"`를 요청할 수 있다.
6. 주제 입력/추천 에이전트 실행 → 전체 이력 비교 + 최근 8주 강한 회피 기준으로 중복 검사
7. 추천 모드일 경우 추천 후보와 제외 근거를 확인한 뒤 사용자가 최종 주제를 선택한다.
8. 기준일이 속한 주가 끝날 때까지 주제가 확정되지 않으면 해당 주차 실행을 `skipped`로 종료한다.
9. 웹툰 생성 에이전트 실행 → 4컷 스크립트/캡션 초안 생성
10. 사용자가 스크립트 초안을 검토하고 승인, 재생성 요청, 직접 수정 중 하나를 선택한다.
11. 승인된 스크립트 기준으로 이미지 생성
12. 생성 결과를 주차 폴더(Drive)에 저장
13. 텍스트 교정 에이전트 실행 → 오타 수정
14. 교정본 이미지를 동일 주차 폴더에 저장
15. 사용자가 필요한 만큼 수동 수정 후 최종 승인
16. 최종 승인된 실행을 `is_active=true`로 설정하고 같은 주차의 다른 실행은 `is_active=false`로 갱신한다.
17. 인스타 업로드
18. 사용자가 정한 최종 게시 시간이 지났는데 `posted` 상태가 아니면 지연 알림을 보낸다.
19. 주제/추천 여부/수정내역/파일 URL/업로드 결과를 `week_key`, `run_id`, `attempt_no`와 함께 스프레드시트에 기록한다.

## 실패 대응
#### 실패 유형 분류
- `recoverable`: 자동 재시도로 복구 가능한 실패
- `manual_action_required`: 사람 개입이 필요한 실패
- `business_rejection`: 승인 반려나 운영 판단에 의한 중단
- `configuration_error`: 인증, 권한, 환경설정 오류
- `external_service_error`: 외부 API 장애 또는 quota 문제

#### 단계별 실패 대응 정책
- 주제 추천 실패
  - API 일시 오류: 1회 자동 재시도
  - 이력 데이터 부족 또는 형식 오류: 수동 확인 필요
- 스크립트 생성 실패
  - 모델 응답 실패: 2회 자동 재시도
  - 응답 형식 불일치: 1회 재생성 후 실패 처리
- 이미지 생성 실패
  - 타임아웃 또는 일시 오류: 2회 자동 재시도
  - 캐릭터 일관성 미달: 자동 복구하지 않고 수동 검토
- OCR 및 교정 실패
  - OCR 인식률 저하: 1회 재시도 후 수동 검토
  - 글자 수 초과: 축약 후 재렌더링
- Drive 또는 Sheets 저장 실패
  - 네트워크 또는 일시 오류: 2회 자동 재시도
  - 권한 오류: 즉시 실패 처리 후 설정 점검
- 승인 반려
  - `rejected` 상태 기록
  - 기술 실패가 아니라 운영 반려로 분류
- Instagram 게시 실패
  - 업로드 일시 실패: 1회 자동 재시도
  - 게시 중복 의심: 재게시 금지 후 수동 확인

#### 실패 기록 규칙
- 실패 발생 시 아래 항목을 반드시 기록한다.
  - `error_stage`
  - `error_type`
  - `error_message`
  - `retry_count`
  - `last_updated_at`
- 자동 복구 불가 실패는 `failed` 또는 `rejected` 상태로 남긴다.
- 같은 실패가 반복되면 알림 대상으로 승격한다.

#### 실패 처리 우선순위
1. 중복 게시 방지
2. 기존 산출물 보존
3. 자동 재시도
4. 수동 개입 요청

## 로그, 모니터링, 알림, 비용 관리
#### 로그 수집 원칙
- 모든 실행은 `run_id` 기준으로 구조화 로그를 남긴다.
- 단계 시작, 단계 종료, 실패 발생 시 로그를 기록한다.
- 최소 로그 필드는 아래와 같다.
  - `timestamp`
  - `week_key`
  - `run_id`
  - `stage`
  - `status`
  - `message`
  - `error_type`
  - `duration_ms`

#### 모니터링 지표
- 주간 실행 성공 여부
- 단계별 실행 시간
- 자동 재시도 횟수
- 실패 단계 빈도
- 승인까지 걸린 시간
- 게시 완료 여부

#### 알림 정책
- 아래 경우 알림을 보낸다.
  - `failed` 상태 진입
  - `rejected` 상태 진입
  - 같은 실패가 반복 발생
  - 게시 실패 발생
  - 인증 또는 권한 오류 발생
- 단순 자동 재시도 1회 수준은 즉시 알림하지 않고 누적 기준으로 판단한다.

#### 비용 관리 원칙
- 외부 API 호출 비용은 `run_id` 단위로 기록한다.
- 최소 기록 항목은 아래와 같다.
  - `image_generation_cost`
  - `ocr_cost`
  - `llm_correction_cost`
  - `total_cost`
- 주간 비용 상한선을 정하고 초과 시 알림을 보낸다.
- 비용이 급증하면 직전 안정 모델과 프롬프트 조합과 비교한다.

## Instagram 게시 운영 정책
#### 게시 전제 조건
- Instagram Graph API 사용을 전제로 한다.
- 게시 자동화는 비즈니스 계정 또는 크리에이터 계정 연동이 완료된 경우에만 수행한다.
- `approved` 상태이면서 `is_active=true`인 실행만 게시 대상으로 허용한다.

#### 게시 절차
1. 게시 대상 이미지와 캡션을 최종 확인한다.
2. Instagram 업로드용 미디어 컨테이너를 생성한다.
3. 컨테이너 생성 성공 후 실제 publish 요청을 수행한다.
4. 게시 성공 시 게시 ID와 게시 URL을 저장한다.

#### 게시 실패 대응
- 컨테이너 생성 실패: 1회 자동 재시도 후 수동 확인
- publish 실패: 1회 자동 재시도 후 수동 확인
- 이미 게시된 `run_id`는 재게시하지 않는다.
- 게시 중복이 의심되면 자동 재시도하지 않고 수동 확인으로 전환한다.

#### 게시 결과 기록
- `instagram_post_id`
- `instagram_post_url`
- `posted_at`
- `published_file_url`
- `publish_result_vN.json`

#### 중복 게시 방지 규칙
- `instagram_post_id`가 이미 존재하는 `run_id`는 게시 대상에서 제외한다.
- 동일 `week_key`에서 새 실행이 승인되면 이전 실행은 `is_active=false`로 유지한다.
- 실제 게시 전 마지막으로 `is_active=true`와 `status=approved`를 다시 검증한다.

## 권장 기술 스택
- 오케스트레이션: cron + Python runner (또는 Prefect)
- 주제 중복 검사: sentence-transformers + Google Sheets API
- 이미지 생성: 고정 참조 이미지 지원 모델(API)
- OCR/교정: EasyOCR(or Vision API) + LLM 교정
- 저장/기록: Google Drive API + Google Sheets API
- 게시 자동화: Instagram Graph API(비즈니스 계정 필요)

## 단계별 구현 및 검증 전략
### 1단계 MVP: 주제 입력/추천 + 스크립트 생성 + 기록
- 범위
  - 사용자가 주제를 직접 입력할 수 있다.
  - `"주제 추천해줘"` 요청 시 중복 검사 기반 추천 후보를 제시할 수 있다.
  - 최종 주제로 4컷 스크립트와 캡션 초안을 생성한다.
  - 결과를 Google Sheets와 로컬 또는 Drive 구조에 기록한다.
- 완료 기준
  - 수동 주제 입력과 추천 모드가 모두 동작한다.
  - 추천 결과에 중복 점수와 제외 근거가 포함된다.
  - 스크립트 JSON이 정의된 스키마를 만족한다.
- 테스트 데이터
  - 기존 주제 이력 20건 이상
  - 계절성 주제 5건 이상
- 다음 단계 진입 조건
  - 최근 5회 테스트에서 치명적 실패 없이 동작

### 2단계 MVP: 캐릭터 참조 이미지 기반 컷 이미지 생성
- 범위
  - 승인된 스크립트로 텍스트 없는 컷 이미지를 생성한다.
  - 캐릭터 참조 이미지를 입력으로 사용한다.
- 완료 기준
  - 4컷 이미지 생성 성공
  - 캐릭터 일관성 PoC 통과
  - 컷 이미지가 저장 규칙에 맞게 기록된다.
- 테스트 데이터
  - 동일 주제 3회
  - 다른 주제 3회
- 다음 단계 진입 조건
  - 검수 체크리스트 기준 캐릭터 혼동 없음

### 3단계 MVP: 텍스트 합성 + OCR 교정 루프
- 범위
  - 말풍선과 텍스트 후처리 합성
  - OCR 기반 교정
  - 텍스트만 재렌더링
- 완료 기준
  - 교정 결과 JSON과 diff JSON 생성
  - 단순 오탈자는 전체 이미지 재생성 없이 수정 가능
  - `rerender_required` 판단이 정상 동작
- 테스트 데이터
  - 의도적 오탈자 포함 샘플 10건
- 다음 단계 진입 조건
  - 교정 성공률과 재렌더링 흐름 확인 완료

### 4단계 MVP: 승인 및 게시 자동화
- 범위
  - 승인 기록
  - Instagram 업로드 및 게시
  - 게시 결과 기록
- 완료 기준
  - 승인 상태에서만 게시 가능
  - 게시 ID와 URL 저장
  - 중복 게시 방지 검증 완료
- 테스트 데이터
  - 비공개 또는 테스트 계정 게시 3회
- 다음 단계 진입 조건
  - 게시 실패, 재시도, 중복 방지 시나리오 검증 완료
