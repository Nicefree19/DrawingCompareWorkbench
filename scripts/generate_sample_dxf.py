#!/usr/bin/env python3
"""
Sample DXF Generator - Day 3 Test Data Creation

목적: DXF Parser 개발을 위한 테스트 샘플 3종 생성
     (실제 도면 투입 전 기본 패턴 검증용)

사용법:
    python scripts/generate_sample_dxf.py
    python scripts/generate_sample_dxf.py --output test_data/dxf_samples

출력:
    - sample_building.dxf: 일반 건물 그리드 (A1-A5, Y1-Y3)
    - sample_factory.dxf: 공장 그리드 (FAB-1, UTIL-1)
    - sample_plant.dxf: 복합 패턴 (ZONE-A1, 혼합)

작성일: 2025-10-30
상태: Day 3 초안 (실제 도면 대기 중)
"""

import argparse
import sys
from pathlib import Path
from typing import List, Tuple

try:
    import ezdxf
except ImportError:
    print("❌ ezdxf 라이브러리가 설치되지 않았습니다.")
    print("설치 명령어: pip install ezdxf")
    sys.exit(1)


class SampleDXFGenerator:
    """테스트용 샘플 DXF 생성기"""

    def __init__(self, output_dir: Path):
        """
        Args:
            output_dir: DXF 파일 출력 디렉토리
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_building_sample(self) -> Path:
        """
        일반 건물 그리드 샘플 생성

        패턴: Alphabet + Numeric (A1-A5, Y1-Y3)
        용도: 기본 X/Y 그리드 분류 테스트

        Returns:
            생성된 DXF 파일 경로
        """
        doc = ezdxf.new('R2010')
        msp = doc.modelspace()

        # X축 그리드 (A1-A5, 6m 간격)
        x_labels = ['A1', 'A2', 'A3', 'A4', 'A5']
        for i, label in enumerate(x_labels):
            x = i * 6000  # 6m = 6000mm
            y = 0
            msp.add_text(
                label,
                dxfattribs={
                    'layer': 'GRID',
                    'height': 200,
                }
            ).set_placement((x, y))

        # Y축 그리드 (Y1-Y3, 8m 간격)
        y_labels = ['Y1', 'Y2', 'Y3']
        for i, label in enumerate(y_labels):
            x = 0
            y = i * 8000  # 8m = 8000mm
            msp.add_text(
                label,
                dxfattribs={
                    'layer': 'GRID',
                    'height': 200,
                }
            ).set_placement((x, y))

        # 그리드 라인 추가 (시각화용)
        for i, _ in enumerate(x_labels):
            x = i * 6000
            msp.add_line((x, 0), (x, 16000), dxfattribs={'layer': 'GRID'})

        for i, _ in enumerate(y_labels):
            y = i * 8000
            msp.add_line((0, y), (24000, y), dxfattribs={'layer': 'GRID'})

        output_path = self.output_dir / 'sample_building.dxf'
        doc.saveas(output_path)
        print(f"[OK] 생성 완료: {output_path}")
        print(f"   - X축 그리드: {', '.join(x_labels)} (6m 간격)")
        print(f"   - Y축 그리드: {', '.join(y_labels)} (8m 간격)")

        return output_path

    def generate_factory_sample(self) -> Path:
        """
        공장 그리드 샘플 생성

        패턴: FAB Series (FAB-1, FAB-2), UTIL Series
        용도: 특수 명명 규칙 테스트

        Returns:
            생성된 DXF 파일 경로
        """
        doc = ezdxf.new('R2010')
        msp = doc.modelspace()

        # FAB 라인 그리드 (FAB-1 ~ FAB-4, 10m 간격)
        fab_labels = ['FAB-1', 'FAB-2', 'FAB-3', 'FAB-4']
        for i, label in enumerate(fab_labels):
            x = i * 10000  # 10m = 10000mm
            y = 0
            msp.add_text(
                label,
                dxfattribs={
                    'layer': 'GRID',
                    'height': 250,
                }
            ).set_placement((x, y))

        # UTIL 라인 그리드 (UTIL-1 ~ UTIL-3, 12m 간격)
        util_labels = ['UTIL-1', 'UTIL-2', 'UTIL-3']
        for i, label in enumerate(util_labels):
            x = 0
            y = i * 12000  # 12m = 12000mm
            msp.add_text(
                label,
                dxfattribs={
                    'layer': 'GRID',
                    'height': 250,
                }
            ).set_placement((x, y))

        # 그리드 라인 추가
        for i, _ in enumerate(fab_labels):
            x = i * 10000
            msp.add_line((x, 0), (x, 24000), dxfattribs={'layer': 'GRID'})

        for i, _ in enumerate(util_labels):
            y = i * 12000
            msp.add_line((0, y), (30000, y), dxfattribs={'layer': 'GRID'})

        output_path = self.output_dir / 'sample_factory.dxf'
        doc.saveas(output_path)
        print(f"[OK] 생성 완료: {output_path}")
        print(f"   - FAB 라인: {', '.join(fab_labels)} (10m 간격)")
        print(f"   - UTIL 라인: {', '.join(util_labels)} (12m 간격)")

        return output_path

    def generate_plant_sample(self) -> Path:
        """
        복합 패턴 샘플 생성

        패턴: ZONE 접두사 + Alphabet-Numeric, Prime Notation
        용도: 복잡한 명명 규칙 테스트

        Returns:
            생성된 DXF 파일 경로
        """
        doc = ezdxf.new('R2010')
        msp = doc.modelspace()

        # ZONE-A 그리드 (ZONE-A1 ~ ZONE-A4, 7m 간격)
        zone_a_labels = ['ZONE-A1', 'ZONE-A2', 'ZONE-A3', 'ZONE-A4']
        for i, label in enumerate(zone_a_labels):
            x = i * 7000  # 7m = 7000mm
            y = 0
            msp.add_text(
                label,
                dxfattribs={
                    'layer': 'GRID',
                    'height': 200,
                    'insert': (x, y),
                }
            )

        # Y축 그리드 + Prime Notation (Y1, Y2, Y2')
        y_labels = ['Y1', 'Y2', "Y2'", 'Y3']
        y_positions = [0, 9000, 10500, 18000]  # Y2와 Y2' 사이 1.5m

        for label, y_pos in zip(y_labels, y_positions):
            x = 0
            msp.add_text(
                label,
                dxfattribs={
                    'layer': 'GRID',
                    'height': 200,
                    'insert': (x, y_pos),
                }
            )

        # 그리드 라인 추가
        for i, _ in enumerate(zone_a_labels):
            x = i * 7000
            msp.add_line((x, 0), (x, 18000), dxfattribs={'layer': 'GRID'})

        for y_pos in y_positions:
            msp.add_line((0, y_pos), (21000, y_pos), dxfattribs={'layer': 'GRID'})

        output_path = self.output_dir / 'sample_plant.dxf'
        doc.saveas(output_path)
        print(f"[OK] 생성 완료: {output_path}")
        print(f"   - ZONE-A 라인: {', '.join(zone_a_labels)} (7m 간격)")
        print(f"   - Y축 (Prime 포함): {', '.join(y_labels)}")

        return output_path

    def generate_all(self) -> List[Path]:
        """
        전체 샘플 DXF 3종 생성

        Returns:
            생성된 파일 경로 리스트
        """
        print(">> 샘플 DXF 파일 생성 시작...")
        print(f"출력 디렉토리: {self.output_dir.absolute()}\n")

        files = [
            self.generate_building_sample(),
            self.generate_factory_sample(),
            self.generate_plant_sample(),
        ]

        print(f"\n>> 전체 {len(files)}개 파일 생성 완료")
        return files


def main():
    """메인 실행 함수"""
    parser = argparse.ArgumentParser(
        description="DXF Parser 테스트용 샘플 파일 3종 생성"
    )
    parser.add_argument(
        '--output',
        type=str,
        default='test_data/dxf_samples',
        help='DXF 파일 출력 디렉토리 (기본값: test_data/dxf_samples)'
    )

    args = parser.parse_args()

    generator = SampleDXFGenerator(output_dir=args.output)
    generated_files = generator.generate_all()

    print("\n>> 다음 단계:")
    print("1. analyze_dxf_grids.py 실행:")
    for file in generated_files:
        print(f"   python scripts/analyze_dxf_grids.py {file}")
    print("\n2. JSON 결과 확인 및 패턴 검증")
    print("3. 실제 도면 투입 후 패턴 업데이트")


if __name__ == '__main__':
    main()
