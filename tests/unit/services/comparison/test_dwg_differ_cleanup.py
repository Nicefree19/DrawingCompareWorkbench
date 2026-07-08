"""DwgDiffer 리소스 정리 및 예외 처리 테스트

Sprint 9 Phase 1.4: DwgDiffer Cleanup Tests
컨텍스트 매니저와 finally 블록을 통한 임시 파일 정리를 검증합니다.

Phase 3 P3-3: ComparisonConfig 통합 테스트
DwgDiffer에 ComparisonConfig 적용 및 expand_blocks 전달을 검증합니다.

Phase R1 (RV-20260509-006) test isolation fix
=============================================
이 파일은 이전에 module import 시점에 ``sys.modules["PySide6"] = MagicMock()``
형태의 globals patch 를 설치하고 있었다. 그 결과 본 파일이 collection 단계에서
가장 먼저 import 되면 ``PySide6`` 가 mock 으로 치환된 상태가 되고, 같은
pytest 프로세스에서 이후에 collected/imported 되는
``tests/unit/services/comparison/test_workbench_phase_*.py`` 가 module-level
``from PySide6.QtWidgets import QApplication`` 을 평가할 때 mock된 PySide6
에서 ``QApplication`` 을 가져갔다 (autouse module-scoped teardown 은 이미
바인딩이 끝난 뒤라 too late).

증상은 일괄 실행 시 ``assert <MagicMock name='mock.QWidget.overlay_opacity_scale'...>``
같은 형태의 11/19 fail (단독 실행은 19/19 통과). 본 파일이 정작 mock 을
필요로 하는 영역은 클래스 내부의 fixture (``_mock_compare_worker``) 가
이미 backup/restore 패턴으로 격리하고 있어 module-level mocking 자체가
불필요했다 — 환경에 PySide6 6.10.1 이 실제 설치되어 있고 ``dwg_differ``
import path 가 PySide6 dependency 가 없음을 확인 후 제거.
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest


from src.services.comparison.base import ComparisonResult
from src.services.comparison.comparison_config import (
    ComparisonConfig,
    SensitivityConfig,
    LayerPriorityConfig,
)
from src.services.comparison.dwg_converter import ODAConverterNotFoundError
from src.services.comparison.dwg_differ import DwgDiffer


def _same_existing_path(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return left.resolve() == right.resolve()


class TestDwgDifferCleanup:
    """DwgDiffer 리소스 정리 테스트"""

    @pytest.fixture
    def temp_dir(self):
        """임시 디렉토리 생성"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def mock_dwg_file(self, temp_dir):
        """Mock DWG 파일 생성"""
        dwg_file = temp_dir / "test.dwg"
        dwg_file.touch()
        return dwg_file

    @pytest.fixture
    def mock_dxf_file(self, temp_dir):
        """Mock DXF 파일 생성"""
        dxf_file = temp_dir / "test.dxf"
        dxf_file.touch()
        return dxf_file

    def test_context_manager_enter(self):
        """컨텍스트 매니저 진입 테스트"""
        differ = DwgDiffer()

        with differ as d:
            assert d is differ
            assert isinstance(d, DwgDiffer)

    def test_context_manager_exit_cleanup(self, temp_dir):
        """컨텍스트 매니저 종료 시 정리 테스트"""
        differ = DwgDiffer()

        # 임시 디렉토리 추가
        temp_subdir = temp_dir / "temp_conversion"
        temp_subdir.mkdir()
        (temp_subdir / "test.dxf").touch()
        differ._temp_dirs.append(str(temp_subdir))

        # 컨텍스트 매니저 사용
        with differ:
            assert temp_subdir.exists()

        # 종료 후 정리 확인
        assert not temp_subdir.exists()
        assert len(differ._temp_dirs) == 0

    def test_context_manager_exit_on_exception(self, temp_dir):
        """예외 발생 시에도 정리되는지 테스트"""
        differ = DwgDiffer()

        # 임시 디렉토리 추가
        temp_subdir = temp_dir / "temp_conversion"
        temp_subdir.mkdir()
        differ._temp_dirs.append(str(temp_subdir))

        # 예외 발생 시에도 정리되어야 함
        with pytest.raises(ValueError):
            with differ:
                assert temp_subdir.exists()
                raise ValueError("Test exception")

        # 예외 발생 후에도 정리 확인
        assert not temp_subdir.exists()
        assert len(differ._temp_dirs) == 0

    def test_context_manager_reraises_exception(self):
        """컨텍스트 매니저가 예외를 재발생시키는지 테스트"""
        differ = DwgDiffer()

        with pytest.raises(ValueError, match="Test exception"):
            with differ:
                raise ValueError("Test exception")

    def test_dxf_conversion_cache_reuses_existing_output(self, temp_dir, mock_dwg_file):
        cache_dir = temp_dir / "dxf_cache"
        converted_dir = temp_dir / "converted"
        converted_dir.mkdir()
        converted = converted_dir / "test.dxf"
        converted.write_text("0\nEOF\n", encoding="utf-8")

        converter = Mock()
        converter.convert.return_value = converted

        differ = DwgDiffer(dxf_cache_dir=cache_dir)
        differ._converter = converter

        first = differ._ensure_dxf(mock_dwg_file)
        second = differ._ensure_dxf(mock_dwg_file)

        assert first == second
        assert _same_existing_path(first.parent, cache_dir)
        assert first.exists()
        assert converter.convert.call_count == 1

    def test_dxf_cache_can_reuse_same_stem_when_exact_key_changes(self, temp_dir):
        cache_dir = temp_dir / "dxf_cache"
        cache_dir.mkdir()
        source_dir = temp_dir / "copied_source"
        source_dir.mkdir()
        source = source_dir / "large_detail.dwg"
        source.write_bytes(b"dwg")
        cached = cache_dir / "large_detail.previoushash.dxf"
        cached.write_text("0\nEOF\n", encoding="utf-8")

        differ = DwgDiffer(
            config={"use_canonical_pipeline": False},
            dxf_cache_dir=cache_dir,
        )

        resolved = differ._ensure_dxf(source)

        assert _same_existing_path(resolved, cached)
        assert differ._dxf_cache_resolution_notes
        assert "using compatible same-stem cache" in differ._dxf_cache_resolution_notes[0]

    def test_legacy_dwg_fallback_error_uses_opt_in_wording(self, mock_dwg_file, temp_dir):
        for differ in (
            DwgDiffer(config={"use_canonical_pipeline": False}),
            DwgDiffer(config={"use_canonical_pipeline": False}, dxf_cache_dir=temp_dir / "cache"),
        ):
            with pytest.raises(ODAConverterNotFoundError) as exc_info:
                differ._ensure_dxf(mock_dwg_file)
            message = str(exc_info.value)
            assert "Legacy DWG-to-DXF fallback is disabled or not configured" in message
            assert "requires ODA" not in message
            assert "requires ODA File Converter" not in message

    @patch("src.services.comparison.dwg_differ.ezdxf")
    @patch("src.services.comparison.dwg_differ.DwgConverter")
    def test_compare_cleanup_on_success(
        self, mock_converter_class, mock_ezdxf, mock_dxf_file, temp_dir
    ):
        """compare() 성공 시 정리 테스트"""
        # Mock 설정
        mock_converter = Mock()
        temp_converted = temp_dir / "converted.dxf"
        temp_converted.touch()
        mock_converter.convert.return_value = temp_converted
        mock_converter_class.return_value = mock_converter

        mock_doc = Mock()
        mock_ezdxf.readfile.return_value = mock_doc

        differ = DwgDiffer()

        # 임시 파일 추적
        with patch.object(differ, "_cleanup_temp") as mock_cleanup:
            try:
                differ.compare(mock_dxf_file, mock_dxf_file)
            except Exception:
                pass  # 예외 무시

            # 정리 함수가 호출되었는지 확인
            mock_cleanup.assert_called_once()

    @patch("src.services.comparison.dwg_differ.ezdxf")
    def test_compare_cleanup_on_exception(self, mock_ezdxf, mock_dxf_file):
        """compare() 예외 발생 시 정리 테스트"""
        # ezdxf.readfile이 예외를 발생시키도록 설정
        mock_ezdxf.readfile.side_effect = Exception("DXF read error")

        differ = DwgDiffer(config={"use_canonical_pipeline": False})

        with patch.object(differ, "_cleanup_temp") as mock_cleanup:
            with pytest.raises(Exception, match="DXF read error"):
                differ.compare(mock_dxf_file, mock_dxf_file)

            # 예외 발생 후에도 정리 함수가 호출되었는지 확인
            mock_cleanup.assert_called_once()

    @patch("src.services.comparison.dwg_differ.ezdxf")
    @patch("src.services.comparison.dxf_cloud_marker.DxfCloudMarker")
    def test_compare_and_mark_cleanup_on_success(
        self, mock_marker_class, mock_ezdxf, mock_dxf_file, temp_dir
    ):
        """compare_and_mark() 성공 시 정리 테스트"""
        # Mock 설정
        mock_doc = Mock()
        mock_ezdxf.readfile.return_value = mock_doc

        mock_marker = Mock()
        output_path = temp_dir / "marked.dxf"
        mock_marker.create_marked_dxf.return_value = output_path
        mock_marker_class.return_value = mock_marker

        differ = DwgDiffer()

        with patch.object(differ, "_cleanup_temp") as mock_cleanup:
            try:
                differ.compare_and_mark(
                    mock_dxf_file, mock_dxf_file, temp_dir / "output.dxf"
                )
            except Exception:
                pass

            # 정리 함수가 호출되었는지 확인
            mock_cleanup.assert_called_once()

    @patch("src.services.comparison.dwg_differ.ezdxf")
    def test_compare_and_mark_cleanup_on_exception(self, mock_ezdxf, mock_dxf_file, temp_dir):
        """compare_and_mark() 예외 발생 시 정리 테스트"""
        # ezdxf.readfile이 예외를 발생시키도록 설정
        mock_ezdxf.readfile.side_effect = Exception("DXF read error")

        differ = DwgDiffer()

        with patch.object(differ, "_cleanup_temp") as mock_cleanup:
            with pytest.raises(Exception, match="DXF read error"):
                differ.compare_and_mark(
                    mock_dxf_file, mock_dxf_file, temp_dir / "output.dxf"
                )

            # 예외 발생 후에도 정리 함수가 호출되었는지 확인
            mock_cleanup.assert_called_once()

    @patch("src.services.comparison.dwg_differ.ezdxf")
    @patch("src.services.comparison.dwg_excel_reporter.DwgExcelReporter")
    def test_export_excel_cleanup_on_success(
        self, mock_reporter_class, mock_ezdxf, mock_dxf_file, temp_dir
    ):
        """export_excel() 성공 시 정리 테스트"""
        # Mock 설정
        mock_doc = Mock()
        mock_ezdxf.readfile.return_value = mock_doc

        mock_reporter = Mock()
        output_path = temp_dir / "report.xlsx"
        mock_reporter.generate.return_value = output_path
        mock_reporter_class.return_value = mock_reporter

        differ = DwgDiffer()

        with patch.object(differ, "_cleanup_temp") as mock_cleanup:
            try:
                differ.export_excel(mock_dxf_file, mock_dxf_file, output_path)
            except Exception:
                pass

            # 정리 함수가 호출되었는지 확인
            mock_cleanup.assert_called_once()

    @patch("src.services.comparison.dwg_differ.ezdxf")
    def test_export_excel_cleanup_on_exception(self, mock_ezdxf, mock_dxf_file, temp_dir):
        """export_excel() 예외 발생 시 정리 테스트"""
        # ezdxf.readfile이 예외를 발생시키도록 설정
        mock_ezdxf.readfile.side_effect = Exception("DXF read error")

        differ = DwgDiffer()

        with patch.object(differ, "_cleanup_temp") as mock_cleanup:
            with pytest.raises(Exception, match="DXF read error"):
                differ.export_excel(
                    mock_dxf_file, mock_dxf_file, temp_dir / "report.xlsx"
                )

            # 예외 발생 후에도 정리 함수가 호출되었는지 확인
            mock_cleanup.assert_called_once()

    def test_cleanup_temp_removes_directories(self, temp_dir):
        """_cleanup_temp()가 임시 디렉토리를 제거하는지 테스트"""
        differ = DwgDiffer()

        # 여러 임시 디렉토리 생성
        temp_dirs = []
        for i in range(3):
            temp_subdir = temp_dir / f"temp_{i}"
            temp_subdir.mkdir()
            (temp_subdir / f"file_{i}.dxf").touch()
            temp_dirs.append(str(temp_subdir))
            differ._temp_dirs.append(str(temp_subdir))

        # 모든 디렉토리가 존재하는지 확인
        for temp_subdir_str in temp_dirs:
            assert Path(temp_subdir_str).exists()

        # 정리 실행
        differ._cleanup_temp()

        # 모든 디렉토리가 제거되었는지 확인
        for temp_subdir_str in temp_dirs:
            assert not Path(temp_subdir_str).exists()

        # 추적 목록이 비워졌는지 확인
        assert len(differ._temp_dirs) == 0

    def test_cleanup_temp_handles_missing_directories(self, temp_dir):
        """_cleanup_temp()가 존재하지 않는 디렉토리를 안전하게 처리하는지 테스트"""
        differ = DwgDiffer()

        # 존재하지 않는 디렉토리 추가
        non_existent = str(temp_dir / "non_existent")
        differ._temp_dirs.append(non_existent)

        # 예외 없이 정리되어야 함
        differ._cleanup_temp()
        assert len(differ._temp_dirs) == 0

    def test_cleanup_temp_handles_permission_errors(self, temp_dir):
        """_cleanup_temp()가 권한 오류를 안전하게 처리하는지 테스트"""
        differ = DwgDiffer()

        temp_subdir = temp_dir / "temp_perm"
        temp_subdir.mkdir()
        differ._temp_dirs.append(str(temp_subdir))

        # shutil.rmtree를 Mock하여 권한 오류 시뮬레이션
        with patch("shutil.rmtree", side_effect=PermissionError("Access denied")):
            # 예외 없이 정리되어야 함
            differ._cleanup_temp()

        # 추적 목록은 비워져야 함 (ignore_errors=True)
        assert len(differ._temp_dirs) == 0

    def test_backward_compatibility_without_context_manager(self, mock_dxf_file):
        """기존 호출 방식과의 호환성 테스트 (컨텍스트 매니저 없이)"""
        differ = DwgDiffer()

        # 컨텍스트 매니저 없이도 사용 가능해야 함
        assert differ is not None

        # 기존 방식으로 메서드 호출 가능
        with patch.object(differ, "_cleanup_temp"):
            try:
                differ.compare(mock_dxf_file, mock_dxf_file)
            except Exception:
                pass  # Mock 때문에 실패할 수 있음

    def test_multiple_operations_cleanup(self, mock_dxf_file, temp_dir):
        """여러 작업 후 정리가 누적되지 않는지 테스트"""
        differ = DwgDiffer()

        # 첫 번째 작업
        temp_dir1 = temp_dir / "temp_1"
        temp_dir1.mkdir()
        differ._temp_dirs.append(str(temp_dir1))
        differ._cleanup_temp()

        assert len(differ._temp_dirs) == 0

        # 두 번째 작업
        temp_dir2 = temp_dir / "temp_2"
        temp_dir2.mkdir()
        differ._temp_dirs.append(str(temp_dir2))
        differ._cleanup_temp()

        assert len(differ._temp_dirs) == 0

    def test_cleanup_temp_is_idempotent(self):
        """_cleanup_temp()를 여러 번 호출해도 안전한지 테스트"""
        differ = DwgDiffer()

        # 여러 번 호출해도 예외가 발생하지 않아야 함
        differ._cleanup_temp()
        differ._cleanup_temp()
        differ._cleanup_temp()

        assert len(differ._temp_dirs) == 0

    @patch("src.services.comparison.dwg_differ.ezdxf")
    def test_compare_layouts_cleanup_on_success(self, mock_ezdxf, mock_dxf_file):
        """compare_layouts() 성공 시 정리 테스트"""
        # Mock 설정
        mock_doc = Mock()
        mock_ezdxf.readfile.return_value = mock_doc

        differ = DwgDiffer()

        with patch.object(differ, "_cleanup_temp") as mock_cleanup:
            try:
                differ.compare_layouts(mock_dxf_file, mock_dxf_file)
            except Exception:
                pass  # Mock 때문에 실패할 수 있음

            # 정리 함수가 호출되었는지 확인
            mock_cleanup.assert_called_once()

    @patch("src.services.comparison.dwg_differ.ezdxf")
    def test_compare_layouts_cleanup_on_exception(self, mock_ezdxf, mock_dxf_file):
        """compare_layouts() 예외 발생 시 정리 테스트"""
        # ezdxf.readfile이 예외를 발생시키도록 설정
        mock_ezdxf.readfile.side_effect = Exception("DXF read error")

        differ = DwgDiffer()

        with patch.object(differ, "_cleanup_temp") as mock_cleanup:
            with pytest.raises(Exception, match="DXF read error"):
                differ.compare_layouts(mock_dxf_file, mock_dxf_file)

            # 예외 발생 후에도 정리 함수가 호출되었는지 확인
            mock_cleanup.assert_called_once()


class TestDwgDifferComparisonConfigIntegration:
    """Phase 3 P3-3: DwgDiffer와 ComparisonConfig 통합 테스트"""

    def test_init_with_comparison_config(self):
        """ComparisonConfig를 사용하여 DwgDiffer 생성 테스트"""
        config = ComparisonConfig(
            sensitivity=SensitivityConfig(position_threshold=2.5),
            expand_blocks=True,
            use_spatial_index=True,
        )
        differ = DwgDiffer(comparison_config=config)

        assert differ._comparison_config is config
        assert differ._comparison_config.expand_blocks is True
        assert differ._comparison_config.sensitivity.position_threshold == 2.5

    def test_init_without_comparison_config(self):
        """ComparisonConfig 없이 DwgDiffer 생성 시 기본 설정 사용.

        Phase Q3 (RV-20260509-002): expand_blocks default flipped
        False → True (block geometry 변경 검출).
        """
        differ = DwgDiffer()

        assert differ._comparison_config is not None
        # Phase Q3 — default flipped
        assert differ._comparison_config.expand_blocks is True
        assert differ._comparison_config.sensitivity.position_threshold == 1.0

    def test_from_config_factory_method(self):
        """from_config 팩토리 메서드 테스트"""
        config = ComparisonConfig(
            sensitivity=SensitivityConfig(near_match_radius=15.0),
            expand_blocks=False,
        )
        differ = DwgDiffer.from_config(config)

        assert differ._comparison_config is config
        assert differ._comparison_config.expand_blocks is False
        assert differ._comparison_config.sensitivity.near_match_radius == 15.0

    def test_comparison_config_property(self):
        """comparison_config 프로퍼티 테스트"""
        config = ComparisonConfig()
        differ = DwgDiffer(comparison_config=config)

        assert differ.comparison_config is config

    def test_comparator_uses_comparison_config(self):
        """comparator가 ComparisonConfig를 사용하는지 테스트"""
        config = ComparisonConfig(
            sensitivity=SensitivityConfig(position_threshold=5.0),
            use_spatial_index=False,
        )
        differ = DwgDiffer(comparison_config=config)

        comparator = differ.comparator

        # comparator가 config를 사용하는지 확인
        assert comparator.config is config
        assert comparator.sensitivity["position"] == 5.0

    def test_comparison_config_overrides_legacy_ignore_layers(self):
        """ComparisonConfig가 레거시 ignore_layers를 오버라이드하는지 테스트"""
        config = ComparisonConfig(
            layer_priority=LayerPriorityConfig(
                ignore_patterns=["CUSTOM_IGNORE*"],
            ),
        )
        differ = DwgDiffer(
            ignore_layers=["Defpoints", "OldLayer"],  # 레거시 설정
            comparison_config=config,  # Config가 우선
        )

        # Config가 설정되면 ignore_layers는 빈 리스트
        assert differ.ignore_layers == []
        # comparator에서 LayerPriorityConfig가 사용됨
        assert differ.comparator._layer_priority.should_ignore("CUSTOM_IGNORE_LAYER") is True

    def test_expand_blocks_true_config(self):
        """expand_blocks=True 설정 테스트"""
        config = ComparisonConfig(expand_blocks=True)
        differ = DwgDiffer(comparison_config=config)

        assert differ._comparison_config.expand_blocks is True

    def test_expand_blocks_false_config(self):
        """expand_blocks=False 설정 테스트"""
        config = ComparisonConfig(expand_blocks=False)
        differ = DwgDiffer(comparison_config=config)

        assert differ._comparison_config.expand_blocks is False

    @patch("src.services.comparison.dwg_differ.ezdxf")
    def test_compare_passes_expand_blocks_to_extractor(self, mock_ezdxf, tmp_path):
        """compare() 메서드가 expand_blocks를 extractor에 전달하는지 테스트"""
        # Mock 설정
        mock_doc = Mock()
        mock_ezdxf.readfile.return_value = mock_doc

        # expand_blocks=True 설정
        config = ComparisonConfig(expand_blocks=True)
        differ = DwgDiffer(comparison_config=config)

        # extractor를 Mock 객체로 설정 (property가 아닌 내부 변수 사용)
        mock_extractor = Mock()
        mock_extractor.extract.return_value = {}
        differ._extractor = mock_extractor

        dxf_file = tmp_path / "test.dxf"
        dxf_file.touch()

        try:
            differ.compare(dxf_file, dxf_file)
        except Exception:
            pass  # 다른 예외 무시

        # extract가 expand_blocks=True로 호출되었는지 확인
        call_args_list = mock_extractor.extract.call_args_list
        if call_args_list:
            for call in call_args_list:
                kwargs = call.kwargs
                assert kwargs.get("expand_blocks") is True

    @patch("src.services.comparison.dwg_differ.ezdxf")
    def test_compare_passes_expand_blocks_false(self, mock_ezdxf, tmp_path):
        """expand_blocks=False가 extractor에 전달되는지 테스트"""
        # Mock 설정
        mock_doc = Mock()
        mock_ezdxf.readfile.return_value = mock_doc

        # expand_blocks=False 설정
        config = ComparisonConfig(expand_blocks=False)
        differ = DwgDiffer(comparison_config=config)

        # extractor를 Mock 객체로 설정 (property가 아닌 내부 변수 사용)
        mock_extractor = Mock()
        mock_extractor.extract.return_value = {}
        differ._extractor = mock_extractor

        dxf_file = tmp_path / "test.dxf"
        dxf_file.touch()

        try:
            differ.compare(dxf_file, dxf_file)
        except Exception:
            pass  # 다른 예외 무시

        # extract가 expand_blocks=False로 호출되었는지 확인
        call_args_list = mock_extractor.extract.call_args_list
        if call_args_list:
            for call in call_args_list:
                kwargs = call.kwargs
                assert kwargs.get("expand_blocks") is False

    def test_backward_compatibility_with_legacy_params(self):
        """레거시 파라미터와의 하위 호환성 테스트"""
        # 레거시 방식 (config 없이)
        differ = DwgDiffer(
            config={"precision": 3},
            ignore_layers=["Defpoints"],
        )

        assert differ.ignore_layers == ["Defpoints"]
        assert differ.config.get("precision") == 3
        # 기본 ComparisonConfig가 적용됨
        assert differ._comparison_config is not None


class TestCompareWorkerComparisonConfigIntegration:
    """Phase 3 P3-3: CompareWorker와 ComparisonConfig 통합 테스트

    Qt 모킹을 통한 테스트 격리를 보장합니다.
    """

    @pytest.fixture(autouse=True)
    def ensure_qt_mocked(self):
        """Qt 모듈이 올바르게 모킹되었는지 확인하고, 필요시 CompareWorker 재로드.

        Phase G2.7-FU: backup the previous sys.modules entries so we can
        restore them on teardown. Without restore, this fixture pollutes
        the rest of the pytest process and silently skips 31 tests in
        ``test_qt_pdf_adapter.py`` (which legitimately needs real Qt PDF).
        """

        import importlib

        # Backup whatever was there (real PySide6 from pytest-qt, or a
        # mock from another test file's module-load).
        backup = {
            name: sys.modules.get(name)
            for name in (
                "PySide6", "PySide6.QtCore",
                "PySide6.QtWidgets", "PySide6.QtGui",
                "src.gui.unified_load_module.workers.compare_worker",
            )
        }

        # Qt 모킹 보장
        mock_qt_core = MagicMock()
        mock_qt_core.QThread = MagicMock
        mock_qt_core.Signal = MagicMock(return_value=MagicMock())

        # sys.modules에 모킹 적용/갱신
        sys.modules["PySide6"] = MagicMock()
        sys.modules["PySide6.QtCore"] = mock_qt_core
        sys.modules["PySide6.QtWidgets"] = MagicMock()
        sys.modules["PySide6.QtGui"] = MagicMock()

        # CompareWorker 모듈 캐시 제거 후 재로드
        module_name = "src.gui.unified_load_module.workers.compare_worker"
        if module_name in sys.modules:
            del sys.modules[module_name]

        yield

        # Restore — pop the mocks we installed and put back whatever was
        # there originally so subsequent test modules can rely on real
        # PySide6 again.
        for name, original in backup.items():
            if original is not None:
                sys.modules[name] = original
            else:
                sys.modules.pop(name, None)

    def test_create_comparison_config_default(self):
        """_create_comparison_config 기본값 테스트.

        Phase Q3 (RV-20260509-002): expand_blocks default flipped
        False → True. CompareWorker mirror.
        """
        from src.gui.unified_load_module.workers.compare_worker import CompareWorker

        worker = CompareWorker("old.dxf", "new.dxf", {})
        config = worker._create_comparison_config()

        assert config.expand_blocks is True  # Phase Q3 — default flipped
        assert config.use_spatial_index is True
        assert config.sensitivity.position_threshold == 1.0
        assert config.sensitivity.near_match_radius == 10.0

    def test_create_comparison_config_with_options(self):
        """UI 옵션에서 ComparisonConfig 생성 테스트"""
        from src.gui.unified_load_module.workers.compare_worker import CompareWorker

        options = {
            "expand_blocks": False,
            "use_spatial_index": False,
            "position_threshold": 2.5,
            "near_match_radius": 15.0,
        }
        worker = CompareWorker("old.dxf", "new.dxf", options)
        config = worker._create_comparison_config()

        assert config.expand_blocks is False
        assert config.use_spatial_index is False
        assert config.sensitivity.position_threshold == 2.5
        assert config.sensitivity.near_match_radius == 15.0

    def test_create_comparison_config_partial_options(self):
        """부분 옵션에서 ComparisonConfig 생성 테스트"""
        from src.gui.unified_load_module.workers.compare_worker import CompareWorker

        options = {
            "expand_blocks": False,
            # 다른 옵션은 기본값 사용
        }
        worker = CompareWorker("old.dxf", "new.dxf", options)
        config = worker._create_comparison_config()

        assert config.expand_blocks is False
        # 나머지는 기본값
        assert config.use_spatial_index is True
        assert config.sensitivity.position_threshold == 1.0
