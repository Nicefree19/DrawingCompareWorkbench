# -*- coding: utf-8 -*-
"""
Global Exception Handler
========================

GUI 전역 예외 처리 및 로깅 시스템.
크래시 방지 및 사용자 친화적 오류 메시지 제공.

Author: TEKLA_MCP Team
Date: 2025-12-14
Sprint: 2.3
"""

import faulthandler
import logging
import os
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Callable

# 로그 설정
LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("GlobalExceptionHandler")

# §12.4 B1 — module-level handle keeps the fault log file alive for the
# entire process lifetime. ``faulthandler`` writes raw stack traces directly
# to this descriptor on a native crash (Qt6Core 0xc0000409, SIGSEGV, etc.),
# so the file MUST stay open until the process dies. Letting the file object
# go out of scope would close the descriptor and silently lose the dump.
_FAULT_LOG_HANDLE: Optional["TextIOBase"] = None  # type: ignore[name-defined]
_FAULT_LOG_PATH: Optional[Path] = None


def setup_logging(log_level: int = logging.INFO) -> None:
    """로깅 시스템 초기화"""
    log_file = LOG_DIR / f"error_{datetime.now().strftime('%Y%m%d')}.log"

    # 파일 핸들러
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.ERROR)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )

    # 콘솔 핸들러
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    # 루트 로거 설정
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    logger.info(f"Logging initialized. Log file: {log_file}")


class ExceptionHandler:
    """중앙 집중형 예외 처리기"""

    _instance: Optional["ExceptionHandler"] = None
    _error_callback: Optional[Callable[[str, str], None]] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def install(cls, error_callback: Optional[Callable[[str, str], None]] = None) -> None:
        """예외 핸들러 설치

        Args:
            error_callback: 오류 발생 시 호출할 콜백 (title, message)
                           GUI 다이얼로그 표시 등에 사용
        """
        handler = cls()
        handler._error_callback = error_callback
        sys.excepthook = handler._handle_exception
        logger.info("Global exception handler installed")

    def _handle_exception(
        self,
        exc_type: type,
        exc_value: BaseException,
        exc_tb,
    ) -> None:
        """예외 처리"""
        # KeyboardInterrupt는 무시 (정상 종료)
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return

        # 로그 기록
        error_message = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logger.error(f"Unhandled exception:\n{error_message}")

        # 사용자 친화적 메시지 생성
        user_message = self._create_user_message(exc_type, exc_value)

        # 콜백 호출 (GUI 다이얼로그 등)
        if self._error_callback:
            try:
                self._error_callback("예상치 못한 오류", user_message)
            except Exception as callback_error:
                logger.error(f"Error callback failed: {callback_error}")
        else:
            # 콜백이 없으면 콘솔에 출력
            print(f"\n[ERROR] {user_message}\n")

    def _create_user_message(self, exc_type: type, exc_value: BaseException) -> str:
        """사용자 친화적 오류 메시지 생성

        보안 강화 (2025-12-25):
        - 시스템 경로, 내부 정보 노출 방지
        - 사용자에게는 일반화된 메시지만 표시
        - 상세 정보는 로그 파일에만 기록
        """
        error_type = exc_type.__name__

        # [보안] 사용자에게 표시할 안전한 에러 메시지 매핑
        # 실제 에러 상세는 로그에만 기록하고, 사용자에게는 일반화된 가이드만 제공
        safe_messages = {
            "MemoryError": "파일이 너무 큽니다. 더 작은 파일로 시도하거나, 청크 단위 처리를 활성화하세요.",
            "FileNotFoundError": "파일을 찾을 수 없습니다. 경로를 확인해주세요.",
            "PermissionError": "파일에 접근할 권한이 없습니다. 파일이 다른 프로그램에서 열려 있는지 확인하세요.",
            "ValueError": "잘못된 값이 입력되었습니다. 입력 데이터를 확인해주세요.",
            "KeyError": "필요한 데이터가 누락되었습니다.",
            "ConnectionError": "연결에 실패했습니다. 네트워크 상태를 확인해주세요.",
            "TimeoutError": "작업 시간이 초과되었습니다. 다시 시도해주세요.",
            "ImportError": "필요한 모듈을 로드할 수 없습니다. 설치 상태를 확인해주세요.",
            "OSError": "시스템 오류가 발생했습니다. 디스크 공간 및 권한을 확인해주세요.",
        }

        # 안전한 메시지가 있으면 사용, 없으면 일반 메시지
        if error_type in safe_messages:
            msg = f"오류 발생: {safe_messages[error_type]}"
        else:
            # [보안] 알 수 없는 에러 타입의 경우 상세 정보 노출 방지
            msg = "예상치 못한 오류가 발생했습니다."

        # 로그 확인 안내 (상세 정보는 로그에서만 확인 가능)
        msg += "\n\n상세 로그는 logs 폴더를 확인해주세요."

        return msg


def show_error_dialog_qt(title: str, message: str) -> None:
    """PySide6 오류 다이얼로그 표시"""
    try:
        from PySide6.QtWidgets import QMessageBox, QApplication

        # QApplication이 없으면 생성
        app = QApplication.instance()
        if app is None:
            app = QApplication([])

        msg_box = QMessageBox()
        msg_box.setIcon(QMessageBox.Icon.Critical)
        msg_box.setWindowTitle(title)
        msg_box.setText(message)
        msg_box.exec()
    except ImportError:
        # PySide6가 없으면 콘솔 출력
        print(f"\n[{title}]\n{message}\n")


def enable_windows_fault_handler(
    log_dir: Optional[Path] = None,
    *,
    cleanup_older_than_days: int = 7,
) -> Path:
    """Arm ``faulthandler`` so native crashes (Qt6Core 0xc0000409, SIGSEGV,
    stack overflow) capture all-thread stacks to ``logs/fault_*.log``.

    Audit-gates §12.4 B1 — without this, native crashes bypass
    ``sys.excepthook`` entirely and the user only sees Windows Error
    Reporting (BEX64) without any actionable Python stack. With this
    armed, ``faulthandler`` will dump every Python thread's stack to
    the configured file at the moment of the crash, surviving even
    Microsoft's ``__fastfail`` invocation.

    Args:
        log_dir: directory to host the fault log. Defaults to the module-
            level ``LOG_DIR`` (``<repo>/logs``).
        cleanup_older_than_days: silently delete fault logs older than this
            cutoff so the directory does not grow unbounded over months of
            normal operation. Set to 0 to disable cleanup.

    Returns:
        Path to the fault log file that was just opened. Callers can log
        this path so users know where to look after a crash.

    Idempotency: a second call closes the previous log and rearms the
    handler with a fresh timestamped file. Safe to call multiple times.
    """
    global _FAULT_LOG_HANDLE, _FAULT_LOG_PATH

    target_dir = Path(log_dir) if log_dir is not None else LOG_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    # Close any previously opened fault log handle so faulthandler swaps
    # cleanly to the new file rather than writing to a stale descriptor.
    if _FAULT_LOG_HANDLE is not None:
        try:
            faulthandler.disable()
        except Exception:
            pass
        try:
            _FAULT_LOG_HANDLE.close()
        except Exception:
            pass
        _FAULT_LOG_HANDLE = None

    # Plan §19 A-3 (Agent T finding T3) — operator opt-out. Crash logs
    # contain raw stack traces with full filesystem paths, which can
    # leak customer project names and cache locations to anyone with
    # read access to the log directory. The env var lets a security-
    # conscious deployment disable the handler entirely while keeping
    # the Python excepthook in place.
    if os.environ.get("DRAWING_COMPARE_DISABLE_FAULT_HANDLER", "").lower() in {
        "1", "true", "yes", "on",
    }:
        _FAULT_LOG_HANDLE = None
        _FAULT_LOG_PATH = None
        return target_dir

    fault_path = target_dir / f"fault_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    handle = open(fault_path, "w", encoding="utf-8", buffering=1)  # line-buffered
    # Plan §19 A-3 (Agent T finding T3) — restrict log file permissions
    # so co-resident users on the same host cannot read leaked paths.
    # os.chmod 0o600 is honoured on POSIX; on Windows it has limited
    # effect (cannot replicate full ACL), so deployments on shared
    # Windows hosts SHOULD use ``DRAWING_COMPARE_DISABLE_FAULT_HANDLER=1``
    # or write the log to a user-private directory.
    try:
        os.chmod(fault_path, 0o600)
    except Exception:
        pass
    # Header line so the user can match the dump to the run that produced it.
    try:
        import platform
        handle.write(
            f"[FAULT HANDLER ARMED] {datetime.now().isoformat()}\n"
            f"Python {sys.version.splitlines()[0]}\n"
            f"Platform {platform.platform()}\n"
        )
        try:
            import PySide6
            handle.write(f"PySide6 {getattr(PySide6, '__version__', 'unknown')}\n")
        except Exception:
            handle.write("PySide6 unavailable\n")
        # Plan §19 A-3 (Agent T T3) — explicit warning so operators
        # reading the file understand the content sensitivity.
        handle.write(
            "If a native crash occurs, every Python thread's stack will be "
            "written below this header.\n"
            "[WARNING] Stack traces contain absolute filesystem paths that "
            "may leak customer project names. Restrict log directory "
            "access (Unix chmod 0o600 applied above; Windows operators "
            "should set NTFS ACLs or DRAWING_COMPARE_DISABLE_FAULT_HANDLER=1).\n\n"
        )
        handle.flush()
    except Exception:
        pass

    faulthandler.enable(file=handle, all_threads=True)
    _FAULT_LOG_HANDLE = handle
    _FAULT_LOG_PATH = fault_path

    if cleanup_older_than_days > 0:
        try:
            cutoff = datetime.now() - timedelta(days=int(cleanup_older_than_days))
            for stale in target_dir.glob("fault_*.log"):
                try:
                    if datetime.fromtimestamp(stale.stat().st_mtime) < cutoff:
                        stale.unlink()
                except OSError:
                    continue
        except Exception:
            pass

    logger.info(
        "Windows fault handler armed -> %s (cleanup older than %d days)",
        fault_path, cleanup_older_than_days,
    )
    return fault_path


def install_exception_handler(
    use_qt_dialog: bool = True,
    *,
    enable_fault_handler: bool = True,
) -> None:
    """예외 핸들러 간편 설치 함수

    Args:
        use_qt_dialog: True면 Qt 다이얼로그 사용, False면 콘솔 출력.
            Headless test 환경에서는 ``QT_QPA_PLATFORM=offscreen`` 일 때
            자동으로 False 강제 — 다이얼로그 띄우려다 hang 되는 것 방지.
        enable_fault_handler: True면 동시에 native crash 캡처용
            ``faulthandler`` 도 활성화. Audit-gates §12 의 기본값.
    """
    setup_logging()

    # Headless guard — pytest 등에서 dialog 띄우면 무한 hang.
    if use_qt_dialog and os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
        use_qt_dialog = False

    callback = show_error_dialog_qt if use_qt_dialog else None
    ExceptionHandler.install(error_callback=callback)

    if enable_fault_handler:
        try:
            enable_windows_fault_handler()
        except Exception as exc:  # noqa: BLE001 — handler arming is best-effort
            logger.warning("enable_windows_fault_handler failed: %s", exc)


# 모듈 로드 시 자동 설정 (선택적)
# install_exception_handler(use_qt_dialog=False)
