#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Comparison Sample DXF Generator

목적: 도면 비교 모듈 검증용 테스트 DXF 쌍 생성
     (알려진 차이점을 가진 old/new 버전)

사용법:
    python scripts/generate_comparison_samples.py
    python scripts/generate_comparison_samples.py --output test_data/comparison_samples

출력:
    - pair1_old.dxf / pair1_new.dxf: 기본 구조 변경 (ADDED/DELETED)
    - pair2_old.dxf / pair2_new.dxf: 치수 및 위치 변경 (MODIFIED)
    - pair3_old.dxf / pair3_new.dxf: 복합 변경 (모든 유형)

작성일: 2025-12-23
상태: Phase 3+ 검증용
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Any
from dataclasses import dataclass, asdict

try:
    import ezdxf
except ImportError:
    print("ERROR: ezdxf not installed")
    print("Install: pip install ezdxf")
    sys.exit(1)


@dataclass
class ExpectedChange:
    """예상되는 변경 사항"""
    change_type: str  # ADDED, DELETED, MODIFIED
    entity_type: str  # LINE, CIRCLE, TEXT, ARC
    layer: str
    description: str
    old_data: Dict = None
    new_data: Dict = None


@dataclass
class ComparisonPairInfo:
    """비교 쌍 정보"""
    pair_name: str
    old_file: str
    new_file: str
    description: str
    expected_changes: List[ExpectedChange]

    def to_dict(self) -> Dict:
        return {
            "pair_name": self.pair_name,
            "old_file": self.old_file,
            "new_file": self.new_file,
            "description": self.description,
            "expected_changes": [asdict(c) for c in self.expected_changes],
            "summary": {
                "added": len([c for c in self.expected_changes if c.change_type == "ADDED"]),
                "deleted": len([c for c in self.expected_changes if c.change_type == "DELETED"]),
                "modified": len([c for c in self.expected_changes if c.change_type == "MODIFIED"]),
                "total": len(self.expected_changes)
            }
        }


class ComparisonSampleGenerator:
    """비교 테스트용 샘플 DXF 쌍 생성기"""

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.pairs_info: List[ComparisonPairInfo] = []

    def generate_pair1(self) -> ComparisonPairInfo:
        """
        Pair 1: 기본 구조 변경 (ADDED/DELETED)

        Old: 기본 구조물 (보, 기둥)
        New: 보 2개 추가, 기둥 1개 삭제
        """
        # === OLD 버전 생성 ===
        doc_old = ezdxf.new('R2010')
        msp_old = doc_old.modelspace()

        # 레이어 설정
        doc_old.layers.add('BEAM', color=3)  # Green
        doc_old.layers.add('COLUMN', color=5)  # Blue
        doc_old.layers.add('GRID', color=7)  # White

        # 그리드 라인 (공통)
        for x in [0, 6000, 12000, 18000]:
            msp_old.add_line((x, 0), (x, 12000), dxfattribs={'layer': 'GRID'})
        for y in [0, 6000, 12000]:
            msp_old.add_line((0, y), (18000, y), dxfattribs={'layer': 'GRID'})

        # 보 (BEAM) - 4개
        beams_old = [
            ((0, 6000), (6000, 6000)),    # B1
            ((6000, 6000), (12000, 6000)), # B2
            ((12000, 6000), (18000, 6000)), # B3
            ((0, 12000), (6000, 12000)),   # B4 (삭제 예정 아님)
        ]
        for start, end in beams_old:
            msp_old.add_line(start, end, dxfattribs={'layer': 'BEAM'})

        # 기둥 (COLUMN) - 4개 (원으로 표현)
        columns_old = [
            (0, 0, 300),      # C1
            (6000, 0, 300),   # C2
            (12000, 0, 300),  # C3 - 삭제 예정
            (18000, 0, 300),  # C4
        ]
        for x, y, r in columns_old:
            msp_old.add_circle((x, y), r, dxfattribs={'layer': 'COLUMN'})

        old_path = self.output_dir / 'pair1_old.dxf'
        doc_old.saveas(old_path)

        # === NEW 버전 생성 ===
        doc_new = ezdxf.new('R2010')
        msp_new = doc_new.modelspace()

        doc_new.layers.add('BEAM', color=3)
        doc_new.layers.add('COLUMN', color=5)
        doc_new.layers.add('GRID', color=7)

        # 그리드 라인 (동일)
        for x in [0, 6000, 12000, 18000]:
            msp_new.add_line((x, 0), (x, 12000), dxfattribs={'layer': 'GRID'})
        for y in [0, 6000, 12000]:
            msp_new.add_line((0, y), (18000, y), dxfattribs={'layer': 'GRID'})

        # 보 (BEAM) - 6개 (2개 추가)
        beams_new = [
            ((0, 6000), (6000, 6000)),     # B1
            ((6000, 6000), (12000, 6000)),  # B2
            ((12000, 6000), (18000, 6000)), # B3
            ((0, 12000), (6000, 12000)),    # B4
            ((6000, 12000), (12000, 12000)), # B5 - 추가됨
            ((12000, 12000), (18000, 12000)), # B6 - 추가됨
        ]
        for start, end in beams_new:
            msp_new.add_line(start, end, dxfattribs={'layer': 'BEAM'})

        # 기둥 (COLUMN) - 3개 (C3 삭제됨)
        columns_new = [
            (0, 0, 300),
            (6000, 0, 300),
            # (12000, 0, 300) - 삭제됨
            (18000, 0, 300),
        ]
        for x, y, r in columns_new:
            msp_new.add_circle((x, y), r, dxfattribs={'layer': 'COLUMN'})

        new_path = self.output_dir / 'pair1_new.dxf'
        doc_new.saveas(new_path)

        # 예상 변경 사항
        expected = [
            ExpectedChange(
                change_type="ADDED",
                entity_type="LINE",
                layer="BEAM",
                description="B5: 상부 보 추가 (6000,12000)-(12000,12000)",
                new_data={"start": (6000, 12000), "end": (12000, 12000)}
            ),
            ExpectedChange(
                change_type="ADDED",
                entity_type="LINE",
                layer="BEAM",
                description="B6: 상부 보 추가 (12000,12000)-(18000,12000)",
                new_data={"start": (12000, 12000), "end": (18000, 12000)}
            ),
            ExpectedChange(
                change_type="DELETED",
                entity_type="CIRCLE",
                layer="COLUMN",
                description="C3: 기둥 삭제 (12000,0) R300",
                old_data={"center": (12000, 0), "radius": 300}
            ),
        ]

        info = ComparisonPairInfo(
            pair_name="pair1",
            old_file=str(old_path),
            new_file=str(new_path),
            description="기본 구조 변경: 보 2개 추가, 기둥 1개 삭제",
            expected_changes=expected
        )

        print(f"[OK] Pair 1 생성 완료")
        print(f"   - Old: {old_path}")
        print(f"   - New: {new_path}")
        print(f"   - 예상 변경: ADDED={len([c for c in expected if c.change_type=='ADDED'])}, DELETED={len([c for c in expected if c.change_type=='DELETED'])}")

        return info

    def generate_pair2(self) -> ComparisonPairInfo:
        """
        Pair 2: 치수 및 위치 변경 (MODIFIED)

        Old: 기본 치수의 구조물
        New: 일부 요소 크기/위치 변경
        """
        # === OLD 버전 생성 ===
        doc_old = ezdxf.new('R2010')
        msp_old = doc_old.modelspace()

        doc_old.layers.add('BEAM', color=3)
        doc_old.layers.add('COLUMN', color=5)
        doc_old.layers.add('DIMENSION', color=1)  # Red

        # 보 (BEAM)
        msp_old.add_line((0, 5000), (8000, 5000), dxfattribs={'layer': 'BEAM'})  # 8m 보
        msp_old.add_line((0, 10000), (8000, 10000), dxfattribs={'layer': 'BEAM'})

        # 기둥 (COLUMN) - R=400
        msp_old.add_circle((0, 0), 400, dxfattribs={'layer': 'COLUMN'})
        msp_old.add_circle((8000, 0), 400, dxfattribs={'layer': 'COLUMN'})

        # 치수선 (텍스트로 표현)
        msp_old.add_text("8000", dxfattribs={'layer': 'DIMENSION', 'height': 150}).set_placement((4000, 5500))

        old_path = self.output_dir / 'pair2_old.dxf'
        doc_old.saveas(old_path)

        # === NEW 버전 생성 ===
        doc_new = ezdxf.new('R2010')
        msp_new = doc_new.modelspace()

        doc_new.layers.add('BEAM', color=3)
        doc_new.layers.add('COLUMN', color=5)
        doc_new.layers.add('DIMENSION', color=1)

        # 보 (BEAM) - 길이 변경 8m -> 9m
        msp_new.add_line((0, 5000), (9000, 5000), dxfattribs={'layer': 'BEAM'})  # 9m 보
        msp_new.add_line((0, 10000), (9000, 10000), dxfattribs={'layer': 'BEAM'})  # 위치도 변경

        # 기둥 (COLUMN) - R=500 (크기 변경)
        msp_new.add_circle((0, 0), 500, dxfattribs={'layer': 'COLUMN'})
        msp_new.add_circle((9000, 0), 500, dxfattribs={'layer': 'COLUMN'})  # 위치 변경

        # 치수선 - 값 변경
        msp_new.add_text("9000", dxfattribs={'layer': 'DIMENSION', 'height': 150}).set_placement((4500, 5500))

        new_path = self.output_dir / 'pair2_new.dxf'
        doc_new.saveas(new_path)

        # 예상 변경 사항
        expected = [
            ExpectedChange(
                change_type="MODIFIED",
                entity_type="LINE",
                layer="BEAM",
                description="하부 보 길이 변경: 8000mm -> 9000mm",
                old_data={"start": (0, 5000), "end": (8000, 5000)},
                new_data={"start": (0, 5000), "end": (9000, 5000)}
            ),
            ExpectedChange(
                change_type="MODIFIED",
                entity_type="LINE",
                layer="BEAM",
                description="상부 보 길이 변경: 8000mm -> 9000mm",
                old_data={"start": (0, 10000), "end": (8000, 10000)},
                new_data={"start": (0, 10000), "end": (9000, 10000)}
            ),
            ExpectedChange(
                change_type="MODIFIED",
                entity_type="CIRCLE",
                layer="COLUMN",
                description="좌측 기둥 크기 변경: R400 -> R500",
                old_data={"center": (0, 0), "radius": 400},
                new_data={"center": (0, 0), "radius": 500}
            ),
            ExpectedChange(
                change_type="MODIFIED",
                entity_type="CIRCLE",
                layer="COLUMN",
                description="우측 기둥 위치/크기 변경: (8000,0) R400 -> (9000,0) R500",
                old_data={"center": (8000, 0), "radius": 400},
                new_data={"center": (9000, 0), "radius": 500}
            ),
            ExpectedChange(
                change_type="MODIFIED",
                entity_type="TEXT",
                layer="DIMENSION",
                description="치수 텍스트 변경: 8000 -> 9000",
                old_data={"text": "8000", "position": (4000, 5500)},
                new_data={"text": "9000", "position": (4500, 5500)}
            ),
        ]

        info = ComparisonPairInfo(
            pair_name="pair2",
            old_file=str(old_path),
            new_file=str(new_path),
            description="치수 및 위치 변경: 보 길이, 기둥 크기/위치, 치수 텍스트",
            expected_changes=expected
        )

        print(f"[OK] Pair 2 생성 완료")
        print(f"   - Old: {old_path}")
        print(f"   - New: {new_path}")
        print(f"   - 예상 변경: MODIFIED={len(expected)}")

        return info

    def generate_pair3(self) -> ComparisonPairInfo:
        """
        Pair 3: 복합 변경 (모든 유형)

        Old: 복잡한 구조물
        New: ADDED + DELETED + MODIFIED 복합 변경
        """
        # === OLD 버전 생성 ===
        doc_old = ezdxf.new('R2010')
        msp_old = doc_old.modelspace()

        doc_old.layers.add('S-BEAM', color=3)
        doc_old.layers.add('S-COLUMN', color=5)
        doc_old.layers.add('S-BRACE', color=4)  # Cyan
        doc_old.layers.add('A-TEXT', color=7)
        doc_old.layers.add('GRID', color=8)

        # 그리드
        for x in [0, 6000, 12000]:
            msp_old.add_line((x, 0), (x, 12000), dxfattribs={'layer': 'GRID'})
        for y in [0, 6000, 12000]:
            msp_old.add_line((0, y), (12000, y), dxfattribs={'layer': 'GRID'})

        # 보 (S-BEAM)
        msp_old.add_line((0, 6000), (6000, 6000), dxfattribs={'layer': 'S-BEAM'})
        msp_old.add_line((6000, 6000), (12000, 6000), dxfattribs={'layer': 'S-BEAM'})
        msp_old.add_line((0, 12000), (6000, 12000), dxfattribs={'layer': 'S-BEAM'})  # 삭제 예정

        # 기둥 (S-COLUMN)
        msp_old.add_circle((0, 0), 350, dxfattribs={'layer': 'S-COLUMN'})
        msp_old.add_circle((6000, 0), 350, dxfattribs={'layer': 'S-COLUMN'})
        msp_old.add_circle((12000, 0), 350, dxfattribs={'layer': 'S-COLUMN'})

        # 가새 (S-BRACE)
        msp_old.add_line((0, 0), (6000, 6000), dxfattribs={'layer': 'S-BRACE'})  # 수정 예정

        # 텍스트
        msp_old.add_text("Level 1", dxfattribs={'layer': 'A-TEXT', 'height': 200}).set_placement((-500, 6000))
        msp_old.add_text("Level 2", dxfattribs={'layer': 'A-TEXT', 'height': 200}).set_placement((-500, 12000))  # 삭제 예정

        old_path = self.output_dir / 'pair3_old.dxf'
        doc_old.saveas(old_path)

        # === NEW 버전 생성 ===
        doc_new = ezdxf.new('R2010')
        msp_new = doc_new.modelspace()

        doc_new.layers.add('S-BEAM', color=3)
        doc_new.layers.add('S-COLUMN', color=5)
        doc_new.layers.add('S-BRACE', color=4)
        doc_new.layers.add('A-TEXT', color=7)
        doc_new.layers.add('GRID', color=8)

        # 그리드 (동일)
        for x in [0, 6000, 12000]:
            msp_new.add_line((x, 0), (x, 12000), dxfattribs={'layer': 'GRID'})
        for y in [0, 6000, 12000]:
            msp_new.add_line((0, y), (12000, y), dxfattribs={'layer': 'GRID'})

        # 보 (S-BEAM) - 1개 삭제, 1개 추가
        msp_new.add_line((0, 6000), (6000, 6000), dxfattribs={'layer': 'S-BEAM'})
        msp_new.add_line((6000, 6000), (12000, 6000), dxfattribs={'layer': 'S-BEAM'})
        # (0, 12000)-(6000, 12000) 삭제됨
        msp_new.add_line((6000, 12000), (12000, 12000), dxfattribs={'layer': 'S-BEAM'})  # 추가됨

        # 기둥 (S-COLUMN) - 크기 변경
        msp_new.add_circle((0, 0), 400, dxfattribs={'layer': 'S-COLUMN'})  # 350->400
        msp_new.add_circle((6000, 0), 400, dxfattribs={'layer': 'S-COLUMN'})  # 350->400
        msp_new.add_circle((12000, 0), 350, dxfattribs={'layer': 'S-COLUMN'})  # 동일

        # 가새 (S-BRACE) - 방향 변경
        msp_new.add_line((6000, 0), (0, 6000), dxfattribs={'layer': 'S-BRACE'})  # 반대 방향
        msp_new.add_line((6000, 0), (12000, 6000), dxfattribs={'layer': 'S-BRACE'})  # 추가

        # 텍스트 - 수정 및 삭제
        msp_new.add_text("Level 1F", dxfattribs={'layer': 'A-TEXT', 'height': 200}).set_placement((-500, 6000))  # 수정
        # Level 2 삭제됨
        msp_new.add_text("Level 2F", dxfattribs={'layer': 'A-TEXT', 'height': 200}).set_placement((-500, 12000))  # 새로 추가

        new_path = self.output_dir / 'pair3_new.dxf'
        doc_new.saveas(new_path)

        # 예상 변경 사항
        expected = [
            # DELETED
            ExpectedChange(
                change_type="DELETED",
                entity_type="LINE",
                layer="S-BEAM",
                description="좌측 상부 보 삭제: (0,12000)-(6000,12000)",
                old_data={"start": (0, 12000), "end": (6000, 12000)}
            ),
            ExpectedChange(
                change_type="DELETED",
                entity_type="LINE",
                layer="S-BRACE",
                description="가새 삭제: (0,0)-(6000,6000)",
                old_data={"start": (0, 0), "end": (6000, 6000)}
            ),
            ExpectedChange(
                change_type="DELETED",
                entity_type="TEXT",
                layer="A-TEXT",
                description="텍스트 삭제: 'Level 2'",
                old_data={"text": "Level 2", "position": (-500, 12000)}
            ),
            # ADDED
            ExpectedChange(
                change_type="ADDED",
                entity_type="LINE",
                layer="S-BEAM",
                description="우측 상부 보 추가: (6000,12000)-(12000,12000)",
                new_data={"start": (6000, 12000), "end": (12000, 12000)}
            ),
            ExpectedChange(
                change_type="ADDED",
                entity_type="LINE",
                layer="S-BRACE",
                description="가새 추가 (반전): (6000,0)-(0,6000)",
                new_data={"start": (6000, 0), "end": (0, 6000)}
            ),
            ExpectedChange(
                change_type="ADDED",
                entity_type="LINE",
                layer="S-BRACE",
                description="가새 추가 (우측): (6000,0)-(12000,6000)",
                new_data={"start": (6000, 0), "end": (12000, 6000)}
            ),
            ExpectedChange(
                change_type="ADDED",
                entity_type="TEXT",
                layer="A-TEXT",
                description="텍스트 추가: 'Level 2F'",
                new_data={"text": "Level 2F", "position": (-500, 12000)}
            ),
            # MODIFIED
            ExpectedChange(
                change_type="MODIFIED",
                entity_type="CIRCLE",
                layer="S-COLUMN",
                description="좌측 기둥 크기 변경: R350 -> R400",
                old_data={"center": (0, 0), "radius": 350},
                new_data={"center": (0, 0), "radius": 400}
            ),
            ExpectedChange(
                change_type="MODIFIED",
                entity_type="CIRCLE",
                layer="S-COLUMN",
                description="중앙 기둥 크기 변경: R350 -> R400",
                old_data={"center": (6000, 0), "radius": 350},
                new_data={"center": (6000, 0), "radius": 400}
            ),
            ExpectedChange(
                change_type="MODIFIED",
                entity_type="TEXT",
                layer="A-TEXT",
                description="텍스트 변경: 'Level 1' -> 'Level 1F'",
                old_data={"text": "Level 1"},
                new_data={"text": "Level 1F"}
            ),
        ]

        info = ComparisonPairInfo(
            pair_name="pair3",
            old_file=str(old_path),
            new_file=str(new_path),
            description="복합 변경: 보/가새/텍스트 추가/삭제, 기둥 크기 변경",
            expected_changes=expected
        )

        added = len([c for c in expected if c.change_type == "ADDED"])
        deleted = len([c for c in expected if c.change_type == "DELETED"])
        modified = len([c for c in expected if c.change_type == "MODIFIED"])

        print(f"[OK] Pair 3 생성 완료")
        print(f"   - Old: {old_path}")
        print(f"   - New: {new_path}")
        print(f"   - 예상 변경: ADDED={added}, DELETED={deleted}, MODIFIED={modified}")

        return info

    def generate_all(self) -> List[ComparisonPairInfo]:
        """전체 비교 샘플 쌍 생성"""
        print(">> 비교 테스트용 DXF 쌍 생성 시작...")
        print(f"출력 디렉토리: {self.output_dir.absolute()}\n")

        pairs = [
            self.generate_pair1(),
            self.generate_pair2(),
            self.generate_pair3(),
        ]

        # 메타데이터 JSON 저장
        metadata = {
            "description": "도면 비교 모듈 검증용 테스트 DXF 쌍",
            "generated_by": "generate_comparison_samples.py",
            "pairs": [p.to_dict() for p in pairs],
            "total_expected_changes": sum(len(p.expected_changes) for p in pairs)
        }

        metadata_path = self.output_dir / 'comparison_samples_metadata.json'
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2, default=str)

        print(f"\n>> 전체 {len(pairs)}개 쌍 생성 완료")
        print(f">> 메타데이터: {metadata_path}")
        print(f">> 총 예상 변경 수: {metadata['total_expected_changes']}")

        return pairs


def main():
    """메인 실행 함수"""
    parser = argparse.ArgumentParser(
        description="도면 비교 모듈 검증용 테스트 DXF 쌍 생성"
    )
    parser.add_argument(
        '--output',
        type=str,
        default='test_data/comparison_samples',
        help='DXF 파일 출력 디렉토리'
    )

    args = parser.parse_args()

    generator = ComparisonSampleGenerator(output_dir=args.output)
    pairs = generator.generate_all()

    print("\n>> 검증 방법:")
    print("1. DxfComparator로 각 쌍 비교:")
    for pair in pairs:
        print(f"   - {pair.pair_name}: {pair.description}")
    print("\n2. 예상 결과와 실제 결과 대조:")
    print(f"   - 메타데이터 파일: {args.output}/comparison_samples_metadata.json")
    print("\n3. 시각화 확인 (VisualizationService)")


if __name__ == '__main__':
    main()
