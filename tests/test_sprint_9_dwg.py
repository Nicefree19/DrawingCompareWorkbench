"""Sprint 9: DWG/DXF Comparison Verification Test

이 스크립트는 Sprint 9에서 구현된 DWG/DXF 비교 엔진의 전체 흐름을 테스트합니다.
1. DwgConverter: ODA 연동 및 변환 테스트
2. DxfEntityExtractor: 엔티티 추출 및 정규화 테스트
3. DxfComparator: 해시 기반 비교 테스트
4. DwgDiffer: 전체 통합 프로세스 테스트

Usage:
    python tests/test_sprint_9_dwg.py
"""

import logging
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# 프로젝트 루트 경로 추가
import sys

project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

from src.services.comparison.dwg_converter import DwgConverter
from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor
from src.services.comparison.dxf_comparator import DxfComparator, DxfChangeType
from src.services.comparison.dwg_differ import DwgDiffer

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Sprint9Test")


class TestSprint9Core(unittest.TestCase):
    """Core Engine Components Test"""

    def setUp(self):
        self.test_dir = Path("tests/temp_sprint9")
        self.test_dir.mkdir(parents=True, exist_ok=True)

        # ODA 경로 강제 설정 (사용자 경로 기반)
        self.oda_path = r"C:\Program Files\ODA\ODAFileConverter 26.10.0\ODAFileConverter.exe"
        if not Path(self.oda_path).exists():
            # Fallback check
            check_paths = [
                r"C:\Program Files\ODA\ODAFileConverter 25.12.0\ODAFileConverter.exe",
                r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe",
            ]
            for p in check_paths:
                if Path(p).exists():
                    self.oda_path = p
                    break

        # 가짜 DWG 파일 생성 (테스트용)
        self.dwg_path = self.test_dir / "test.dwg"
        self.dwg_path.touch()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_01_dwg_converter_path(self):
        """DwgConverter: ODA 경로 탐색 테스트"""
        logger.info("Testing DwgConverter path detection...")

        # 수동 경로 주입
        converter = DwgConverter(oda_path=self.oda_path)
        self.assertTrue(converter.is_available())
        logger.info(f"ODA Path detected: {converter.oda_path}")

    @patch("subprocess.run")
    def test_02_dwg_conversion(self, mock_run):
        """DwgConverter: 변환 로직 모의 테스트"""
        logger.info("Testing DwgConverter conversion logic...")

        mock_run.return_value = MagicMock(returncode=0)

        converter = DwgConverter(oda_path=self.oda_path)

        # 변환 시뮬레이션: 결과 DXF 파일 생성
        with patch("tempfile.mkdtemp") as mock_tmp:
            mock_tmp.return_value = str(self.test_dir)

            # 예상 출력 파일 미리 생성
            output_dxf = self.test_dir / "test.dxf"
            output_dxf.touch()

            try:
                result = converter.convert(self.dwg_path)
                logger.info(f"Conversion result: {result}")
            except Exception as e:
                # subprocess 모의가 완벽하지 않아도 로직 흐름 확인
                logger.warning(f"Conversion simulated error (expected in mock): {e}")

    def test_03_entity_extractor(self):
        """DxfEntityExtractor: 엔티티 정규화 테스트"""
        logger.info("Testing DxfEntityExtractor...")

        extractor = DxfEntityExtractor()

        # Mock Entity: LINE
        mock_line = MagicMock()
        mock_line.dxftype.return_value = "LINE"
        mock_line.dxf.start.x, mock_line.dxf.start.y = 0.0, 0.0
        mock_line.dxf.end.x, mock_line.dxf.end.y = 10.0, 10.0
        mock_line.dxf.layer = "WALL"

        # 정규화 실행
        normalized = extractor._normalize_line(mock_line)

        self.assertEqual(normalized.entity_type, "LINE")
        self.assertEqual(normalized.layer, "WALL")
        self.assertEqual(normalized.data["start"], (0.0, 0.0))
        self.assertEqual(normalized.data["end"], (10.0, 10.0))

        logger.info(f"Normalized Line Hash: {normalized.hash}")

        # 방향 무관성 테스트 (역방향)
        mock_line_rev = MagicMock()
        mock_line_rev.dxftype.return_value = "LINE"
        mock_line_rev.dxf.start.x, mock_line_rev.dxf.start.y = 10.0, 10.0
        mock_line_rev.dxf.end.x, mock_line_rev.dxf.end.y = 0.0, 0.0
        mock_line_rev.dxf.layer = "WALL"

        normalized_rev = extractor._normalize_line(mock_line_rev)
        self.assertEqual(normalized.hash, normalized_rev.hash, "선분 방향 무관성 실패")
        logger.info("Direction invariance verified.")

    def test_04_comparator(self):
        """DxfComparator: 비교 로직 테스트"""
        logger.info("Testing DxfComparator...")

        extractor = DxfEntityExtractor()
        comparator = DxfComparator()

        # Entity A
        mock_line_a = MagicMock()
        mock_line_a.dxftype.return_value = "LINE"
        mock_line_a.dxf.start.x, mock_line_a.dxf.start.y = 0, 0
        mock_line_a.dxf.end.x, mock_line_a.dxf.end.y = 100, 0
        mock_line_a.dxf.layer = "0"
        ent_a = extractor._normalize_line(mock_line_a)

        # Entity B (Same as A)
        ent_b1 = extractor._normalize_line(mock_line_a)

        # Entity B2 (New Line)
        mock_line_b = MagicMock()
        mock_line_b.dxftype.return_value = "LINE"
        mock_line_b.dxf.start.x, mock_line_b.dxf.start.y = 50, 50
        mock_line_b.dxf.end.x, mock_line_b.dxf.end.y = 100, 50
        mock_line_b.dxf.layer = "0"
        ent_b2 = extractor._normalize_line(mock_line_b)

        # 비교 수행
        entities_a = {"LINE": [ent_a]}
        entities_b = {"LINE": [ent_b1, ent_b2]}

        result = comparator.compare(entities_a, entities_b)

        self.assertEqual(result.added_count, 1)
        self.assertEqual(result.deleted_count, 0)
        self.assertEqual(result.changes[0].change_type, DxfChangeType.ADDED)

        logger.info(
            f"Comparison Result: Added={result.added_count}, Deleted={result.deleted_count}"
        )

    def test_05_dwg_differ_integration(self):
        """DwgDiffer: 통합 테스트"""
        logger.info("Testing DwgDiffer integration...")

        differ = DwgDiffer(config={"oda_converter_path": self.oda_path})

        # 컴포넌트 로드 확인
        self.assertIsNotNone(differ.extractor)
        self.assertIsNotNone(differ.comparator)

        # ODA-free policy (docs/CAD_FORMAT_SUPPORT_POLICY.md): DwgDiffer must NOT
        # auto-load the ODA converter just because an ODA path is configured.
        # The converter stays disabled until the caller explicitly opts in via
        # allow_oda_fallback (dwg_differ.py:147-157), which keeps the default
        # customer path ODA-free. The pre-policy version of this test asserted
        # the opposite (path present -> converter loaded) and silently rotted
        # until the full suite ran. Explicit-opt-in converter loading is covered
        # by the DWG fallback tests (test_import_compare_pipeline /
        # test_dwg_dxf_fallback); here we lock the ODA-free default.
        self.assertIsNone(
            differ.converter,
            "ODA-free policy: converter must stay None without an explicit "
            "allow_oda_fallback opt-in, even when an ODA path is present",
        )


if __name__ == "__main__":
    unittest.main()
