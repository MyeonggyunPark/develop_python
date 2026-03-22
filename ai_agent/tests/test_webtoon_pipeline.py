from __future__ import annotations

import io
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from agents.webtoon import pipeline as pipeline_module
from agents.webtoon import clients as clients_module
from agents.webtoon.pipeline import (
    _apply_corrected_dialogues,
    _build_character_priority_gate_errors,
    _build_progressive_anchor_bundle,
    _is_better_candidate,
    _build_prompt_plan_package,
    _build_publish_decision,
    _generate_panels_progressively,
    _simplify_background_correction,
    _stabilize_generated_image,
    OutboundArtifact,
    OutboundBundle,
    build_bubble_only_ocr_image,
    build_panel_generation_prompt,
    build_run_identity,
    build_thumbnail_generation_prompt,
    build_versioned_path,
    create_artifact_paths,
    execute_outbound_bundle,
    list_character_reference_files,
)
from agents.webtoon.clients import _extract_json_object
from agents.webtoon.config import WebtoonSettings


class TestBuildRunIdentity:
    def test_returns_week_key_and_run_id(self):
        week_key, run_id = build_run_identity()
        assert week_key.startswith("20")
        assert "-W" in week_key
        assert run_id.startswith("run-")
        assert len(run_id) == 12  # "run-" + 8 hex chars

    def test_deterministic_week_key_for_same_date(self):
        dt = datetime(2026, 3, 16, 12, 0, 0, tzinfo=timezone.utc)
        wk1, _ = build_run_identity(dt)
        wk2, _ = build_run_identity(dt)
        assert wk1 == wk2

    def test_run_id_is_unique(self):
        dt = datetime(2026, 3, 16, 12, 0, 0, tzinfo=timezone.utc)
        _, rid1 = build_run_identity(dt)
        _, rid2 = build_run_identity(dt)
        assert rid1 != rid2


class TestExtractJsonObject:
    def test_extracts_first_json_object_even_with_trailing_text(self):
        text = '{"title":"테스트","panels":[]}\\n추가 설명이 뒤에 붙었습니다.'
        parsed = _extract_json_object(text)
        assert parsed["title"] == "테스트"
        assert parsed["panels"] == []

    def test_extracts_json_inside_code_fence_with_preface(self):
        text = '여기 결과입니다.\\n```json\\n{"ok": true, "items": [1, 2]}\\n```\\n감사합니다.'
        parsed = _extract_json_object(text)
        assert parsed["ok"] is True
        assert parsed["items"] == [1, 2]


class TestCreativeBriefHumorRules:
    def test_humor_block_mentions_comedic_payoff_and_character_contrast(self):
        block = clients_module.CREATIVE_BRIEF_HUMOR_BLOCK
        assert "생활형 개그 웹툰" in block
        assert "콜라의 과한 자신감" in block
        assert "제로의 예민함" in block
        assert "이번 주제의 실제 상황" in block
        assert "setup 또는 punchline" in block
        assert "마지막 컷" in block
        assert "payoff" in block

    def test_humor_review_block_rejects_plain_exposition(self):
        block = clients_module.CREATIVE_BRIEF_HUMOR_REVIEW_BLOCK
        assert "평범한 설명문" in block
        assert "절차를 순서대로 밟는 설명 만화" in block
        assert "허세" in block
        assert "반전" in block or "payoff" in block
        assert "주제 바깥의 뜬금없는 농담" in block
        assert "setup/payoff" in block


class TestBuildVersionedPath:
    def test_format(self):
        path = build_versioned_path(Path("/tmp/run"), "panel_01_final", 3, "png")
        assert path == Path("/tmp/run/panel_01_final_v3.png")


class TestCreateArtifactPaths:
    def test_paths_are_in_temp_dir(self):
        artifacts = create_artifact_paths("run-abc12345", "2026-W12")
        assert artifacts.run_dir.exists()
        assert "webtoon-run-abc12345" in artifacts.run_dir.name
        assert artifacts.metadata_path.name == "run_metadata.json"
        import shutil
        shutil.rmtree(artifacts.run_dir, ignore_errors=True)


class TestRunWebtoonPipelineDiagnostics:
    def test_preserves_run_dir_and_writes_failure_report_on_exception(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        settings = _make_dummy_settings()
        run_dir = tmp_path / "webtoon-run-failure"
        run_dir.mkdir()
        artifact_paths = pipeline_module.PipelineArtifacts(
            run_dir=run_dir,
            run_id="run-test1234",
            week_key="2026-W12",
            metadata_path=run_dir / "run_metadata.json",
            publish_result_path=run_dir / "publish_result_v1.json",
        )

        monkeypatch.setattr(pipeline_module, "build_run_identity", lambda: ("2026-W12", "run-test1234"))
        monkeypatch.setattr(pipeline_module, "create_artifact_paths", lambda *_args, **_kwargs: artifact_paths)

        def fake_execute(*_args: Any, **kwargs: Any) -> dict[str, Any]:
            pipeline_module._mark_pipeline_stage(
                kwargs["artifact_paths"],
                "drive_upload_started",
                topic=kwargs["topic"],
                notes=kwargs["notes"],
            )
            raise RuntimeError("drive upload exploded")

        monkeypatch.setattr(pipeline_module, "_execute_webtoon_pipeline", fake_execute)

        with pytest.raises(RuntimeError, match="drive upload exploded"):
            pipeline_module.run_webtoon_pipeline(settings, topic="독일 입국", notes="diagnostic test")

        status_payload = json.loads((run_dir / "pipeline_status.json").read_text(encoding="utf-8"))
        failure_payload = json.loads((run_dir / "pipeline_failure.json").read_text(encoding="utf-8"))

        assert run_dir.exists()
        assert status_payload["stage"] == "drive_upload_started"
        assert failure_payload["failed_stage"] == "drive_upload_started"
        assert failure_payload["error_type"] == "RuntimeError"
        assert failure_payload["error_message"] == "drive upload exploded"
        assert "RuntimeError: drive upload exploded" in failure_payload["traceback"]
        assert not artifact_paths.publish_result_path.exists()


class TestBuildThumbnailGenerationPrompt:
    def test_contains_required_elements(self):
        prompt = build_thumbnail_generation_prompt(
            "기본 프롬프트",
            title="테스트 제목",
            subtitle="설명 부제목",
            topic="테스트 주제",
            caption="독일 입국부터 이동까지 한 에피소드",
            episode_scope="journey",
            subtitle_scope="journey",
            scope_summary="독일 입국부터 기차 환승까지 이어지는 여정",
            character_notes="검은 고양이",
            thumbnail_scene_prompt="공항 입국장 전광판 앞에서 두 캐릭터가 환승 안내를 확인하는 장면",
            character_accuracy_lines=[
                "등장인물 정확성이 배경 디테일보다 항상 우선이다.",
            ],
        )
        assert "기본 프롬프트" in prompt
        assert "테스트 주제" in prompt
        assert "설명 부제목" in prompt
        assert "독일 입국부터 이동까지 한 에피소드" in prompt
        assert "journey" in prompt
        assert "부제목 범위 타입" in prompt
        assert "검은 고양이" in prompt
        assert "공항 입국장 전광판" in prompt
        assert "표지" in prompt or "커버" in prompt
        assert "10~15%" in prompt
        assert "상단 하늘" in prompt
        assert "마스터 레퍼런스" in prompt
        assert "멀티패널" in prompt
        assert "핵심 행동, 공간, 소품 단서" in prompt
        assert "비주요 인간 배경 인물" in prompt
        assert "골반과 상체가 사람처럼 직립" in prompt
        assert "같은 캐리어 배치" in prompt
        assert "핵심 물건이 지정되면 그 물건을 정확히 그대로" in prompt
        assert "종류와 주된 색상까지 그대로" in prompt
        assert "두 권 여권을 한 권짜리 두꺼운 책자처럼 합치면 실패" in prompt
        assert "콜라가 제로와 비슷한 크기이거나 더 작아 보이면 안 된다" in prompt
        assert "사전 확정 등장인물 정확성 계약" in prompt
        assert "등장인물 정확성이 배경 디테일보다 항상 우선" in prompt
        assert "썸네일부터 패널 6까지 모든 컷에 공통으로 적용되는 절대 규칙" in prompt
        assert "같은 좁은 범위로 유지" in prompt
        assert "사람이 올라서면 안 되는 표면" in prompt
        assert "여분의 손, 세 번째 앞발, 끊긴 팔, 팔이 없는 실루엣" in prompt
        assert "선글라스, 모자, 목걸이, 배지, 목도리" in prompt
        assert "인간 배경 인물은 항상 주인공보다 작고 흐리게" in prompt
        assert "모자이크처럼 깨진 텍스트" in prompt
        assert "포괄적 대표 배경" in prompt
        assert "같은 메인 홀/복도/대기 공간 구조에 간판만 바꾼 버전도 금지" in prompt
        assert "특정 한 컷에만 적용되는 것이 아니라 패널 1부터 패널 6 전체에 적용" in prompt
        assert "구조적 지문" in prompt
        assert "전체 여정을 소개하는 별도 전환 공간" in prompt
        assert "scene-required in-world signage" in prompt
        assert "'INFO'" in prompt
        assert "'PASSPORT'" in prompt or "'DOCUMENT'" in prompt
        assert "날짜 한 줄만 허용" in prompt
        assert "독일 풍경(거리, 마트, 카페, 공원 등)" not in prompt

    def test_empty_character_notes(self):
        prompt = build_thumbnail_generation_prompt(
            "기본 프롬프트",
            title="제목",
            subtitle="",
            topic="주제",
            caption="",
            episode_scope="single_location",
            subtitle_scope="single_location",
            scope_summary="공항 입국장에서 끝나는 에피소드",
            character_notes="",
            thumbnail_scene_prompt="",
        )
        assert "캐릭터 일관성 메모" not in prompt

    def test_includes_explicit_background_text_plan_when_provided(self):
        prompt = build_thumbnail_generation_prompt(
            "기본 프롬프트",
            title="테스트 제목",
            subtitle="설명 부제목",
            topic="테스트 주제",
            caption="독일 입국부터 이동까지 한 에피소드",
            episode_scope="journey",
            subtitle_scope="journey",
            scope_summary="독일 입국부터 기차 환승까지 이어지는 여정",
            character_notes="검은 고양이",
            thumbnail_scene_prompt="공항 입국장 장면",
            shot_plan="대표 장소=입국장 | 핵심 장면=전광판 확인",
            background_text_mode="exact",
            background_text_lines=["ARRIVALS", "PASSPORT CONTROL"],
        )
        assert "사전 확정 샷 플랜" in prompt
        assert "ARRIVALS" in prompt
        assert "PASSPORT CONTROL" in prompt
        assert "위 목록 외의 읽을 수 있는 배경 텍스트는 만들지 않는다" in prompt


class TestBuildPanelGenerationPrompt:
    def test_contains_scene_prompt(self):
        panel = {
            "panel_no": 2,
            "scene_prompt": "마트에서 장보기",
            "story_role": "승",
            "location": "독일 마트 입구",
            "key_props": ["장바구니", "할인 전단"],
            "carryover_props": ["장바구니"],
        }
        previous_panel = {
            "panel_no": 1,
            "location": "공항 입국장",
            "key_props": ["장바구니", "여권"],
        }
        prompt = build_panel_generation_prompt(
            "기본",
            panel,
            "짧은 제목",
            "설명 부제목",
            "마트 적응기",
            "journey",
            "journey",
            "공항에서 마트까지 이어지는 생활 적응 에피소드",
            "",
            ["안녕하세요"],
            previous_panel=previous_panel,
        )
        assert "마트에서 장보기" in prompt
        assert "2컷" in prompt
        assert "안녕하세요" in prompt
        assert "설명 부제목" in prompt
        assert "마트 적응기" in prompt
        assert "journey" in prompt
        assert "부제목 범위 타입" in prompt
        assert "이 컷의 서사 역할: 승" in prompt
        assert "이 컷의 주요 장소: 독일 마트 입구" in prompt
        assert "이전 컷 장소: 공항 입국장" in prompt
        assert "배경이 확실히 바뀌어야" in prompt
        assert "10~15%" in prompt
        assert "같은 바닥 평면" in prompt
        assert "마스터 레퍼런스" in prompt
        assert "단일 패널 한 컷" in prompt
        assert "비주요 인간 배경 인물" in prompt
        assert "앞발로 바닥을 짚어 체중을 싣는 일반 동물형 자세는 금지" in prompt
        assert "네 발 보행이나 앞발 체중 지지는 한 번이라도 허용되지 않는다" in prompt
        assert "자세는 반드시 인간형 직립 이족보행" in prompt
        assert "핵심 소품과 손동작은 정확히 지킨다" in prompt
        assert "이번 컷 핵심 소품: 장바구니, 할인 전단." in prompt
        assert "이번 컷에서 연속 유지해야 할 소품: 장바구니." in prompt
        assert "연속 소품은 이유 없이 다른 종류로 바꾸지 않는다" in prompt
        assert "종류뿐 아니라 주된 색상, 크기, 손에 든 쪽" in prompt
        assert "콜라가 제로와 비슷한 크기이거나 더 작아 보이면 안 된다" in prompt
        assert "썸네일부터 패널 6까지 모든 컷에 공통으로 적용되는 절대 규칙" in prompt
        assert "같은 좁은 범위로 유지" in prompt
        assert "벨트, 레일, 기계 상판, 운반 장비, 전시대 상단은 배경 장비" in prompt
        assert "모자이크처럼 깨진 텍스트" in prompt
        assert "썸네일의 대표 배경 구조나 서브로케이션을 재사용하면 안 된다" in prompt
        assert "본문 여섯 컷 전체에서 금지된 전용 배경" in prompt
        assert "구조적 지문" in prompt
        assert "같은 건물을 넓게/좁게만 다시 그리거나" in prompt
        assert "'INFO'" in prompt
        assert "'PASSPORT'" in prompt or "'DOCUMENT'" in prompt
        assert "날짜 한 줄만 허용" in prompt
        assert "머리와 귀는 패널 상단 18% 영역을 침범하지 않게" not in prompt  # even panel hint should target bottom

    def test_empty_dialogue(self):
        panel = {"panel_no": 1, "scene_prompt": "장면"}
        prompt = build_panel_generation_prompt(
            "기본",
            panel,
            "제목",
            "부제목",
            "캡션",
            "single_location",
            "single_location",
            "한 현장에서 끝나는 에피소드",
            "",
            [],
            previous_panel=None,
        )
        assert "텍스트 없음" in prompt
        assert "썸네일의 직후 비트" in prompt
        assert "머리와 귀는 패널 상단 18% 영역" in prompt

    def test_includes_panel_prompt_plan_constraints(self):
        panel = {
            "panel_no": 4,
            "scene_prompt": "입국 심사대 앞에서 여권을 내미는 장면",
            "location": "입국 심사대",
            "story_role": "전",
            "key_props": ["여권"],
            "carryover_props": ["여권"],
        }
        prompt = build_panel_generation_prompt(
            "기본 프롬프트",
            panel,
            "독일 입국",
            "입국장에서 시작",
            "캡션",
            "journey",
            "journey",
            "입국장에서 출구까지 이어지는 여정",
            "",
            ["여권 잘 챙겨.", "긴장된다..."],
            previous_panel={"panel_no": 3, "location": "입국 심사 대기줄", "scene_prompt": "대기 중", "key_props": ["여권"]},
            shot_plan="장소=입국 심사대 | 핵심행동=여권 제시",
            background_text_mode="exact",
            background_text_lines=["PASSPORT CONTROL", "DOCUMENT", "NAME"],
            prop_plan_lines=[
                "이번 컷에 실제로 보여야 하는 핵심 소품: 여권",
                "연속 소품은 이전 컷과 같은 물건으로 유지: 여권 (종류, 주된 색상, 크기, 손에 든 방향, 열림/닫힘 상태를 유지)",
            ],
            pose_plan_lines=[
                "두 캐릭터 모두 골반과 상체를 세운 완전한 직립 이족보행 자세를 유지한다.",
                "앞발은 손처럼만 사용하고 바닥, 책상, 카운터, 캐리어, 레일 위에 체중을 싣지 않는다.",
            ],
            character_identity_contract_lines=[
                "콜라는 매우 짙은 검은 단색 털, 줄무늬 없음, 둥근 노란 눈을 유지한다.",
                "제로는 밝은 회색 바탕+진한 회색 태비 소용돌이 무늬, 갈색 눈을 유지한다.",
            ],
            scale_contract_lines=[
                "콜라는 언제나 제로보다 약간 더 크게 읽혀야 하며 목표 격차는 약 12%, 허용 범위는 8~15%다.",
                "원근 때문에 제로가 더 크게 읽히지 않게 두 캐릭터를 비슷한 거리 평면에 둔다.",
            ],
            character_accuracy_lines=[
                "등장인물 정확성이 배경 디테일보다 항상 우선이다.",
            ],
            action_contract_lines=[
                "장면 설명에 지정된 행동 주체를 바꾸지 않는다.",
                "장면 설명에 left/right paw가 있으면 그 손 방향을 정확히 지킨다.",
            ],
        )
        assert "사전 확정 샷 플랜" in prompt
        assert "PASSPORT CONTROL" in prompt
        assert "DOCUMENT" in prompt
        assert "사전 확정 소품 계획" in prompt
        assert "연속 소품은 이전 컷과 같은 물건으로 유지: 여권" in prompt
        assert "사전 확정 포즈 계획" in prompt
        assert "사전 확정 액션 계약" in prompt
        assert "사전 확정 캐릭터 정체성 계약" in prompt
        assert "사전 확정 상대 크기 계약" in prompt
        assert "사전 확정 등장인물 정확성 계약" in prompt
        assert "완전한 직립 이족보행" in prompt
        assert "소품의 실제성도 유지한다" in prompt
        assert "감정 표현은 얼굴과 몸짓으로만 전달" in prompt
        assert "위 목록 외의 읽을 수 있는 배경 텍스트는 만들지 않는다" in prompt


class TestPromptPlanPackage:
    def test_builds_prompt_plan_package_with_dialogue_and_background_text(self):
        brief = {
            "title": "독일 입국",
            "thumbnail_subtitle": "입국장에서 시작",
            "episode_scope": "journey",
            "subtitle_scope": "journey",
            "scope_summary": "공항 입국부터 출구까지 이동하는 여정",
            "image_prompt": "base prompt",
            "thumbnail_scene_prompt": "입국장 전광판 아래에서 안내를 확인하는 장면",
            "caption": "독일 입국 테스트",
            "hashtags": ["#독일생활"],
            "character_notes": "",
            "panels": [
                {
                    "panel_no": 1,
                    "story_role": "기",
                    "location": "입국장",
                    "scene_prompt": "arrivals board and passport in hand",
                    "key_props": ["여권"],
                    "carryover_props": [],
                    "speaker_dialogues": [
                        {"speaker": "kolla", "dialogue_lines": ["드디어 왔다."]},
                        {"speaker": "zero", "dialogue_lines": ["줄 길어 보인다."]},
                    ],
                },
                *[
                    {
                        "panel_no": index,
                        "story_role": "전개",
                        "location": f"장소 {index}",
                        "scene_prompt": f"scene {index}",
                        "key_props": [],
                        "carryover_props": [],
                        "speaker_dialogues": [
                            {"speaker": "kolla", "dialogue_lines": [f"콜라 {index}"]},
                            {"speaker": "zero", "dialogue_lines": [f"제로 {index}"]},
                        ],
                    }
                    for index in range(2, 7)
                ],
            ],
        }

        prompt_plan = _build_prompt_plan_package(brief, sanitized_topic="독일 입국")

        assert prompt_plan["validation"]["has_issues"] is False
        assert prompt_plan["thumbnail"]["thumbnail_blueprint_lines"]
        assert prompt_plan["thumbnail"]["pose_plan_lines"]
        assert prompt_plan["thumbnail"]["character_accuracy_lines"]
        assert prompt_plan["character_identity_contract"]
        assert prompt_plan["scale_contract"]
        assert "ARRIVALS" in prompt_plan["thumbnail"]["final_prompt"]
        assert "사전 확정 썸네일 블루프린트" in prompt_plan["thumbnail"]["final_prompt"]
        assert "사전 확정 캐릭터 정체성 계약" in prompt_plan["thumbnail"]["final_prompt"]
        assert "사전 확정 상대 크기 계약" in prompt_plan["thumbnail"]["final_prompt"]
        assert "사전 확정 등장인물 정확성 계약" in prompt_plan["thumbnail"]["final_prompt"]
        assert any("근육질" in line for line in prompt_plan["character_identity_contract"])
        assert any("남색" in line for line in prompt_plan["character_identity_contract"])
        assert any("불변 기준" in line for line in prompt_plan["character_identity_contract"])
        assert any("여분의 손/팔/앞발 또는 사라진 팔" in line for line in prompt_plan["character_identity_contract"])
        assert any("배경 디테일보다 항상 우선" in line for line in prompt_plan["thumbnail"]["character_accuracy_lines"])
        assert any("정면 또는 3/4 시점" in line for line in prompt_plan["thumbnail"]["thumbnail_blueprint_lines"])
        panel_1_plan = next(item for item in prompt_plan["panels"] if item["panel_no"] == 1)
        assert panel_1_plan["dialogue_lines"] == ["드디어 왔다.", "줄 길어 보인다."]
        assert "PASSPORT" in panel_1_plan["final_prompt"] or "VISA" in panel_1_plan["final_prompt"]
        assert "사전 확정 샷 플랜" in panel_1_plan["final_prompt"]

    def test_prompt_plan_tracks_episode_prop_registry_and_panel_prop_plan(self):
        brief = {
            "title": "생활 소동",
            "thumbnail_subtitle": "이동 중 생긴 해프닝",
            "episode_scope": "journey",
            "subtitle_scope": "journey",
            "scope_summary": "출발 지점에서 다음 장소로 이동하는 짧은 여정",
            "image_prompt": "base prompt",
            "thumbnail_scene_prompt": "안내 표지판 앞에서 이동 경로를 확인하는 장면",
            "caption": "이동 중 생긴 해프닝 테스트",
            "hashtags": ["#생활에피소드"],
            "character_notes": "",
            "panels": [
                {
                    "panel_no": 1,
                    "story_role": "기",
                    "location": "대기 구역",
                    "scene_prompt": "blue folder and red ticket in hand",
                    "key_props": ["파란 파일", "빨간 티켓"],
                    "carryover_props": [],
                    "speaker_dialogues": [
                        {"speaker": "kolla", "dialogue_lines": ["드디어 왔다."]},
                        {"speaker": "zero", "dialogue_lines": ["줄 길어 보인다."]},
                    ],
                },
                {
                    "panel_no": 2,
                    "story_role": "승",
                    "location": "안내 데스크 앞",
                    "scene_prompt": "still holding the same blue folder and red ticket",
                    "key_props": ["파란 파일", "빨간 티켓"],
                    "carryover_props": ["파란 파일", "빨간 티켓"],
                    "speaker_dialogues": [
                        {"speaker": "kolla", "dialogue_lines": ["별거 아니지!"]},
                        {"speaker": "zero", "dialogue_lines": ["서류 챙겨."]},
                    ],
                },
                *[
                    {
                        "panel_no": index,
                        "story_role": "전개",
                        "location": f"장소 {index}",
                        "scene_prompt": f"scene {index}",
                        "key_props": [],
                        "carryover_props": [],
                        "speaker_dialogues": [
                            {"speaker": "kolla", "dialogue_lines": [f"콜라 {index}"]},
                            {"speaker": "zero", "dialogue_lines": [f"제로 {index}"]},
                        ],
                    }
                    for index in range(3, 7)
                ],
            ],
        }

        prompt_plan = _build_prompt_plan_package(brief, sanitized_topic="생활 소동")

        registry_names = [item["name"] for item in prompt_plan["episode_prop_registry"]]
        assert "파란 파일" in registry_names
        panel_2_plan = next(item for item in prompt_plan["panels"] if item["panel_no"] == 2)
        assert panel_2_plan["carryover_props"] == ["파란 파일", "빨간 티켓"]
        assert any("파란 파일" in line for line in panel_2_plan["prop_plan_lines"])
        assert any("빨간 티켓" in line for line in panel_2_plan["prop_plan_lines"])
        assert any("직립 이족보행" in line for line in panel_2_plan["pose_plan_lines"])
        assert any("배경 디테일보다 항상 우선" in line for line in panel_2_plan["character_accuracy_lines"])
        assert any("행동 주체" in line for line in panel_2_plan["action_contract_lines"])
        assert "사전 확정 캐릭터 정체성 계약" in panel_2_plan["final_prompt"]
        assert "사전 확정 상대 크기 계약" in panel_2_plan["final_prompt"]
        assert "사전 확정 등장인물 정확성 계약" in panel_2_plan["final_prompt"]

    def test_prompt_plan_adds_pose_and_prop_realism_lines_for_action_sensitive_scene(self):
        brief = {
            "title": "상태 확인",
            "thumbnail_subtitle": "문서와 소품 점검",
            "episode_scope": "single_location",
            "subtitle_scope": "single_location",
            "scope_summary": "한 장소에서 문서와 소품 상태를 확인하는 장면",
            "image_prompt": "base prompt",
            "thumbnail_scene_prompt": "안내판 앞 점검 장면",
            "caption": "상태 확인 테스트",
            "hashtags": ["#생활에피소드"],
            "character_notes": "",
            "panels": [
                {
                    "panel_no": 1,
                    "story_role": "기",
                    "location": "확인 데스크",
                    "scene_prompt": "open red passport with visible blue stamp while pointing to the document at the counter",
                    "key_props": ["빨간 여권", "파란 도장"],
                    "carryover_props": [],
                    "speaker_dialogues": [
                        {"speaker": "kolla", "dialogue_lines": ["여기 봐봐."]},
                        {"speaker": "zero", "dialogue_lines": ["도장 잘 보인다."]},
                    ],
                },
                *[
                    {
                        "panel_no": index,
                        "story_role": "전개",
                        "location": f"장소 {index}",
                        "scene_prompt": f"scene {index}",
                        "key_props": [],
                        "carryover_props": [],
                        "speaker_dialogues": [
                            {"speaker": "kolla", "dialogue_lines": [f"콜라 {index}"]},
                            {"speaker": "zero", "dialogue_lines": [f"제로 {index}"]},
                        ],
                    }
                    for index in range(2, 7)
                ],
            ],
        }

        prompt_plan = _build_prompt_plan_package(brief, sanitized_topic="상태 확인")
        panel_1_plan = next(item for item in prompt_plan["panels"] if item["panel_no"] == 1)

        assert any("열림/닫힘 상태" in line for line in panel_1_plan["prop_plan_lines"])
        assert any("직립" in line for line in panel_1_plan["pose_plan_lines"])
        assert "사전 확정 포즈 계획" in panel_1_plan["final_prompt"]

    def test_prompt_plan_adds_action_contract_for_hand_and_prop_owner(self):
        brief = {
            "title": "표 확인",
            "thumbnail_subtitle": "손동작 테스트",
            "episode_scope": "single_location",
            "subtitle_scope": "single_location",
            "scope_summary": "한 장소에서 표와 문서를 보여주는 장면",
            "image_prompt": "base prompt",
            "thumbnail_scene_prompt": "teaser shot facing the sign",
            "caption": "액션 계약 테스트",
            "hashtags": ["#생활에피소드"],
            "character_notes": "",
            "panels": [
                {
                    "panel_no": 1,
                    "story_role": "기",
                    "location": "안내 창구",
                    "scene_prompt": "Zero holds the blue passport and the entry document",
                    "key_props": ["파란 여권", "입국 서류"],
                    "carryover_props": [],
                    "speaker_dialogues": [
                        {"speaker": "kolla", "dialogue_lines": ["여기 보여줘."]},
                        {"speaker": "zero", "dialogue_lines": ["왼쪽으로 가리킬게."]},
                    ],
                },
                {
                    "panel_no": 2,
                    "story_role": "승",
                    "location": "안내 창구",
                    "scene_prompt": "Zero presents the blue passport and points with the left paw to the entry document",
                    "key_props": ["파란 여권", "입국 서류"],
                    "carryover_props": ["파란 여권", "입국 서류"],
                    "speaker_dialogues": [
                        {"speaker": "kolla", "dialogue_lines": ["여기 보여줘."]},
                        {"speaker": "zero", "dialogue_lines": ["왼쪽으로 가리킬게."]},
                    ],
                },
                *[
                    {
                        "panel_no": index,
                        "story_role": "전개",
                        "location": f"장소 {index}",
                        "scene_prompt": f"scene {index}",
                        "key_props": [],
                        "carryover_props": [],
                        "speaker_dialogues": [
                            {"speaker": "kolla", "dialogue_lines": [f"콜라 {index}"]},
                            {"speaker": "zero", "dialogue_lines": [f"제로 {index}"]},
                        ],
                    }
                    for index in range(3, 7)
                ],
            ],
        }

        prompt_plan = _build_prompt_plan_package(brief, sanitized_topic="표 확인")
        panel_2_plan = next(item for item in prompt_plan["panels"] if item["panel_no"] == 2)

        assert any("left/right paw" in line for line in panel_2_plan["action_contract_lines"])
        assert any("행동 주체" in line for line in panel_2_plan["action_contract_lines"])
        assert any("같은 캐릭터가 계속 들고 있어야 한다" in line for line in panel_2_plan["action_contract_lines"])
        assert any("여분의 손/팔" in line for line in panel_2_plan["action_contract_lines"])
        assert any("중복 소유" in line for line in panel_2_plan["action_contract_lines"])

    def test_prompt_plan_adds_count_orientation_and_underarm_contracts(self):
        brief = {
            "title": "문서 소동",
            "thumbnail_subtitle": "여권 개수 테스트",
            "episode_scope": "single_location",
            "subtitle_scope": "single_location",
            "scope_summary": "한 장소에서 소품 개수와 방향이 중요한 장면",
            "image_prompt": "base prompt",
            "thumbnail_scene_prompt": "teaser shot with two blue passports",
            "caption": "세부 액션 테스트",
            "hashtags": ["#생활에피소드"],
            "character_notes": "",
            "panels": [
                {
                    "panel_no": 1,
                    "story_role": "기",
                    "location": "창구",
                    "scene_prompt": "Zero holds two blue passports while Kolla keeps one passport tucked under his arm and upside down, Zero does a one-paw facepalm",
                    "key_props": ["파란 여권 2개"],
                    "carryover_props": [],
                    "speaker_dialogues": [
                        {"speaker": "kolla", "dialogue_lines": ["이거 맞지?"]},
                        {"speaker": "zero", "dialogue_lines": ["그 손 말고!"]},
                    ],
                },
                *[
                    {
                        "panel_no": index,
                        "story_role": "전개",
                        "location": f"장소 {index}",
                        "scene_prompt": f"scene {index}",
                        "key_props": [],
                        "carryover_props": [],
                        "speaker_dialogues": [
                            {"speaker": "kolla", "dialogue_lines": [f"콜라 {index}"]},
                            {"speaker": "zero", "dialogue_lines": [f"제로 {index}"]},
                        ],
                    }
                    for index in range(2, 7)
                ],
            ],
        }

        prompt_plan = _build_prompt_plan_package(brief, sanitized_topic="문서 소동")
        panel_1_plan = next(item for item in prompt_plan["panels"] if item["panel_no"] == 1)

        assert any("소품 2개" in line for line in panel_1_plan["action_contract_lines"])
        assert any("겨드랑이에 끼우는 소품" in line for line in panel_1_plan["action_contract_lines"])
        assert any("뒤집혀 있어야" in line for line in panel_1_plan["action_contract_lines"])
        assert any("정확히 한 앞발만 얼굴에 닿아야" in line for line in panel_1_plan["action_contract_lines"])

    def test_prompt_plan_builds_thumbnail_blueprint_and_global_contracts(self):
        brief = {
            "title": "이동 시작",
            "thumbnail_subtitle": "출발 장면",
            "episode_scope": "journey",
            "subtitle_scope": "journey",
            "scope_summary": "출발 지점에서 다음 장소로 이동하는 이야기",
            "image_prompt": "base prompt",
            "thumbnail_scene_prompt": "wide high-angle teaser shot at the transit hall entrance",
            "caption": "출발 테스트",
            "hashtags": ["#생활에피소드"],
            "character_notes": "",
            "panels": [
                {
                    "panel_no": 1,
                    "story_role": "기",
                    "location": "출발 홀",
                    "scene_prompt": "front eye-level shot while holding a green passport and blue folder",
                    "key_props": ["초록 여권", "파란 폴더"],
                    "carryover_props": [],
                    "speaker_dialogues": [
                        {"speaker": "kolla", "dialogue_lines": ["가보자."]},
                        {"speaker": "zero", "dialogue_lines": ["긴장된다."]},
                    ],
                },
                *[
                    {
                        "panel_no": index,
                        "story_role": "전개",
                        "location": f"장소 {index}",
                        "scene_prompt": f"scene {index}",
                        "key_props": ["초록 여권"] if index < 4 else [],
                        "carryover_props": ["초록 여권"] if index < 4 else [],
                        "speaker_dialogues": [
                            {"speaker": "kolla", "dialogue_lines": [f"콜라 {index}"]},
                            {"speaker": "zero", "dialogue_lines": [f"제로 {index}"]},
                        ],
                    }
                    for index in range(2, 7)
                ],
            ],
        }

        prompt_plan = _build_prompt_plan_package(brief, sanitized_topic="이동 시작")

        assert any("패널 1 주요 장소" in line for line in prompt_plan["thumbnail"]["thumbnail_blueprint_lines"])
        assert any("패널 1의 핵심행동" in line for line in prompt_plan["thumbnail"]["thumbnail_blueprint_lines"])
        assert any("초록 여권" in line for line in prompt_plan["thumbnail"]["thumbnail_blueprint_lines"])
        assert any("갈색 눈" in line for line in prompt_plan["character_identity_contract"])
        assert any("일반적인 집고양이 기반의 둥글고 자연스러운 체형" in line for line in prompt_plan["character_identity_contract"])
        assert any("머리 꼭대기 높이" in line for line in prompt_plan["scale_contract"])


class TestListCharacterReferenceFiles:
    def test_empty_dir(self):
        settings = _make_dummy_settings()
        result = list_character_reference_files(settings)
        assert result == []

    def test_finds_preferred_files(self, tmp_path):
        char_dir = tmp_path / "characters"
        char_dir.mkdir()
        (char_dir / "black_cat.png").write_bytes(b"fake")
        (char_dir / "gray_cat.png").write_bytes(b"fake")
        (char_dir / "other.jpg").write_bytes(b"fake")

        settings = _make_dummy_settings(character_assets_dir=char_dir)
        result = list_character_reference_files(settings)
        names = [p.name for p in result]
        assert names[0] == "black_cat.png"
        assert names[1] == "gray_cat.png"
        assert "other.jpg" in names


class TestCharacterCompositionChecks:
    def test_flags_extra_missing_limbs_and_action_mismatch_as_issues(self):
        client = object.__new__(clients_module.GeminiOcrClient)
        client._generate_review_text = lambda *_args, **_kwargs: (
            '{"has_issues": false, "issues": [], "edit_instruction": "", '
            '"kolla_count": 1, "zero_count": 1, "extra_character_count": 0, '
            '"duplicate_scene_detected": false, "bipedal_ok": true, "scene_match_ok": true, '
            '"kolla_larger_than_zero_ok": true, "silhouette_extra_count": 0, '
            '"kolla_size_gap_band_ok": true, "estimated_size_gap_percent": 12, '
            '"duplicate_character_detected": false, "upper_margin_character_detected": false, '
            '"quadruped_detected": false, "quadruped_subjects": [], "upright_pose_ok": true, '
            '"forepaws_used_as_hands_ok": true, "unsafe_surface_pose_detected": false, '
            '"reference_like_copy_detected": false, "extra_limb_detected": true, '
            '"missing_limb_detected": true, "action_owner_ok": false, "left_right_action_ok": false, '
            '"cutin_or_sticker_detected": false, "partial_body_duplicate_detected": false}'
        )

        result = clients_module.GeminiOcrClient.check_character_composition(
            client,
            b"fake-image",
            "Zero points with the left paw while holding the blue folder",
        )

        assert result["has_issues"] is True
        assert result["extra_limb_detected"] is True
        assert result["missing_limb_detected"] is True
        assert result["action_owner_ok"] is False
        assert result["left_right_action_ok"] is False
        assert "correct character holds or points with the correct paw" in result["edit_instruction"]


class TestCharacterPriorityGateErrors:
    def test_flags_extra_or_missing_limbs_as_priority_gate_error(self):
        errors = _build_character_priority_gate_errors(
            {
                "has_issues": True,
                "extra_limb_detected": True,
                "missing_limb_detected": False,
                "issues": ["제로 팔이 세 개처럼 보입니다."],
                "edit_instruction": "Remove the extra limb.",
            },
            {"has_issues": False},
        )

        assert any("해부학/팔다리 수 품질 게이트" in error for error in errors)


class TestStabilizeGeneratedImage:
    def test_runs_character_reference_and_background_stabilization_round(self, monkeypatch: pytest.MonkeyPatch):
        settings = _make_dummy_settings()
        image_client = object()
        ocr_client = object()
        quality_states = iter(
            [
                {
                    "character_check": {"has_issues": True},
                    "reference_check": {"has_issues": True},
                    "background_check": {"has_errors": True},
                    "quality_errors": ["캐릭터 구성", "참조", "배경"],
                },
                {
                    "character_check": {"has_issues": False},
                    "reference_check": {"has_issues": False},
                    "background_check": {"has_errors": False},
                    "quality_errors": [],
                },
            ]
        )
        calls: list[str] = []

        monkeypatch.setattr(
            pipeline_module,
            "_run_generation_quality_checks",
            lambda *_args, **_kwargs: next(quality_states),
        )
        monkeypatch.setattr(
            pipeline_module,
            "correct_character_composition",
            lambda *_args, **_kwargs: (b"comp", {"has_issues": False, "edit_instruction": ""}),
        )
        monkeypatch.setattr(
            pipeline_module,
            "correct_character_reference_consistency",
            lambda *_args, **_kwargs: (calls.append("reference") or b"ref", {"has_issues": False, "edit_instruction": ""}),
        )
        monkeypatch.setattr(
            pipeline_module,
            "correct_background_text",
            lambda *_args, **_kwargs: (calls.append("background") or b"bg", {"has_errors": False, "corrections": []}),
        )
        monkeypatch.setattr(
            pipeline_module,
            "correct_character_composition",
            lambda *_args, **_kwargs: (calls.append("composition") or b"comp", {"has_issues": False, "edit_instruction": ""}),
        )

        image_bytes, state = _stabilize_generated_image(
            image_client,
            ocr_client,
            image_bytes=b"start",
            scene_prompt="scene prompt",
            reference_parts=[(b"ref", "image/png")],
            reference_files=[],
            max_rounds=1,
        )

        assert image_bytes == b"bg"
        assert state["quality_errors"] == []
        assert calls == ["composition", "reference", "background"]
        assert state["stabilization_rounds"][0]["before_quality_errors"] == ["캐릭터 구성", "참조", "배경"]


class TestCandidateSelection:
    def test_prefers_character_accuracy_over_background_cleanliness(self):
        worse_character = {
            "character_check": {"has_issues": True, "issues": ["size", "biped"]},
            "reference_check": {"has_issues": False, "issues": []},
            "background_check": {"has_errors": False, "corrections": []},
        }
        better_character = {
            "character_check": {"has_issues": False, "issues": []},
            "reference_check": {"has_issues": False, "issues": []},
            "background_check": {"has_errors": True, "corrections": [{"found": "ABC", "correct": "INFO"}]},
        }

        assert _is_better_candidate(worse_character, better_character) is True

    def test_prefers_fewer_reference_issues_before_background(self):
        current = {
            "character_check": {"has_issues": False, "issues": []},
            "reference_check": {"has_issues": True, "issues": ["swirl mismatch"]},
            "background_check": {"has_errors": False, "corrections": []},
        }
        challenger = {
            "character_check": {"has_issues": False, "issues": []},
            "reference_check": {"has_issues": False, "issues": []},
            "background_check": {"has_errors": True, "corrections": [{"found": "PASSPORT", "correct": "PASSPORT"}]},
        }

        assert _is_better_candidate(current, challenger) is True


class TestGenerationQualityGateMode:
    def test_can_ignore_background_errors_during_generation_gate(self, monkeypatch: pytest.MonkeyPatch):
        client = object.__new__(clients_module.GeminiOcrClient)
        monkeypatch.setattr(
            clients_module.GeminiOcrClient,
            "check_character_composition",
            lambda *_args, **_kwargs: {"has_issues": False, "issues": [], "edit_instruction": ""},
        )
        monkeypatch.setattr(
            clients_module.GeminiOcrClient,
            "check_character_reference_consistency",
            lambda *_args, **_kwargs: {"has_issues": False, "issues": [], "edit_instruction": ""},
        )
        monkeypatch.setattr(
            clients_module.GeminiOcrClient,
            "check_background_text",
            lambda *_args, **_kwargs: {
                "has_errors": True,
                "background_texts": ["BROKEN"],
                "corrections": [{"found": "BROKEN", "correct": "INFO", "reason": "ocr"}],
            },
        )

        result = pipeline_module._run_generation_quality_checks(
            client,
            b"fake-image",
            "scene prompt",
            reference_parts=[(b"ref", "image/png")],
            include_background_in_errors=False,
        )

        assert result["character_check"]["has_issues"] is False
        assert result["reference_check"]["has_issues"] is False
        assert result["background_check"]["has_errors"] is True
        assert result["quality_errors"] == []


class TestCharacterPriorityGateErrors:
    def test_splits_generation_priority_errors_into_five_character_axes(self):
        character_check = {
            "has_issues": True,
            "issues": ["자세와 손동작이 틀렸습니다."],
            "edit_instruction": "Fix character pose and ownership.",
            "bipedal_ok": False,
            "upright_pose_ok": False,
            "forepaws_used_as_hands_ok": False,
            "unsafe_surface_pose_detected": True,
            "quadruped_detected": True,
            "kolla_larger_than_zero_ok": False,
            "kolla_size_gap_band_ok": False,
            "action_owner_ok": False,
            "left_right_action_ok": False,
        }
        reference_check = {
            "has_issues": True,
            "issues": ["참조 외형이 틀렸습니다."],
            "edit_instruction": "Match references.",
        }

        errors = _build_character_priority_gate_errors(character_check, reference_check)

        assert any("캐릭터 참조 일관성" in item for item in errors)
        assert any("이족보행 품질 게이트" in item for item in errors)
        assert any("사족보행 방지 품질 게이트" in item for item in errors)
        assert any("상대 크기 비율 품질 게이트" in item for item in errors)
        assert any("손/소품 주체 품질 게이트" in item for item in errors)


class TestProgressiveAnchorBundle:
    def test_keeps_thumbnail_and_last_two_panels_as_anchor_references(self):
        master_refs = [(b"master-kolla", "image/png"), (b"master-zero", "image/png")]
        approved = [
            {"slide_type": "thumbnail", "image_bytes": b"thumb", "mime_type": "image/png"},
            {"slide_type": "panel", "panel_no": 1, "image_bytes": b"p1", "mime_type": "image/png", "location": "A"},
            {"slide_type": "panel", "panel_no": 2, "image_bytes": b"p2", "mime_type": "image/png", "location": "B"},
            {"slide_type": "panel", "panel_no": 3, "image_bytes": b"p3", "mime_type": "image/png", "location": "C"},
        ]

        refs, descriptions = _build_progressive_anchor_bundle(master_refs, approved)

        assert refs == [
            (b"master-kolla", "image/png"),
            (b"master-zero", "image/png"),
            (b"thumb", "image/png"),
            (b"p2", "image/png"),
            (b"p3", "image/png"),
        ]
        assert descriptions[0].startswith("승인된 썸네일 앵커")
        assert any("승인된 패널 2 앵커" in item for item in descriptions)
        assert any("승인된 패널 3 앵커" in item for item in descriptions)


class TestGeneratePanelsProgressively:
    def test_uses_thumbnail_then_prior_panels_as_progressive_anchors(self, monkeypatch, tmp_path):
        captured_refs: list[list[bytes]] = []
        captured_anchor_descriptions: list[list[str]] = []

        def fake_generate_panel_base_image(*args, **kwargs):
            captured_refs.append([part[0] for part in kwargs["reference_parts"]])
            captured_anchor_descriptions.append(list(kwargs.get("anchor_reference_descriptions") or []))
            panel_no = int(kwargs["panel"]["panel_no"])
            return {
                "base_bytes": f"panel-{panel_no}".encode(),
                "prompt": f"panel {panel_no}",
                "image_text": "",
                "character_check": {"has_issues": False, "issues": [], "edit_instruction": ""},
                "reference_check": {"has_issues": False, "issues": [], "edit_instruction": ""},
                "background_check": {"has_errors": False, "background_texts": [], "corrections": []},
            }

        monkeypatch.setattr(pipeline_module, "_generate_panel_base_image", fake_generate_panel_base_image)
        monkeypatch.setattr(pipeline_module, "save_image", lambda *_args, **_kwargs: None)

        artifacts = pipeline_module.create_artifact_paths("run-test123", "2026-W12")
        try:
            panels = [
                {"panel_no": 1, "scene_prompt": "장면 1", "location": "A"},
                {"panel_no": 2, "scene_prompt": "장면 2", "location": "B"},
                {"panel_no": 3, "scene_prompt": "장면 3", "location": "C"},
            ]
            settings = WebtoonSettings.from_env(required_fields=set())
            panel_items, notes, anchors = _generate_panels_progressively(
                image_client=object(),
                ocr_client=object(),
                settings=settings,
                brief={
                    "title": "t",
                    "caption": "",
                    "thumbnail_subtitle": "",
                    "episode_scope": "journey",
                    "subtitle_scope": "journey",
                    "scope_summary": "",
                    "character_notes": "",
                },
                current_panels=panels,
                prompt_plan=None,
                master_reference_parts=[(b"master-k", "image/png"), (b"master-z", "image/png")],
                reference_files=[],
                approved_anchor_images=[
                    {
                        "slide_type": "thumbnail",
                        "panel_no": None,
                        "image_bytes": b"thumb",
                        "mime_type": "image/png",
                        "scene_prompt": "thumb",
                        "location": "thumbnail",
                    }
                ],
                artifact_paths=artifacts,
            )

            assert [item["panel_no"] for item in panel_items] == [1, 2, 3]
            assert len(notes) == 3
            assert len(anchors) == 4
            assert captured_refs[0] == [b"master-k", b"master-z", b"thumb"]
            assert captured_refs[1] == [b"master-k", b"master-z", b"thumb", b"panel-1"]
            assert captured_refs[2] == [b"master-k", b"master-z", b"thumb", b"panel-1", b"panel-2"]
            assert any("승인된 썸네일 앵커" in item for item in captured_anchor_descriptions[0])
            assert any("승인된 패널 1 앵커" in item for item in captured_anchor_descriptions[1])
            assert any("승인된 패널 2 앵커" in item for item in captured_anchor_descriptions[2])
        finally:
            import shutil

            shutil.rmtree(artifacts.run_dir, ignore_errors=True)


class TestBuildBubbleOnlyOcrImage:
    def _make_test_image_bytes(self, size=(800, 600)):
        img = Image.new("RGB", size, color=(255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_no_bubbles_returns_original(self):
        image_bytes = self._make_test_image_bytes()
        result = build_bubble_only_ocr_image(image_bytes, [])
        assert result == image_bytes

    def test_with_bubble_boxes(self):
        image_bytes = self._make_test_image_bytes()
        layout = [{"bubble_box": [100, 100, 300, 200]}]
        result = build_bubble_only_ocr_image(image_bytes, layout)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"
        assert result != image_bytes

    def test_multiple_bubbles(self):
        image_bytes = self._make_test_image_bytes()
        layout = [
            {"bubble_box": [10, 10, 200, 100]},
            {"bubble_box": [10, 200, 200, 300]},
        ]
        result = build_bubble_only_ocr_image(image_bytes, layout)
        with Image.open(io.BytesIO(result)) as img:
            assert img.height > 100  # combined height of two crops


class TestApplyCorrectedDialogues:
    def test_prefers_corrected_speaker_dialogues_over_flat_lines(self):
        panel = {
            "speaker_dialogues": [
                {"speaker": "kolla", "dialogue_lines": ["별거 있겠어?", "다 사람 사는 데야."]},
                {"speaker": "zero", "dialogue_lines": ["혹시 몰라..."]},
            ],
            "dialogue_lines": ["별거 있겠어?", "다 사람 사는 데야.", "혹시 몰라..."],
        }

        result = _apply_corrected_dialogues(
            panel,
            ["별거 있겠어?", "다 사람", "사는 데야.", "혹시 몰라..."],
            corrected_speaker_dialogues=[
                {"speaker": "kolla", "dialogue_lines": ["별거 있겠어?", "다 사람 사는 데야."]},
                {"speaker": "zero", "dialogue_lines": ["혹시 몰라..."]},
            ],
        )

        assert result["speaker_dialogues"][0]["dialogue_lines"] == ["별거 있겠어?", "다 사람 사는 데야."]
        assert result["speaker_dialogues"][1]["dialogue_lines"] == ["혹시 몰라..."]


class TestBuildPublishDecision:
    def _base_kwargs(self, *, publish_requested=True):
        return {
            "publish_requested": publish_requested,
            "brief": {"story_review": {"has_issues": False}},
            "thumbnail_checks": {
                "character": {"has_issues": False},
                "reference": {"has_issues": False},
                "background": {"has_errors": False},
                "distinction": {"has_issues": False},
            },
            "panel_generation_notes": [
                {
                    "slide_type": "panel",
                    "panel_no": 1,
                    "character_composition_check": {"has_issues": False},
                    "character_reference_check": {"has_issues": False},
                    "background_text_check": {"has_errors": False},
                }
            ],
            "panel_reviews": [
                {
                    "panel_no": 1,
                    "review": {
                        "rerender_required": False,
                        "confidence": 0.98,
                        "issues": [],
                    },
                }
            ],
            "bubble_review": {"has_issues": False, "issues": [], "soft_score": 0.95},
            "final_package_review": {
                "hard_blockers": [],
                "soft_scores": {
                    "topic_alignment": 0.92,
                    "story_flow": 0.9,
                    "background_progression": 0.88,
                    "thumbnail_distinction": 0.93,
                    "bubble_placement": 0.91,
                    "ending_resolution": 0.9,
                },
                "notes": [],
                "summary": "ok",
            },
        }

    def test_allows_publish_when_hard_gates_clear_and_soft_scores_high(self):
        decision = _build_publish_decision(**self._base_kwargs())

        assert decision["quality_decision"] == "allow"
        assert decision["publish_decision"] == "allow"
        assert decision["hard_blockers"] == []

    def test_returns_manual_review_when_soft_scores_are_low(self):
        kwargs = self._base_kwargs()
        kwargs["final_package_review"]["soft_scores"]["story_flow"] = 0.55

        decision = _build_publish_decision(**kwargs)

        assert decision["quality_decision"] == "manual_review"
        assert decision["publish_decision"] == "manual_review"
        assert any("story_flow" in item for item in decision["manual_review_reasons"])

    def test_blocks_publish_when_hard_gate_remains(self):
        kwargs = self._base_kwargs()
        kwargs["panel_generation_notes"][0]["character_composition_check"] = {
            "has_issues": True,
            "issues": ["사족보행"],
            "edit_instruction": "fix",
        }

        decision = _build_publish_decision(**kwargs)

        assert decision["quality_decision"] == "blocked"
        assert decision["publish_decision"] == "blocked"
        assert any("패널 1 캐릭터 구성" in item for item in decision["hard_blockers"])

    def test_marks_final_review_as_misaligned_when_stage_gate_still_blocks(self):
        kwargs = self._base_kwargs()
        kwargs["panel_generation_notes"][0]["character_composition_check"] = {
            "has_issues": True,
            "issues": ["사족보행"],
            "edit_instruction": "fix",
        }

        decision = _build_publish_decision(**kwargs)

        assert any("stage gate" in note for note in decision["final_package_review"]["notes"])
        assert "게시는 차단되었습니다" in decision["final_package_review"]["summary"]

    def test_publish_false_maps_to_skip(self):
        decision = _build_publish_decision(**self._base_kwargs(publish_requested=False))

        assert decision["quality_decision"] == "allow"
        assert decision["publish_decision"] == "skip"


def _write_artifact(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    return path


def _make_outbound_bundle(tmp_path: Path, *, status: str) -> OutboundBundle:
    thumb_base = _write_artifact(tmp_path / "thumbnail_base_v1.png", b"thumb-base")
    thumb_final = _write_artifact(tmp_path / "thumbnail_final_v1.png", b"thumb-final")
    thumb_publish = _write_artifact(tmp_path / "thumbnail_publish_v1.jpg", b"thumb-publish")
    panel_final = _write_artifact(tmp_path / "panel_01_final_v1.png", b"panel-final")
    panel_publish = _write_artifact(tmp_path / "panel_01_publish_v1.jpg", b"panel-publish")

    artifacts = (
        OutboundArtifact(
            source_path=thumb_base,
            bundle_name=thumb_base.name,
            mime_type="image/png",
            make_public=False,
            artifact_role="thumbnail_base_png",
            slide_index=0,
            slide_type="thumbnail",
        ),
        OutboundArtifact(
            source_path=thumb_final,
            bundle_name=thumb_final.name,
            mime_type="image/png",
            make_public=False,
            artifact_role="thumbnail_final_png",
            slide_index=0,
            slide_type="thumbnail",
        ),
        OutboundArtifact(
            source_path=thumb_publish,
            bundle_name=thumb_publish.name,
            mime_type="image/jpeg",
            make_public=True,
            artifact_role="thumbnail_publish_jpg",
            slide_index=0,
            slide_type="thumbnail",
        ),
        OutboundArtifact(
            source_path=panel_final,
            bundle_name=panel_final.name,
            mime_type="image/png",
            make_public=False,
            artifact_role="panel_final_png",
            slide_index=1,
            slide_type="panel",
            panel_no=1,
        ),
        OutboundArtifact(
            source_path=panel_publish,
            bundle_name=panel_publish.name,
            mime_type="image/jpeg",
            make_public=True,
            artifact_role="panel_publish_jpg",
            slide_index=1,
            slide_type="panel",
            panel_no=1,
        ),
    )

    return OutboundBundle(
        run_id="run-abc12345",
        week_key="2026-W12",
        drive_path_parts=("2026년", "03월", "run-abc12345"),
        upload_artifacts=artifacts,
        metadata_payload={
            "character_reference_files": ["agents/webtoon/assets/characters/black_cat.png"],
            "correction_attempts": [{"correction_version": 1}],
            "quality_report": {"quality_decision": "allow" if status == "approved" else "blocked"},
            "slides": [
                {"slide_index": 0, "slide_type": "thumbnail", "file": thumb_final.name, "publish_file": thumb_publish.name},
                {"slide_index": 1, "slide_type": "panel", "panel_no": 1, "file": panel_final.name, "publish_file": panel_publish.name},
            ],
        },
        json_payloads=(
            ("panel_dialogues.json", {"panels": [{"panel_no": 1}]}),
            ("panel_video_prompts.json", {"panels": [{"panel_no": 1}]}),
        ),
        quality_report={
            "quality_decision": "allow" if status == "approved" else "blocked",
            "hard_blockers": [],
            "manual_review_reasons": [],
        },
        status=status,
        approved_by="tester" if status == "approved" else "",
        approved_at="2026-03-18T10:00:00+00:00" if status == "approved" else "",
        approved_image_version=1 if status == "approved" else "",
        slides=(
            {"slide_index": 0, "slide_type": "thumbnail", "file": thumb_final.name, "publish_file": thumb_publish.name},
            {"slide_index": 1, "slide_type": "panel", "panel_no": 1, "file": panel_final.name, "publish_file": panel_publish.name},
        ),
    )


class TestExecuteOutboundBundle:
    def test_uploads_drive_images_and_json_payloads(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        settings = _make_dummy_settings()
        bundle = _make_outbound_bundle(tmp_path, status="generation_blocked")
        uploaded_jsons: list[tuple[str, dict[str, object]]] = []

        class FakeGoogleWorkspaceClient:
            def __init__(self, incoming_settings: WebtoonSettings):
                assert incoming_settings is settings

            def ensure_drive_path(self, *parts: str):
                assert parts == bundle.drive_path_parts
                return [{"id": "folder-123", "name": parts[-1], "webViewLink": "https://drive.example/folder-123"}]

            def upload_bytes(
                self,
                folder_id: str,
                filename: str,
                data: bytes,
                mime_type: str,
                *,
                make_public: bool = False,
            ):
                assert folder_id == "folder-123"
                assert data
                response = {
                    "id": filename,
                    "name": filename,
                    "mimeType": mime_type,
                    "webViewLink": f"https://drive.example/{filename}",
                }
                if make_public:
                    response["public_download_url"] = f"https://public.example/{filename}"
                return response

            def upload_json(self, folder_id: str, filename: str, payload: dict[str, object], *, make_public: bool = False):
                assert folder_id == "folder-123"
                assert make_public is False
                uploaded_jsons.append((filename, payload))
                return {"id": filename}

        monkeypatch.setattr(pipeline_module, "GoogleWorkspaceClient", FakeGoogleWorkspaceClient)

        result = execute_outbound_bundle(settings, bundle)

        assert result["status"] == "generation_blocked"
        assert any(filename == "run_metadata.json" for filename, _payload in uploaded_jsons)
        assert any(filename == "panel_dialogues.json" for filename, _payload in uploaded_jsons)
        assert any(filename == "panel_video_prompts.json" for filename, _payload in uploaded_jsons)


class TestDialogueExportPayload:
    def test_exports_cola_instead_of_kolla(self):
        payload = pipeline_module._build_dialogue_export_payload(
            topic="독일 입국",
            title="독일 입국",
            panels=[
                {
                    "panel_no": 1,
                    "story_role": "setup",
                    "location": "공항",
                    "speaker_dialogues": [
                        {"speaker": "kolla", "dialogue_lines": ["가보자."]},
                        {"speaker": "zero", "dialogue_lines": ["긴장된다."]},
                    ],
                }
            ],
        )

        assert payload["panels"][0]["dialogues"][0]["speaker"] == "cola"
        assert payload["panels"][0]["dialogues"][0]["character_name"] == "콜라"
        assert payload["panels"][0]["dialogues"][1]["speaker"] == "zero"


class TestSimplifyBackgroundCorrection:
    def test_keeps_short_precise_label(self):
        result = _simplify_background_correction(
            "Willkommen in Deutschand",
            "Willkommen in Deutschland",
            "sign typo",
        )

        assert result == "Willkommen in Deutschland"

    def test_simplifies_verbose_display_instruction(self):
        result = _simplify_background_correction(
            "Flight Indormasoon Onurdtseave",
            "This digital display screen should show legible flight information. It could be simplified to 'Departure Information' as a header, followed by a few lines of generic but readable flight details.",
            "The text on the digital display screen is entirely gibberish and unreadable placeholder text.",
        )

        assert result == "Use one short header and up to three short rows only; no microtext."

    def test_simplifies_document_instruction(self):
        result = _simplify_background_correction(
            "DatoGebort Obördcox 301",
            "[Passport holder name and ID details - placeholder text]",
            "passport details unreadable",
        )

        assert result == "Use one to three large document labels only; keep the rest blank."


class TestRunWebtoonPipelineReferences:
    def test_panels_do_not_use_previous_panel_image_as_reference(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        def _png_bytes(color: tuple[int, int, int]) -> bytes:
            image = Image.new("RGB", (8, 8), color=color)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return buffer.getvalue()

        ref_dir = tmp_path / "characters"
        ref_dir.mkdir()
        ref_a = ref_dir / "kolla.png"
        ref_b = ref_dir / "zero.png"
        ref_a.write_bytes(_png_bytes((0, 0, 0)))
        ref_b.write_bytes(_png_bytes((128, 128, 128)))

        settings = _make_dummy_settings(character_assets_dir=ref_dir, max_correction_attempts=0, api_parallelism=1)
        source_png = _png_bytes((255, 0, 0))
        render_png = _png_bytes((0, 255, 0))
        image_calls: list[dict[str, Any]] = []

        class FakeTextClient:
            def __init__(self, incoming_settings: WebtoonSettings):
                assert incoming_settings is settings

            def build_creative_brief(self, topic: str) -> dict[str, Any]:
                return {
                    "title": "독일 입국",
                    "thumbnail_subtitle": "입국장에서 시작",
                    "episode_scope": "journey",
                    "subtitle_scope": "journey",
                    "scope_summary": "공항 입국부터 이동까지 이어지는 여정",
                    "image_prompt": "base prompt",
                    "thumbnail_scene_prompt": "입국장 대표 장면",
                    "caption": "독일 입국 테스트",
                    "hashtags": ["#독일생활"],
                    "character_notes": "",
                    "panels": [
                        {
                            "panel_no": 1,
                            "story_role": "전개",
                            "location": "비행기 출구",
                            "scene_prompt": "scene 1 passport and ticket in hand",
                            "key_props": ["passport", "ticket"],
                            "carryover_props": [],
                            "speaker_dialogues": [
                                {"speaker": "kolla", "dialogue_lines": ["콜라 1"]},
                                {"speaker": "zero", "dialogue_lines": ["제로 1"]},
                            ],
                        },
                        {
                            "panel_no": 2,
                            "story_role": "전개",
                            "location": "에스컬레이터 복도",
                            "scene_prompt": "scene 2 still holding passport and ticket while walking",
                            "key_props": ["passport", "ticket"],
                            "carryover_props": ["passport", "ticket"],
                            "speaker_dialogues": [
                                {"speaker": "kolla", "dialogue_lines": ["콜라 2"]},
                                {"speaker": "zero", "dialogue_lines": ["제로 2"]},
                            ],
                        },
                        *[
                            {
                                "panel_no": index,
                                "story_role": "전개",
                                "location": f"장소 {index}",
                                "scene_prompt": f"scene {index}",
                                "key_props": [],
                                "carryover_props": [],
                                "speaker_dialogues": [
                                    {"speaker": "kolla", "dialogue_lines": [f"콜라 {index}"]},
                                    {"speaker": "zero", "dialogue_lines": [f"제로 {index}"]},
                                ],
                            }
                            for index in range(3, 7)
                        ],
                    ],
                    "model": "fake-llm",
                    "story_review": {"has_issues": False},
                }

            def revise_creative_brief_for_quality(
                self,
                _topic: str,
                brief: dict[str, Any],
                **_kwargs: Any,
            ) -> dict[str, Any]:
                return brief

            def revise_creative_brief_for_quality(
                self,
                _topic: str,
                brief: dict[str, Any],
                **_kwargs: Any,
            ) -> dict[str, Any]:
                return brief

        class FakeImageClient:
            def __init__(self, incoming_settings: WebtoonSettings):
                assert incoming_settings is settings

            def generate_image(self, prompt: str, **kwargs: Any) -> tuple[bytes, str, str]:
                image_calls.append(
                    {
                        "prompt": prompt,
                        "reference_image_paths": list(kwargs.get("reference_image_paths") or []),
                    }
                )
                return source_png, "image/png", "notes"

        class FakeOcrClient:
            def __init__(self, incoming_settings: WebtoonSettings):
                assert incoming_settings is settings

            def check_character_composition(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
                return {"has_issues": False, "issues": [], "edit_instruction": ""}

            def check_character_reference_consistency(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
                return {"has_issues": False, "issues": [], "edit_instruction": ""}

            def check_background_text(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
                return {"has_errors": False, "background_texts": [], "corrections": []}

            def check_thumbnail_panel_distinction(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
                return {"has_issues": False, "issues": [], "edit_instruction": "", "duplicated_panel_numbers": []}

            def extract_text(self, *_args: Any, **_kwargs: Any) -> str:
                return ""

            def plan_text_corrections(self, intended_lines: list[str], _ocr_text: str, **_kwargs: Any) -> dict[str, Any]:
                return {
                    "rerender_required": False,
                    "corrected_text_lines": intended_lines,
                    "corrected_speaker_dialogues": [],
                    "issues": [],
                    "edit_instruction": "",
                    "confidence": 1.0,
                }

            def review_final_webtoon_package(self, **_kwargs: Any) -> dict[str, Any]:
                return {
                    "hard_blockers": [],
                    "soft_scores": {
                        "topic_alignment": 1.0,
                        "story_flow": 1.0,
                        "background_progression": 1.0,
                        "thumbnail_distinction": 1.0,
                        "bubble_placement": 1.0,
                        "ending_resolution": 1.0,
                        "scope_alignment": 1.0,
                        "caption_alignment": 1.0,
                    },
                    "notes": [],
                    "summary": "ok",
                }

        monkeypatch.setattr(pipeline_module, "GeminiTextClient", FakeTextClient)
        monkeypatch.setattr(pipeline_module, "GeminiImageClient", FakeImageClient)
        monkeypatch.setattr(pipeline_module, "GeminiOcrClient", FakeOcrClient)
        monkeypatch.setattr(
            pipeline_module,
            "render_thumbnail_card",
            lambda *_args, **_kwargs: {"image_bytes": render_png, "layout": [], "font_path": "fake-font.ttf"},
        )
        monkeypatch.setattr(
            pipeline_module,
            "execute_outbound_bundle",
            lambda _settings, bundle: {"status": bundle.status, "run_id": bundle.run_id},
        )

        result = pipeline_module.run_webtoon_pipeline(settings, topic="독일 입국", notes="test")

        assert result["status"] == "approved"
        # 8 calls: costume sheet + thumbnail + 6 panels
        assert len(image_calls) == 8
        # Costume sheet uses original character reference files
        assert image_calls[0]["reference_image_paths"] == [ref_a, ref_b]
        # Thumbnail and panels pass reference_image_paths for logging but use costume bytes
        for call in image_calls[1:]:
            assert call["reference_image_paths"] == [ref_a, ref_b]

    def test_single_pass_generates_all_images_without_regeneration(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """Design-first single-pass pipeline generates thumbnail + 6 panels exactly once."""

        def _png_bytes(color: tuple[int, int, int]) -> bytes:
            image = Image.new("RGB", (8, 8), color=color)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return buffer.getvalue()

        ref_dir = tmp_path / "characters"
        ref_dir.mkdir()
        ref_a = ref_dir / "kolla.png"
        ref_b = ref_dir / "zero.png"
        ref_a.write_bytes(_png_bytes((0, 0, 0)))
        ref_b.write_bytes(_png_bytes((128, 128, 128)))

        settings = _make_dummy_settings(character_assets_dir=ref_dir, max_correction_attempts=0, api_parallelism=3)
        source_png = _png_bytes((255, 0, 0))
        render_png = _png_bytes((0, 255, 0))
        image_calls: list[str] = []

        class FakeTextClient:
            def __init__(self, incoming_settings: WebtoonSettings):
                assert incoming_settings is settings

            def build_creative_brief(self, topic: str) -> dict[str, Any]:
                return {
                    "title": "독일 입국",
                    "thumbnail_subtitle": "입국장에서 시작",
                    "episode_scope": "journey",
                    "subtitle_scope": "journey",
                    "scope_summary": "공항 입국부터 출구까지 이동하는 여정",
                    "image_prompt": "base prompt",
                    "thumbnail_scene_prompt": "입국장 대표 장면",
                    "caption": "독일 입국 테스트",
                    "hashtags": ["#독일생활"],
                    "character_notes": "",
                    "outfit_plan": {
                        "kolla": {"top": "navy hoodie", "bottom": "dark jeans"},
                        "zero": {"top": "white t-shirt", "bottom": "khaki pants"},
                    },
                    "prop_registry": {"passport": "navy booklet with gold emblem"},
                    "thumbnail_direction": {
                        "shot": "wide establishing shot",
                        "scene": "airport arrival hall",
                        "background": "large arrival sign",
                        "composition": "Characters in lower 60%",
                    },
                    "panels": [
                        {
                            "panel_no": index,
                            "story_role": "전개",
                            "location": f"장소 {index}",
                            "scene_prompt": f"scene {index}",
                            "direction": {
                                "shot": "medium shot",
                                "scene": f"scene {index} action",
                                "background": f"장소 {index} 배경",
                                "composition": "centered",
                            },
                            "key_props": ["passport"] if index >= 4 else [],
                            "carryover_props": ["passport"] if index >= 4 else [],
                            "speaker_dialogues": [
                                {"speaker": "kolla", "dialogue_lines": [f"콜라 {index}"]},
                                {"speaker": "zero", "dialogue_lines": [f"제로 {index}"]},
                            ],
                        }
                        for index in range(1, 7)
                    ],
                    "model": "fake-llm",
                    "story_review": {"has_issues": False},
                }

        class FakeImageClient:
            def __init__(self, incoming_settings: WebtoonSettings):
                assert incoming_settings is settings

            def generate_image(self, prompt: str, **_kwargs: Any) -> tuple[bytes, str, str]:
                if "COSTUME REFERENCE SHEET" in prompt:
                    image_calls.append("costume-sheet")
                elif "THUMBNAIL" in prompt:
                    image_calls.append("thumbnail")
                else:
                    match = re.search(r"PANEL (\d+)/", prompt)
                    assert match is not None, f"Expected PANEL n/ in prompt, got: {prompt[:100]}"
                    image_calls.append(f"panel-{match.group(1)}")
                return source_png, "image/png", "notes"

        class FakeOcrClient:
            def __init__(self, incoming_settings: WebtoonSettings):
                assert incoming_settings is settings

        monkeypatch.setattr(pipeline_module, "GeminiTextClient", FakeTextClient)
        monkeypatch.setattr(pipeline_module, "GeminiImageClient", FakeImageClient)
        monkeypatch.setattr(pipeline_module, "GeminiOcrClient", FakeOcrClient)
        monkeypatch.setattr(
            pipeline_module,
            "render_thumbnail_card",
            lambda *_args, **_kwargs: {"image_bytes": render_png, "layout": [], "font_path": "fake-font.ttf"},
        )
        monkeypatch.setattr(
            pipeline_module,
            "execute_outbound_bundle",
            lambda _settings, bundle: {"status": bundle.status, "run_id": bundle.run_id},
        )

        result = pipeline_module.run_webtoon_pipeline(settings, topic="독일 입국", notes="test")

        assert result["status"] == "approved"
        # Exactly 8 image calls: 1 costume sheet + 1 thumbnail + 6 panels, no regeneration
        assert len(image_calls) == 8
        assert image_calls[0] == "costume-sheet"
        assert image_calls[1] == "thumbnail"
        assert sorted(image_calls[2:]) == ["panel-1", "panel-2", "panel-3", "panel-4", "panel-5", "panel-6"]

    def test_targeted_replan_feedback_extracts_thumbnail_and_arbitrary_panels(self):
        review = {
            "hard_blockers": [
                "썸네일 캐릭터 신체 구조 오류가 남아 있습니다.",
                "패널 2(표지판 읽기), 패널 5(도장 찍기)의 scene_prompt가 실제 이미지에 반영되지 않았습니다.",
            ],
            "soft_scores": {
                "story_flow": 0.95,
                "background_progression": 0.95,
                "ending_resolution": 0.95,
            },
            "summary": "blocked",
        }

        result = pipeline_module._collect_targeted_replan_feedback(review, panel_count=6)

        assert result["thumbnail_feedback"] == ["썸네일 캐릭터 신체 구조 오류가 남아 있습니다."]
        assert sorted(result["panel_feedback"]) == [2, 5]

    def test_targeted_replan_feedback_expands_global_panel_blockers_to_all_panels(self):
        review = {
            "hard_blockers": [
                "장면과 프롬프트의 심각한 불일치: 패널 전체에서 같은 배경이 반복되고 있습니다."
            ],
            "soft_scores": {
                "story_flow": 0.4,
                "background_progression": 0.3,
                "ending_resolution": 0.9,
            },
            "summary": "blocked",
        }

        result = pipeline_module._collect_targeted_replan_feedback(review, panel_count=6)

        assert result["thumbnail_feedback"] == []
        assert sorted(result["panel_feedback"]) == [1, 2, 3, 4, 5, 6]

    def test_targeted_replan_feedback_does_not_expand_eye_color_to_all_panels(self):
        # Eye colour is a model-limitation issue that cannot be fixed by regeneration.
        # It should NOT be expanded to all panels via the global feedback path.
        review = {
            "hard_blockers": [
                "Thumbnail: Incorrect Zero Eye Color: Zero's eyes are rendered as yellow/amber.",
                "Panel 1: Incorrect Zero Eye Color: Zero's eyes are rendered as yellow/amber, and this recurs throughout all panels.",
            ],
            "soft_scores": {
                "story_flow": 0.95,
                "background_progression": 0.95,
                "ending_resolution": 0.95,
            },
            "summary": "blocked",
        }

        result = pipeline_module._collect_targeted_replan_feedback(review, panel_count=6)

        # Eye-colour issues are non-fixable: filtered from both thumbnail and panel feedback.
        assert result["thumbnail_feedback"] == []
        assert result["panel_feedback"] == {}

    def test_targeted_replan_feedback_filters_background_text_per_panel(self):
        # Background text spelling errors are a model limitation — regeneration cannot fix them.
        review = {
            "hard_blockers": [
                "패널 1 배경 텍스트 품질 게이트 실패: 9ASSPOR9->PASSPORT",
                "패널 3 배경 텍스트 품질 게이트 실패: PASEPORT->PASSPORT",
                "패널 4 캐릭터 구성 품질 게이트 실패: 캐릭터 누락",
            ],
            "soft_scores": {"story_flow": 0.95, "background_progression": 0.95, "ending_resolution": 0.95},
            "summary": "blocked",
        }

        result = pipeline_module._collect_targeted_replan_feedback(review, panel_count=6)

        # Background text blockers filtered out; only panel 4 character issue remains
        assert sorted(result["panel_feedback"]) == [4]
        assert any("캐릭터 누락" in item for item in result["panel_feedback"][4])

    def test_targeted_replan_feedback_uses_soft_score_summary_when_no_panel_matches(self):
        review = {
            "hard_blockers": [],
            "soft_scores": {
                "story_flow": 0.55,
                "background_progression": 0.95,
                "ending_resolution": 0.95,
            },
            "summary": "최종 검수에서 이야기 흐름 또는 배경 진행 점수가 낮았습니다.",
        }

        result = pipeline_module._collect_targeted_replan_feedback(review, panel_count=6)

        assert result["thumbnail_feedback"] == []
        assert sorted(result["panel_feedback"]) == [1, 2, 3, 4, 5, 6]


def _make_dummy_settings(**overrides) -> WebtoonSettings:
    defaults = {
        "google_oauth_client_secret_file": Path("/tmp/fake-secret.json"),
        "google_oauth_token_file": Path("/tmp/fake-token.json"),
        "google_drive_root_folder_id": "fake-folder-id",
        "gemini_api_key": "fake-image-key",
        "approval_default_user": "tester",
        "character_assets_dir": Path("/tmp/nonexistent-chars"),
    }
    defaults.update(overrides)
    return WebtoonSettings(**defaults)
