from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import WEBTOON_REQUIRED_FIELDS, WebtoonSettings
from .clients import GoogleWorkspaceClient
from .pipeline import run_webtoon_pipeline
from .smoke_tests import run_smoke_tests

DEFAULT_SMOKE_SERVICES = ("llm", "ocr", "image", "drive", "sheets", "instagram")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="웹툰 에이전트 CLI")
    parser.add_argument("--env-file", type=Path, default=None, help="환경변수 파일 경로")

    subparsers = parser.add_subparsers(dest="command", required=True)

    smoke_parser = subparsers.add_parser("smoke", help="외부 서비스 스모크 테스트 실행")
    smoke_parser.add_argument(
        "--services",
        nargs="*",
        choices=["llm", "ocr", "image", "drive", "sheets", "instagram"],
        default=None,
        help="테스트할 서비스 목록",
    )

    google_auth_parser = subparsers.add_parser("google-auth", help="Google OAuth 2.0 사용자 인증 수행")
    google_auth_parser.add_argument(
        "--status-only",
        action="store_true",
        help="토큰 파일 경로와 현재 상태만 출력",
    )

    pipeline_parser = subparsers.add_parser("pipeline", help="이미지 생성부터 저장/게시까지 파이프라인 실행")
    pipeline_parser.add_argument("--topic", required=True, help="웹툰 주제")
    pipeline_parser.add_argument("--notes", default="", help="시트 notes 컬럼에 넣을 메모")
    pipeline_parser.add_argument("--publish", action="store_true", help="Instagram 게시까지 실제 실행")

    return parser


def _required_settings_fields(args: argparse.Namespace) -> set[str]:
    if args.command == "google-auth":
        if args.status_only:
            return set()
        return {"google_oauth_client_secret_file"}

    if args.command == "smoke":
        selected = set(args.services or DEFAULT_SMOKE_SERVICES)
        required: set[str] = set()
        if {"llm", "ocr", "image"} & selected:
            required.add("gemini_api_key")
        if "drive" in selected:
            required.add("google_drive_root_folder_id")
        if "sheets" in selected:
            required.add("google_sheets_spreadsheet_id")
        if "instagram" in selected:
            required.update(
                {
                    "instagram_access_token",
                    "instagram_business_account_id",
                }
            )
        return required

    if args.command == "pipeline":
        required = set(WEBTOON_REQUIRED_FIELDS)
        required.discard("google_oauth_client_secret_file")
        if not args.publish:
            required.discard("instagram_access_token")
            required.discard("instagram_business_account_id")
        return required

    return set()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        settings = WebtoonSettings.from_env(args.env_file, required_fields=_required_settings_fields(args))
    except ValueError as error:
        parser.exit(2, f"설정 오류: {error}\n")

    if args.command == "smoke":
        result = run_smoke_tests(settings, services=args.services)
    elif args.command == "google-auth":
        if args.status_only:
            result = {
                "client_secret_file": str(settings.google_oauth_client_secret_file),
                "token_file": str(settings.google_oauth_token_file),
                "token_exists": settings.google_oauth_token_file.exists(),
            }
        else:
            google_client = GoogleWorkspaceClient(settings, interactive_auth=True)
            result = google_client.auth_status()
    elif args.command == "pipeline":
        result = run_webtoon_pipeline(settings, topic=args.topic, publish=args.publish, notes=args.notes)
    else:
        parser.error(f"Unknown command: {args.command}")
        return

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
