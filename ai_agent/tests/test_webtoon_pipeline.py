from __future__ import annotations

import io
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from agents.webtoon import pipeline as pipeline_module
from agents.webtoon.pipeline import (
    _apply_corrected_dialogues,
    _build_publish_decision,
    _simplify_background_correction,
    OutboundArtifact,
    OutboundBundle,
    PublishRequest,
    build_bubble_only_ocr_image,
    build_panel_generation_prompt,
    build_run_identity,
    build_thumbnail_generation_prompt,
    build_versioned_path,
    create_artifact_paths,
    execute_outbound_bundle,
    list_character_reference_files,
)
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
        assert "콜라가 제로와 비슷한 크기이거나 더 작아 보이면 안 된다" in prompt
        assert "썸네일부터 패널 6까지 모든 컷에 공통으로 적용되는 절대 규칙" in prompt
        assert "같은 좁은 범위로 유지" in prompt
        assert "사람이 올라서면 안 되는 표면" in prompt
        assert "모자이크처럼 깨진 텍스트" in prompt
        assert "포괄적 대표 배경" in prompt
        assert "같은 메인 홀/복도/대기 공간 구조에 간판만 바꾼 버전도 금지" in prompt
        assert "특정 한 컷에만 적용되는 것이 아니라 패널 1부터 패널 6 전체에 적용" in prompt
        assert "구조적 지문" in prompt
        assert "전체 여정을 소개하는 별도 전환 공간" in prompt
        assert "scene-required in-world signage" in prompt
        assert "'INFO', 'Gate A12', 'On Time'" in prompt
        assert "'DOCUMENT', 'NAME', 'ID'" in prompt
        assert "짧은 인장 라벨 1개와 짧은 날짜 한 줄" in prompt
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
        assert "'INFO', 'Gate A12', 'On Time'" in prompt
        assert "'DOCUMENT', 'NAME', 'ID'" in prompt
        assert "짧은 인장 라벨 1개와 짧은 날짜 한 줄" in prompt
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


def _make_outbound_bundle(tmp_path: Path, *, publish_decision: str, include_publish_request: bool) -> OutboundBundle:
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
            "quality_report": {"quality_decision": publish_decision},
            "slides": [
                {"slide_index": 0, "slide_type": "thumbnail", "file": thumb_final.name, "publish_file": thumb_publish.name},
                {"slide_index": 1, "slide_type": "panel", "panel_no": 1, "file": panel_final.name, "publish_file": panel_publish.name},
            ],
        },
        metadata_filename="run_metadata.json",
        ocr_payload={"panels": [{"panel_no": 1, "ocr_text": "ok"}]},
        ocr_filename="ocr_result_v1.json",
        correction_payloads=(("correction_v1.json", {"panels": [{"panel_no": 1}]}),),
        sheet_name="weekly_planning",
        sheet_headers=("week_key", "status", "drive_folder_url"),
        sheet_row={"week_key": "2026-W12", "status": "approved", "drive_folder_url": ""},
        quality_report={"quality_decision": publish_decision, "hard_blockers": [], "manual_review_reasons": []},
        publish_decision=publish_decision,
        initial_status="publish_blocked" if publish_decision == "blocked" else "approved",
        approved_by="tester" if publish_decision == "allow" else "",
        approved_at="2026-03-18T10:00:00+00:00" if publish_decision == "allow" else "",
        approved_image_version=1 if publish_decision == "allow" else "",
        slides=(
            {"slide_index": 0, "slide_type": "thumbnail", "file": thumb_final.name, "publish_file": thumb_publish.name},
            {"slide_index": 1, "slide_type": "panel", "panel_no": 1, "file": panel_final.name, "publish_file": panel_publish.name},
        ),
        publish_request=(
            PublishRequest(
                caption="캡션\n\n#해시태그",
                image_bundle_names=(thumb_publish.name, panel_publish.name),
            )
            if include_publish_request
            else None
        ),
    )


class TestExecuteOutboundBundle:
    def test_uploads_drive_and_sheets_even_when_publish_is_blocked(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        settings = _make_dummy_settings()
        bundle = _make_outbound_bundle(tmp_path, publish_decision="blocked", include_publish_request=True)
        uploaded_jsons: list[tuple[str, dict[str, object]]] = []
        appended_rows: list[dict[str, object]] = []

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

            def append_row(self, sheet_name: str, headers: list[str], row: dict[str, object]):
                assert sheet_name == "weekly_planning"
                assert headers == list(bundle.sheet_headers)
                appended_rows.append(row)
                return {"updates": {"updatedRows": 1}}

        class FakeInstagramGraphClient:
            def __init__(self, incoming_settings: WebtoonSettings):
                raise AssertionError("Instagram client should not initialize for blocked publish decision")

        monkeypatch.setattr(pipeline_module, "GoogleWorkspaceClient", FakeGoogleWorkspaceClient)
        monkeypatch.setattr(pipeline_module, "InstagramGraphClient", FakeInstagramGraphClient)

        result = execute_outbound_bundle(settings, bundle)

        assert result["status"] == "publish_blocked"
        assert result["instagram"] is None
        assert appended_rows[0]["status"] == "publish_blocked"
        assert any(filename == "run_metadata.json" for filename, _payload in uploaded_jsons)
        assert any(filename == "ocr_result_v1.json" for filename, _payload in uploaded_jsons)

    def test_publishes_instagram_when_publish_is_allowed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        settings = _make_dummy_settings()
        bundle = _make_outbound_bundle(tmp_path, publish_decision="allow", include_publish_request=True)
        appended_rows: list[dict[str, object]] = []

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
                return {"id": filename}

            def append_row(self, sheet_name: str, headers: list[str], row: dict[str, object]):
                assert sheet_name == "weekly_planning"
                assert headers == list(bundle.sheet_headers)
                appended_rows.append(row)
                return {"updates": {"updatedRows": 1}}

        class FakeInstagramGraphClient:
            def __init__(self, incoming_settings: WebtoonSettings):
                assert incoming_settings is settings

            def publish_carousel(self, image_urls: list[str], caption: str):
                assert image_urls == [
                    "https://cdn.example/thumbnail_publish_v1.jpg",
                    "https://cdn.example/panel_01_publish_v1.jpg",
                ]
                assert caption == "캡션\n\n#해시태그"
                return {"media": {"id": "ig-media-1", "permalink": "https://instagram.example/p/1"}}

        monkeypatch.setattr(pipeline_module, "GoogleWorkspaceClient", FakeGoogleWorkspaceClient)
        monkeypatch.setattr(pipeline_module, "InstagramGraphClient", FakeInstagramGraphClient)
        monkeypatch.setattr(
            pipeline_module,
            "upload_image_for_instagram",
            lambda image_bytes, filename: f"https://cdn.example/{filename}",
        )

        result = execute_outbound_bundle(settings, bundle)

        assert result["status"] == "posted"
        assert result["instagram"]["media"]["id"] == "ig-media-1"
        assert appended_rows[0]["status"] == "posted"


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
    def test_panels_do_not_use_thumbnail_image_as_reference(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
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
            "render_text_boxes",
            lambda *_args, **_kwargs: {"image_bytes": render_png, "layout": []},
        )
        monkeypatch.setattr(
            pipeline_module,
            "review_bubble_layout",
            lambda _layout: {"has_issues": False, "issues": [], "panel_scores": [], "soft_score": 1.0},
        )
        monkeypatch.setattr(pipeline_module, "build_bubble_only_ocr_image", lambda *_args, **_kwargs: render_png)
        monkeypatch.setattr(
            pipeline_module,
            "execute_outbound_bundle",
            lambda _settings, bundle: {"status": bundle.initial_status, "run_id": bundle.run_id},
        )

        result = pipeline_module.run_webtoon_pipeline(settings, topic="독일 입국", publish=False, notes="test")

        assert result["status"] == "approved"
        assert len(image_calls) == 7
        assert image_calls[0]["reference_image_paths"] == [ref_a, ref_b]
        assert image_calls[1]["reference_image_paths"] == [ref_a, ref_b]
        assert image_calls[2]["reference_image_paths"][:2] == [ref_a, ref_b]
        assert image_calls[2]["reference_image_paths"][2].name == "panel_01_base_v1.png"
        for call in image_calls[3:]:
            assert call["reference_image_paths"] == [ref_a, ref_b]


def _make_dummy_settings(**overrides) -> WebtoonSettings:
    defaults = {
        "google_oauth_client_secret_file": Path("/tmp/fake-secret.json"),
        "google_oauth_token_file": Path("/tmp/fake-token.json"),
        "google_drive_root_folder_id": "fake-folder-id",
        "google_sheets_spreadsheet_id": "fake-sheet-id",
        "gemini_api_key": "fake-image-key",
        "instagram_access_token": "fake-token",
        "instagram_business_account_id": "fake-account-id",
        "approval_default_user": "tester",
        "character_assets_dir": Path("/tmp/nonexistent-chars"),
    }
    defaults.update(overrides)
    return WebtoonSettings(**defaults)
