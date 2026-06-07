"""DWG → DXF 변환기 (ODA File Converter 연동)

Sprint 9 Phase 1.1: DwgConverter
ODA File Converter를 사용하여 DWG 파일을 DXF로 변환합니다.

설치 요구사항:
    - ODA File Converter (무료)
    - https://www.opendesign.com/guestfiles/oda_file_converter

보안 강화 (2025-12-25):
    - 안전한 임시 디렉토리 관리 (TemporaryDirectory 컨텍스트 매니저)
    - 경로 검증 및 symlink 해결
    - 정리 실패 로깅
"""

import logging
import os
import shutil
import subprocess
import tempfile
import secrets
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class ODAConverterNotFoundError(Exception):
    """ODA File Converter가 설치되지 않았을 때 발생하는 예외"""

    pass


class DWGConversionError(Exception):
    """DWG 변환 실패 시 발생하는 예외"""

    pass


class DwgConverter:
    """DWG → DXF 변환기 (ODA File Converter 래퍼)

    ODA File Converter를 사용하여 DWG 파일을 DXF 형식으로 변환합니다.

    사용 예시:
        converter = DwgConverter()
        dxf_path = converter.convert("input.dwg")

    Attributes:
        oda_path: ODA File Converter 실행 파일 경로
    """

    # Windows 기본 설치 경로
    DEFAULT_PATHS = [
        r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe",
        r"C:\Program Files (x86)\ODA\ODAFileConverter\ODAFileConverter.exe",
        r"C:\Program Files\ODA\ODAFileConverter 26.10.0\ODAFileConverter.exe",
        r"C:\Program Files\ODA\ODAFileConverter 25.12\ODAFileConverter.exe",
        r"C:\Program Files (x86)\ODA\ODAFileConverter 26.10.0\ODAFileConverter.exe",
        r"C:\Program Files (x86)\ODA\ODAFileConverter 25.12\ODAFileConverter.exe",
    ]

    # 지원하는 DXF 출력 버전
    SUPPORTED_VERSIONS = [
        "ACAD9",
        "ACAD10",
        "ACAD12",
        "ACAD13",
        "ACAD14",
        "ACAD2000",
        "ACAD2004",
        "ACAD2007",
        "ACAD2010",
        "ACAD2013",
        "ACAD2018",
    ]

    def __init__(self, oda_path: Optional[str] = None):
        """DwgConverter 초기화

        Args:
            oda_path: ODA Converter 실행 파일 경로 (None이면 자동 탐색)

        Raises:
            ODAConverterNotFoundError: ODA Converter를 찾을 수 없는 경우
        """
        self.oda_path = oda_path or self._find_converter()

        if not self.oda_path:
            raise ODAConverterNotFoundError(
                "ODA File Converter가 설치되지 않았습니다.\n"
                "다운로드: https://www.opendesign.com/guestfiles/oda_file_converter\n"
                "설치 후 ODA_CONVERTER_PATH 환경변수를 설정하거나, "
                "기본 경로에 설치해주세요."
            )

        logger.info(f"ODA Converter 경로: {self.oda_path}")

    def _find_converter(self) -> Optional[str]:
        """ODA File Converter 경로 자동 탐색

        탐색 순서:
            1. ODA_CONVERTER_PATH 환경변수
            2. 기본 설치 경로 목록
            3. 시스템 PATH

        Returns:
            실행 파일 경로 또는 None
        """
        # 1. 환경변수 확인
        env_path = os.environ.get("ODA_CONVERTER_PATH")
        if env_path and Path(env_path).exists():
            logger.debug(f"환경변수에서 ODA 경로 발견: {env_path}")
            return env_path

        # 2. 기본 설치 경로 확인
        for path in self.DEFAULT_PATHS:
            if Path(path).exists():
                logger.debug(f"기본 경로에서 ODA 발견: {path}")
                return path

        # 3. 동적 버전 폴더 탐색 (glob 패턴)
        import glob

        for base in [r"C:\Program Files\ODA", r"C:\Program Files (x86)\ODA"]:
            pattern = os.path.join(base, "ODAFileConverter*", "ODAFileConverter.exe")
            matches = glob.glob(pattern)
            if matches:
                logger.debug(f"동적 탐색에서 ODA 발견: {matches[0]}")
                return matches[0]

        # 4. 시스템 PATH에서 검색
        result = shutil.which("ODAFileConverter")
        if result:
            logger.debug(f"PATH에서 ODA 발견: {result}")
            return result

        logger.warning("ODA File Converter를 찾을 수 없습니다")
        return None

    def _validate_path(self, file_path: Path) -> Path:
        """경로 검증 및 정규화 (보안 강화)

        Args:
            file_path: 검증할 파일 경로

        Returns:
            정규화된 절대 경로

        Raises:
            ValueError: 잘못된 경로
        """
        try:
            # symlink 해결 및 절대 경로로 변환
            resolved = Path(file_path).resolve()

            # [보안] 경로가 실제 파일인지 확인 (장치 파일 등 방지)
            if resolved.exists() and not resolved.is_file():
                raise ValueError(f"일반 파일이 아닙니다: {file_path}")

            return resolved
        except (OSError, RuntimeError) as e:
            raise ValueError(f"잘못된 파일 경로: {e}")

    def _create_secure_temp_dir(self, prefix: str) -> Tuple[str, str]:
        """보안이 강화된 임시 디렉토리 생성

        Args:
            prefix: 디렉토리 이름 접두사

        Returns:
            (임시 디렉토리 경로, 고유 토큰)
        """
        # 예측 불가능한 접미사 추가
        unique_token = secrets.token_hex(8)
        temp_dir = tempfile.mkdtemp(prefix=f"{prefix}{unique_token}_")
        return temp_dir, unique_token

    def _safe_cleanup(self, path: str, description: str) -> bool:
        """안전한 임시 디렉토리 정리 (로깅 포함)

        Args:
            path: 정리할 디렉토리 경로
            description: 로그용 설명

        Returns:
            정리 성공 여부
        """
        try:
            if Path(path).exists():
                shutil.rmtree(path)
                logger.debug(f"임시 디렉토리 정리 완료: {description}")
                return True
            return True
        except Exception as e:
            # [보안] 정리 실패를 로그에 기록 (silent failure 방지)
            logger.warning(f"임시 디렉토리 정리 실패 ({description}): {e}")
            return False

    def convert(
        self,
        dwg_path: Path,
        output_version: str = "ACAD2018",
        timeout: int = 120,
    ) -> Path:
        """DWG 파일을 DXF로 변환

        Args:
            dwg_path: 입력 DWG 파일 경로
            output_version: 출력 DXF 버전 (기본: ACAD2018)
            timeout: 변환 타임아웃 (초)

        Returns:
            변환된 DXF 파일 경로 (임시 디렉토리)

        Raises:
            FileNotFoundError: 입력 파일이 없는 경우
            ValueError: 지원하지 않는 출력 버전 또는 잘못된 경로
            DWGConversionError: 변환 실패
            TimeoutError: 타임아웃 초과

        보안 강화 (2025-12-25):
            - symlink 해결 및 경로 검증
            - 예측 불가능한 임시 디렉토리 이름
            - 정리 실패 로깅
        """
        # [보안] 경로 검증 및 정규화
        dwg_path = self._validate_path(Path(dwg_path))

        # 입력 파일 검증
        if not dwg_path.exists():
            raise FileNotFoundError(f"DWG 파일을 찾을 수 없습니다: {dwg_path}")

        if dwg_path.suffix.lower() not in (".dwg", ".dxf"):
            raise ValueError(f"지원하지 않는 파일 형식: {dwg_path.suffix}")

        # 출력 버전 검증 (화이트리스트)
        if output_version not in self.SUPPORTED_VERSIONS:
            raise ValueError(
                f"지원하지 않는 출력 버전: {output_version}\n"
                f"지원 버전: {', '.join(self.SUPPORTED_VERSIONS)}"
            )

        # 이미 DXF인 경우 그대로 반환
        if dwg_path.suffix.lower() == ".dxf":
            logger.info("입력 파일이 이미 DXF 형식입니다")
            return dwg_path

        # [보안] 예측 불가능한 임시 디렉토리 생성
        temp_input, _ = self._create_secure_temp_dir("dwg_in_")
        temp_output, _ = self._create_secure_temp_dir("dwg_out_")

        try:
            # 입력 파일 복사 (ODA는 폴더 단위로 작동)
            input_copy = Path(temp_input) / dwg_path.name
            shutil.copy(dwg_path, input_copy)

            logger.info(f"DWG 변환 시작: {dwg_path.name} → DXF ({output_version})")

            # ODA Converter 실행
            # 명령어: ODAFileConverter "입력폴더" "출력폴더" "버전" "형식" "재귀" "감사"
            cmd = [
                self.oda_path,
                temp_input,
                temp_output,
                output_version,
                "DXF",  # 출력 형식
                "0",  # 재귀 탐색 안 함
                "1",  # 파일 감사 및 복구 시도
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            # 결과 확인
            if result.returncode != 0:
                # [보안] 에러 메시지 길이 제한 (정보 노출 방지)
                error_msg = (result.stderr or result.stdout or "알 수 없는 오류")[:200]
                raise DWGConversionError(f"DWG 변환 실패: {error_msg}")

            # 출력 파일 찾기
            output_file = Path(temp_output) / dwg_path.with_suffix(".dxf").name

            if not output_file.exists():
                # 대소문자 차이 고려
                for f in Path(temp_output).iterdir():
                    if f.suffix.lower() == ".dxf":
                        output_file = f
                        break

            if not output_file.exists():
                raise DWGConversionError("변환된 DXF 파일을 찾을 수 없습니다")

            logger.info(f"DWG 변환 완료: {output_file}")
            return output_file

        except subprocess.TimeoutExpired:
            raise TimeoutError(
                f"DWG 변환 시간 초과 ({timeout}초)\n" f"파일이 너무 크거나 손상되었을 수 있습니다."
            )

        finally:
            # [보안] 입력 임시 폴더 정리 (로깅 포함)
            self._safe_cleanup(temp_input, "입력 임시 폴더")

    def is_available(self) -> bool:
        """ODA Converter 사용 가능 여부 확인

        Returns:
            True: 사용 가능
            False: 사용 불가
        """
        return self.oda_path is not None and Path(self.oda_path).exists()

    @classmethod
    def check_installation(cls) -> dict:
        """ODA Converter 설치 상태 확인

        Returns:
            {
                "installed": bool,
                "path": Optional[str],
                "message": str
            }
        """
        try:
            converter = cls()
            return {
                "installed": True,
                "path": converter.oda_path,
                "message": f"ODA Converter 설치됨: {converter.oda_path}",
            }
        except ODAConverterNotFoundError as e:
            return {"installed": False, "path": None, "message": str(e)}


def convert_with_configured_converter(
    source: str | Path,
    *,
    output_version: str = "ACAD2018",
    timeout_seconds: int = 180,
) -> Tuple[Optional[Path], str]:
    """Quarantined conversion shim for policy-safe callers.

    Returns ``(converted_path, note)`` where ``note`` is ``converted`` on
    success, ``oda_unavailable`` when no local converter is installed, or
    ``oda_failed:<ExceptionType>`` for non-fatal conversion failures.
    """

    try:
        converter = DwgConverter()
    except ODAConverterNotFoundError:
        return None, "oda_unavailable"

    try:
        converted = converter.convert(
            source,
            output_version=output_version,
            timeout=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - explicit fallback stays non-fatal
        return None, f"oda_failed:{type(exc).__name__}"

    return Path(converted), "converted"
