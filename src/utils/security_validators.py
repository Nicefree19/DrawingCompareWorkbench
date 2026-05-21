# -*- coding: utf-8 -*-
"""
보안 검증 유틸리티 모듈
========================

Path Traversal 방어, 입력 검증, 파일 크기 제한 등 보안 관련 기능 제공.

OWASP Top 10 대응:
- A01: Path Traversal 방어
- A03: 입력 검증 (SQL Injection, XSS 방지)
- A04: 파일 크기 제한 (DoS 방지)

Author: TEKLA_MCP Team
Date: 2025-12-18
"""

import logging
import os
import re
import unicodedata
from pathlib import Path
from typing import List, Optional, Set, Union

logger = logging.getLogger(__name__)


# ============================================================================
# 보안 설정 상수
# ============================================================================

class SecurityConfig:
    """보안 설정 상수"""

    # 파일 크기 제한
    MAX_FILE_SIZE_MB: int = 50
    MAX_PDF_PAGES: int = 100
    MAX_IMAGE_DPI: int = 300

    # 문자열 길이 제한
    MAX_PROJECT_NAME_LENGTH: int = 255
    MAX_SECTION_NAME_LENGTH: int = 255
    MAX_COMMENT_LENGTH: int = 1000
    MAX_PATH_LENGTH: int = 260  # Windows MAX_PATH

    # 허용 파일 확장자
    ALLOWED_EXTENSIONS: Set[str] = {
        '.pdf', '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff',
        '.xlsx', '.xls', '.csv',
        '.mgt', '.dxf', '.dwg',
        '.txt', '.json', '.yaml', '.yml',
    }

    # 허용 디렉토리 패턴 (프로젝트 루트 기준)
    ALLOWED_DIRECTORY_PATTERNS: List[str] = [
        'data',
        'out',
        'output',
        'exports',
        'reports',
        'test_data',
        'temp',
    ]


# ============================================================================
# Path Traversal 방어
# ============================================================================

class PathValidationError(ValueError):
    """경로 검증 실패 예외"""
    pass


class FileSizeError(ValueError):
    """파일 크기 초과 예외"""
    pass


class InputValidationError(ValueError):
    """입력 검증 실패 예외"""
    pass


def validate_path(
    user_path: Union[str, Path],
    allowed_base_dir: Optional[Union[str, Path]] = None,
    must_exist: bool = False,
    allow_absolute: bool = True,
) -> Path:
    """사용자 제공 경로를 검증하여 Path Traversal 공격 방지

    Args:
        user_path: 사용자가 제공한 파일 경로
        allowed_base_dir: 허용된 기본 디렉토리 (None이면 검사 생략)
        must_exist: True면 파일 존재 여부 확인
        allow_absolute: 절대 경로 허용 여부

    Returns:
        검증된 Path 객체

    Raises:
        PathValidationError: 검증 실패 시

    Examples:
        >>> validate_path("data/test.pdf", allowed_base_dir=Path.cwd())
        PosixPath('/project/data/test.pdf')

        >>> validate_path("../../etc/passwd", allowed_base_dir=Path.cwd())
        PathValidationError: Path outside allowed directory
    """
    if not user_path:
        raise PathValidationError("경로가 비어있습니다")

    # 문자열 -> Path 변환
    path = Path(user_path)

    # 경로 길이 검사
    if len(str(path)) > SecurityConfig.MAX_PATH_LENGTH:
        raise PathValidationError(
            f"경로가 너무 깁니다: {len(str(path))} > {SecurityConfig.MAX_PATH_LENGTH}"
        )

    # 절대 경로 검사
    if not allow_absolute and path.is_absolute():
        raise PathValidationError("절대 경로는 허용되지 않습니다")

    # 경로 정규화 (.. 해석)
    try:
        resolved_path = path.resolve()
    except (OSError, RuntimeError) as e:
        raise PathValidationError(f"경로 해석 실패: {e}")

    # 허용 디렉토리 검사 (Path Traversal 방어 핵심)
    if allowed_base_dir is not None:
        allowed_dir = Path(allowed_base_dir).resolve()

        try:
            # Python 3.9+ is_relative_to() 사용
            if not resolved_path.is_relative_to(allowed_dir):
                logger.warning(
                    f"Path Traversal 시도 감지: {user_path} -> {resolved_path} "
                    f"(allowed: {allowed_dir})"
                )
                raise PathValidationError(
                    f"허용된 디렉토리 외부 경로입니다: {resolved_path}"
                )
        except AttributeError:
            # Python 3.8 이하 호환
            try:
                resolved_path.relative_to(allowed_dir)
            except ValueError:
                logger.warning(
                    f"Path Traversal 시도 감지: {user_path} -> {resolved_path}"
                )
                raise PathValidationError(
                    f"허용된 디렉토리 외부 경로입니다: {resolved_path}"
                )

    # 존재 여부 검사
    if must_exist and not resolved_path.exists():
        raise PathValidationError(f"파일이 존재하지 않습니다: {resolved_path}")

    return resolved_path


def validate_file_extension(
    path: Union[str, Path],
    allowed_extensions: Optional[Set[str]] = None,
) -> Path:
    """파일 확장자 검증

    Args:
        path: 파일 경로
        allowed_extensions: 허용 확장자 세트 (None이면 기본값 사용)

    Returns:
        검증된 Path 객체

    Raises:
        PathValidationError: 허용되지 않은 확장자
    """
    path = Path(path)
    allowed = allowed_extensions or SecurityConfig.ALLOWED_EXTENSIONS

    ext = path.suffix.lower()
    if ext not in allowed:
        raise PathValidationError(
            f"허용되지 않은 파일 형식입니다: {ext} "
            f"(허용: {', '.join(sorted(allowed))})"
        )

    return path


def validate_output_path(
    user_path: Union[str, Path],
    allowed_base_dir: Optional[Union[str, Path]] = None,
) -> Path:
    """출력 파일 경로 검증 (쓰기 작업용)

    Args:
        user_path: 사용자가 제공한 출력 경로
        allowed_base_dir: 허용된 기본 디렉토리

    Returns:
        검증된 Path 객체
    """
    path = validate_path(
        user_path,
        allowed_base_dir=allowed_base_dir,
        must_exist=False,
        allow_absolute=True,
    )

    # 부모 디렉토리 존재 확인 (없으면 생성 가능하도록)
    parent = path.parent
    if not parent.exists():
        logger.debug(f"출력 디렉토리 생성 필요: {parent}")

    return path


# ============================================================================
# 파일 크기 제한
# ============================================================================

def validate_file_size(
    path: Union[str, Path],
    max_size_mb: Optional[float] = None,
) -> Path:
    """파일 크기 검증

    Args:
        path: 파일 경로
        max_size_mb: 최대 파일 크기 (MB), None이면 기본값 사용

    Returns:
        검증된 Path 객체

    Raises:
        FileSizeError: 크기 초과
        PathValidationError: 파일 미존재
    """
    path = Path(path)
    max_mb = max_size_mb or SecurityConfig.MAX_FILE_SIZE_MB

    if not path.exists():
        raise PathValidationError(f"파일이 존재하지 않습니다: {path}")

    size_bytes = path.stat().st_size
    size_mb = size_bytes / (1024 * 1024)

    if size_mb > max_mb:
        logger.warning(f"파일 크기 초과: {path} ({size_mb:.2f}MB > {max_mb}MB)")
        raise FileSizeError(
            f"파일 크기가 제한을 초과했습니다: {size_mb:.1f}MB > {max_mb}MB"
        )

    return path


def validate_pdf_pages(
    pdf_path: Union[str, Path],
    max_pages: Optional[int] = None,
) -> int:
    """PDF 페이지 수 검증

    Args:
        pdf_path: PDF 파일 경로
        max_pages: 최대 페이지 수 (None이면 기본값 사용)

    Returns:
        PDF 페이지 수

    Raises:
        FileSizeError: 페이지 수 초과
        ImportError: PyMuPDF 미설치
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError("PDF 검증을 위해 PyMuPDF를 설치하세요: pip install PyMuPDF")

    max_p = max_pages or SecurityConfig.MAX_PDF_PAGES

    doc = fitz.open(str(pdf_path))
    page_count = len(doc)
    doc.close()

    if page_count > max_p:
        logger.warning(f"PDF 페이지 초과: {pdf_path} ({page_count} > {max_p})")
        raise FileSizeError(
            f"PDF 페이지 수가 제한을 초과했습니다: {page_count} > {max_p}"
        )

    return page_count


def validate_dpi(dpi: int, max_dpi: Optional[int] = None) -> int:
    """DPI 값 검증 (OCR 메모리 공격 방지)

    Args:
        dpi: 요청된 DPI 값
        max_dpi: 최대 허용 DPI

    Returns:
        검증된 DPI 값
    """
    max_d = max_dpi or SecurityConfig.MAX_IMAGE_DPI

    if dpi < 72:
        logger.warning(f"DPI가 너무 낮습니다: {dpi} -> 72")
        return 72

    if dpi > max_d:
        logger.warning(f"DPI 제한 적용: {dpi} -> {max_d}")
        return max_d

    return dpi


# ============================================================================
# 입력 문자열 검증
# ============================================================================

def sanitize_string(
    value: str,
    max_length: int,
    field_name: str = "입력",
    allow_newlines: bool = False,
) -> str:
    """문자열 입력 검증 및 정제

    Args:
        value: 입력 문자열
        max_length: 최대 길이
        field_name: 필드 이름 (에러 메시지용)
        allow_newlines: 개행 문자 허용 여부

    Returns:
        정제된 문자열

    Raises:
        InputValidationError: 검증 실패
    """
    if value is None:
        return ""

    if not isinstance(value, str):
        value = str(value)

    # Unicode 정규화 (homograph 공격 방지)
    value = unicodedata.normalize('NFKC', value)

    # 제어 문자 제거
    value = ''.join(
        char for char in value
        if unicodedata.category(char) != 'Cc' or (allow_newlines and char in '\n\r')
    )

    # 길이 검사
    if len(value) > max_length:
        raise InputValidationError(
            f"{field_name}이(가) 너무 깁니다: {len(value)} > {max_length}"
        )

    return value.strip()


def validate_project_name(name: str) -> str:
    """프로젝트 이름 검증 및 정제

    Args:
        name: 프로젝트 이름

    Returns:
        정제된 프로젝트 이름

    Raises:
        InputValidationError: 검증 실패
    """
    if not name:
        raise InputValidationError("프로젝트 이름이 비어있습니다")

    # 기본 정제
    name = sanitize_string(
        name,
        max_length=SecurityConfig.MAX_PROJECT_NAME_LENGTH,
        field_name="프로젝트 이름",
    )

    # 특수문자 제한 (파일 시스템 안전)
    # 허용: 한글, 영문, 숫자, 공백, 하이픈, 언더스코어, 점
    sanitized = re.sub(r'[^\w\s\-_.]', '', name, flags=re.UNICODE)

    if sanitized != name:
        logger.warning(f"프로젝트 이름 정제됨: '{name}' -> '{sanitized}'")

    if not sanitized:
        raise InputValidationError("유효한 문자가 없는 프로젝트 이름입니다")

    return sanitized


def validate_section_name(name: str) -> str:
    """섹션(부재) 이름 검증"""
    return sanitize_string(
        name,
        max_length=SecurityConfig.MAX_SECTION_NAME_LENGTH,
        field_name="섹션 이름",
    )


def validate_comment(comment: str) -> str:
    """주석/메모 검증"""
    return sanitize_string(
        comment,
        max_length=SecurityConfig.MAX_COMMENT_LENGTH,
        field_name="주석",
        allow_newlines=True,
    )


def validate_numeric(
    value: Union[int, float, str],
    field_name: str = "숫자",
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
) -> float:
    """숫자 입력 검증

    Args:
        value: 검증할 값
        field_name: 필드 이름
        min_value: 최소값
        max_value: 최대값

    Returns:
        검증된 float 값
    """
    try:
        num = float(value)
    except (ValueError, TypeError):
        raise InputValidationError(f"{field_name}이(가) 유효한 숫자가 아닙니다: {value}")

    # NaN, Inf 검사
    if not (num == num):  # NaN check
        raise InputValidationError(f"{field_name}이(가) NaN입니다")
    if num == float('inf') or num == float('-inf'):
        raise InputValidationError(f"{field_name}이(가) 무한대입니다")

    if min_value is not None and num < min_value:
        raise InputValidationError(f"{field_name}이(가) 최소값보다 작습니다: {num} < {min_value}")

    if max_value is not None and num > max_value:
        raise InputValidationError(f"{field_name}이(가) 최대값보다 큽니다: {num} > {max_value}")

    return num


def validate_element_id(element_id: Union[int, str]) -> int:
    """Element ID 검증

    Args:
        element_id: 엘리먼트 ID

    Returns:
        검증된 정수 ID
    """
    try:
        eid = int(element_id)
    except (ValueError, TypeError):
        raise InputValidationError(f"유효하지 않은 Element ID: {element_id}")

    if eid < 0:
        raise InputValidationError(f"Element ID는 음수일 수 없습니다: {eid}")

    return eid


# ============================================================================
# 편의 함수
# ============================================================================

def safe_file_open(
    file_path: Union[str, Path],
    allowed_base_dir: Optional[Union[str, Path]] = None,
    max_size_mb: Optional[float] = None,
    allowed_extensions: Optional[Set[str]] = None,
) -> Path:
    """파일 열기 전 종합 보안 검증

    Args:
        file_path: 파일 경로
        allowed_base_dir: 허용 디렉토리
        max_size_mb: 최대 파일 크기
        allowed_extensions: 허용 확장자

    Returns:
        검증된 Path 객체
    """
    # 1. 경로 검증 (Path Traversal 방어)
    path = validate_path(
        file_path,
        allowed_base_dir=allowed_base_dir,
        must_exist=True,
    )

    # 2. 확장자 검증
    path = validate_file_extension(path, allowed_extensions)

    # 3. 파일 크기 검증
    path = validate_file_size(path, max_size_mb)

    return path


def get_project_root() -> Path:
    """프로젝트 루트 디렉토리 반환"""
    # 이 파일 기준으로 루트 찾기: src/utils/security_validators.py
    return Path(__file__).resolve().parent.parent.parent


def get_allowed_data_dir() -> Path:
    """허용된 데이터 디렉토리 반환"""
    return get_project_root() / "data"


def get_allowed_output_dir() -> Path:
    """허용된 출력 디렉토리 반환"""
    out_dir = get_project_root() / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


# ============================================================================
# 확장 입력 검증 (2025-12-30 추가)
# ============================================================================

# 정규식 패턴
class ValidationPatterns:
    """검증용 정규식 패턴"""

    # 사용자 관련
    USERNAME = re.compile(r"^[a-zA-Z0-9_\-\.가-힣]{1,50}$")
    USER_ID = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")
    EMAIL = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

    # 세션/프로젝트 관련
    SESSION_ID = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")
    SESSION_NAME = re.compile(r"^[a-zA-Z0-9_\-\s가-힣]{1,100}$")
    PROJECT_ID = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")

    # 기술적 ID
    ELEMENT_ID = re.compile(r"^[0-9]{1,10}$")
    CHANGE_ID = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")
    CONFLICT_ID = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")

    # 안전한 문자열 (HTML/SQL 인젝션 방지)
    SAFE_STRING = re.compile(r"^[a-zA-Z0-9_\-\.\s가-힣,;:()[\]]+$")

    # URL 패턴 (검증용)
    URL = re.compile(
        r"^https?://"
        r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|"
        r"localhost|"
        r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"
        r"(?::\d+)?"
        r"(?:/?|[/?]\S+)$",
        re.IGNORECASE
    )


class JSONSchemaError(ValueError):
    """JSON 스키마 검증 실패 예외"""
    pass


class WebSocketMessageError(ValueError):
    """WebSocket 메시지 검증 실패 예외"""
    pass


def validate_pattern(
    value: str,
    pattern: re.Pattern,
    field_name: str,
    allow_empty: bool = False,
) -> str:
    """정규식 패턴 기반 문자열 검증

    Args:
        value: 검증할 문자열
        pattern: 정규식 패턴
        field_name: 필드 이름 (에러 메시지용)
        allow_empty: 빈 문자열 허용 여부

    Returns:
        검증된 문자열

    Raises:
        InputValidationError: 검증 실패
    """
    if value is None:
        if allow_empty:
            return ""
        raise InputValidationError(f"{field_name}이(가) 비어있습니다")

    value = str(value).strip()

    if not value:
        if allow_empty:
            return ""
        raise InputValidationError(f"{field_name}이(가) 비어있습니다")

    if not pattern.match(value):
        raise InputValidationError(
            f"{field_name}에 허용되지 않는 문자가 포함되어 있습니다: {value[:50]}"
        )

    return value


def validate_username(username: str) -> str:
    """사용자 이름 검증"""
    return validate_pattern(username, ValidationPatterns.USERNAME, "사용자 이름")


def validate_user_id(user_id: str) -> str:
    """사용자 ID 검증"""
    return validate_pattern(user_id, ValidationPatterns.USER_ID, "사용자 ID")


def validate_email(email: str) -> str:
    """이메일 주소 검증"""
    return validate_pattern(email, ValidationPatterns.EMAIL, "이메일")


def validate_session_id(session_id: str) -> str:
    """세션 ID 검증"""
    return validate_pattern(session_id, ValidationPatterns.SESSION_ID, "세션 ID")


def validate_session_name(session_name: str) -> str:
    """세션 이름 검증"""
    return validate_pattern(session_name, ValidationPatterns.SESSION_NAME, "세션 이름")


def validate_project_id(project_id: str) -> str:
    """프로젝트 ID 검증"""
    return validate_pattern(project_id, ValidationPatterns.PROJECT_ID, "프로젝트 ID")


def sanitize_html(value: str, max_length: int = 1000) -> str:
    """HTML 특수 문자 이스케이프 (XSS 방지)

    Args:
        value: 입력 문자열
        max_length: 최대 길이

    Returns:
        이스케이프된 문자열
    """
    if not value:
        return ""

    # HTML 특수 문자 이스케이프
    sanitized = (
        value
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
        .replace("/", "&#x2F;")
    )

    return sanitized[:max_length]


def validate_json_object(
    data: dict,
    required_fields: Optional[List[str]] = None,
    allowed_fields: Optional[List[str]] = None,
    field_validators: Optional[dict] = None,
) -> dict:
    """JSON 객체 검증

    Args:
        data: 검증할 JSON 객체
        required_fields: 필수 필드 목록
        allowed_fields: 허용 필드 목록 (None이면 모든 필드 허용)
        field_validators: 필드별 검증 함수 딕셔너리 {field_name: validator_func}

    Returns:
        검증된 딕셔너리

    Raises:
        JSONSchemaError: 검증 실패
    """
    if data is None:
        raise JSONSchemaError("JSON 데이터가 비어있습니다")

    if not isinstance(data, dict):
        raise JSONSchemaError(f"JSON 객체가 아닙니다: {type(data).__name__}")

    # 필수 필드 검사
    if required_fields:
        missing = [f for f in required_fields if f not in data]
        if missing:
            raise JSONSchemaError(f"필수 필드 누락: {', '.join(missing)}")

    # 허용 필드 검사
    if allowed_fields is not None:
        unknown = [f for f in data.keys() if f not in allowed_fields]
        if unknown:
            logger.warning(f"알 수 없는 필드 무시됨: {', '.join(unknown)}")
            # 알 수 없는 필드 제거
            data = {k: v for k, v in data.items() if k in allowed_fields}

    # 필드별 검증
    if field_validators:
        validated_data = {}
        for field, value in data.items():
            if field in field_validators:
                try:
                    validated_data[field] = field_validators[field](value)
                except (ValueError, TypeError, InputValidationError) as e:
                    raise JSONSchemaError(f"필드 '{field}' 검증 실패: {e}")
            else:
                validated_data[field] = value
        return validated_data

    return data


def validate_websocket_message(
    message: dict,
    allowed_types: Optional[List[str]] = None,
    max_payload_size: int = 65536,  # 64KB
) -> dict:
    """WebSocket 메시지 검증

    Args:
        message: WebSocket 메시지 객체
        allowed_types: 허용된 메시지 타입 목록
        max_payload_size: 최대 페이로드 크기 (bytes)

    Returns:
        검증된 메시지

    Raises:
        WebSocketMessageError: 검증 실패
    """
    if not isinstance(message, dict):
        raise WebSocketMessageError("메시지가 유효한 JSON 객체가 아닙니다")

    # 메시지 타입 필수
    msg_type = message.get("type")
    if not msg_type:
        raise WebSocketMessageError("메시지 타입이 누락되었습니다")

    # 허용된 타입 검사
    if allowed_types and msg_type not in allowed_types:
        raise WebSocketMessageError(f"허용되지 않은 메시지 타입: {msg_type}")

    # 페이로드 크기 검사
    import json
    try:
        payload_size = len(json.dumps(message, ensure_ascii=False).encode('utf-8'))
        if payload_size > max_payload_size:
            raise WebSocketMessageError(
                f"메시지 크기 초과: {payload_size} > {max_payload_size} bytes"
            )
    except (TypeError, ValueError) as e:
        raise WebSocketMessageError(f"메시지 직렬화 실패: {e}")

    return message


# 허용된 WebSocket 메시지 타입
ALLOWED_WS_MESSAGE_TYPES = [
    "ping", "pong",
    "join_session", "leave_session",
    "join_session_response", "leave_session_response",
    "make_change", "change_response", "change_made",
    "update_cursor", "update_selection",
    "chat_message",
    "user_joined", "user_left",
    "conflict_detected", "conflict_resolved",
    "error", "welcome",
]


def validate_chat_message(message: str, max_length: int = 2000) -> str:
    """채팅 메시지 검증

    Args:
        message: 채팅 메시지
        max_length: 최대 길이

    Returns:
        검증 및 정제된 메시지
    """
    if not message:
        raise InputValidationError("채팅 메시지가 비어있습니다")

    # 기본 정제
    message = sanitize_string(
        message,
        max_length=max_length,
        field_name="채팅 메시지",
        allow_newlines=True,
    )

    # HTML 이스케이프 (XSS 방지)
    message = sanitize_html(message, max_length)

    return message


def validate_cursor_position(position: dict) -> dict:
    """커서 위치 검증

    Args:
        position: 커서 위치 객체 {x, y, z} 또는 {line, column}

    Returns:
        검증된 위치 객체
    """
    if not isinstance(position, dict):
        raise InputValidationError("커서 위치가 유효한 객체가 아닙니다")

    validated = {}

    # 3D 좌표 또는 텍스트 좌표 검증
    for key in ["x", "y", "z", "line", "column"]:
        if key in position:
            try:
                val = float(position[key])
                # 합리적인 범위 검사
                if key in ["x", "y", "z"]:
                    val = validate_numeric(
                        val, f"좌표 {key}",
                        min_value=-1e9, max_value=1e9
                    )
                else:  # line, column
                    val = validate_numeric(
                        val, f"위치 {key}",
                        min_value=0, max_value=1e6
                    )
                validated[key] = val
            except (ValueError, TypeError):
                raise InputValidationError(f"커서 위치 '{key}'이(가) 유효한 숫자가 아닙니다")

    return validated


def validate_selected_objects(objects: list, max_count: int = 1000) -> list:
    """선택된 객체 목록 검증

    Args:
        objects: 객체 ID 목록
        max_count: 최대 선택 개수

    Returns:
        검증된 객체 ID 목록
    """
    if not isinstance(objects, list):
        raise InputValidationError("선택 목록이 배열이 아닙니다")

    if len(objects) > max_count:
        raise InputValidationError(f"선택 개수 초과: {len(objects)} > {max_count}")

    validated = []
    for obj in objects:
        if isinstance(obj, (int, str)):
            # 객체 ID를 문자열로 정규화
            obj_id = str(obj).strip()
            if obj_id and len(obj_id) <= 64:
                validated.append(obj_id)
        elif isinstance(obj, dict) and "id" in obj:
            # 객체 딕셔너리인 경우
            obj_id = str(obj["id"]).strip()
            if obj_id and len(obj_id) <= 64:
                validated.append(obj_id)

    return validated


def validate_change_metadata(metadata: dict) -> dict:
    """변경 메타데이터 검증

    Args:
        metadata: 메타데이터 객체

    Returns:
        검증된 메타데이터
    """
    if metadata is None:
        return {}

    if not isinstance(metadata, dict):
        raise InputValidationError("메타데이터가 유효한 객체가 아닙니다")

    # 메타데이터 크기 제한 (16KB)
    import json
    try:
        size = len(json.dumps(metadata, ensure_ascii=False).encode('utf-8'))
        if size > 16384:
            raise InputValidationError(f"메타데이터 크기 초과: {size} > 16384 bytes")
    except (TypeError, ValueError):
        raise InputValidationError("메타데이터 직렬화 실패")

    # 깊이 제한 검사 (재귀 방지)
    def check_depth(obj, current_depth=0, max_depth=5):
        if current_depth > max_depth:
            raise InputValidationError(f"메타데이터 깊이 초과: {current_depth} > {max_depth}")
        if isinstance(obj, dict):
            for v in obj.values():
                check_depth(v, current_depth + 1, max_depth)
        elif isinstance(obj, list):
            for item in obj:
                check_depth(item, current_depth + 1, max_depth)

    check_depth(metadata)

    return metadata


def validate_role(role: str) -> str:
    """사용자 역할 검증

    Args:
        role: 역할 문자열

    Returns:
        검증된 역할
    """
    allowed_roles = ["owner", "admin", "editor", "viewer", "guest"]

    role = str(role).lower().strip()
    if role not in allowed_roles:
        raise InputValidationError(
            f"허용되지 않은 역할: {role} (허용: {', '.join(allowed_roles)})"
        )

    return role


def validate_change_type(change_type: str) -> str:
    """변경 타입 검증

    Args:
        change_type: 변경 타입 문자열

    Returns:
        검증된 변경 타입
    """
    allowed_types = ["create", "update", "delete", "move", "resize", "rotate"]

    change_type = str(change_type).lower().strip()
    if change_type not in allowed_types:
        raise InputValidationError(
            f"허용되지 않은 변경 타입: {change_type}"
        )

    return change_type


# ============================================================================
# 통합 검증 함수
# ============================================================================

def validate_api_request(
    data: dict,
    endpoint: str,
) -> dict:
    """API 요청 통합 검증

    Args:
        data: 요청 데이터
        endpoint: API 엔드포인트

    Returns:
        검증된 데이터

    Raises:
        JSONSchemaError: 검증 실패
    """
    if not isinstance(data, dict):
        raise JSONSchemaError("요청 데이터가 유효한 JSON 객체가 아닙니다")

    # 엔드포인트별 검증 스키마
    schemas = {
        "/api/sessions": {
            "allowed_fields": ["name", "user_id", "username", "project_id"],
            "field_validators": {
                "name": lambda v: validate_session_name(v) if v else f"Session_{int(__import__('time').time())}",
                "user_id": lambda v: validate_user_id(v) if v else "anonymous",
                "username": lambda v: validate_username(v) if v else None,
                "project_id": lambda v: validate_project_id(v) if v else "default",
            }
        },
        "/api/users": {
            "allowed_fields": ["user_id", "username", "email", "role"],
            "required_fields": ["user_id", "username"],
            "field_validators": {
                "user_id": validate_user_id,
                "username": validate_username,
                "email": lambda v: validate_email(v) if v else None,
                "role": lambda v: validate_role(v) if v else "viewer",
            }
        },
    }

    schema = schemas.get(endpoint, {})

    return validate_json_object(
        data,
        required_fields=schema.get("required_fields"),
        allowed_fields=schema.get("allowed_fields"),
        field_validators=schema.get("field_validators"),
    )
