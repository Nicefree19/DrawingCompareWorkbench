#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
도면 비교 모듈 검증 스크립트

목적: 생성된 샘플 DXF 쌍으로 비교 모듈 정확도 검증
     예상 결과(메타데이터)와 실제 결과 비교

사용법:
    python scripts/verify_comparison_module.py
    python scripts/verify_comparison_module.py --verbose

작성일: 2025-12-23
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Any, Tuple

# 프로젝트 루트를 sys.path에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services.comparison import (
    DxfComparator,
    DxfChange,
    DxfChangeType,
    ComparisonConfig,
    SensitivityPreset,
    PriorityCalculator,
    get_default_calculator,
    TopNFilter,
    filter_top_n,
    filter_critical_changes,
)
from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor


class ComparisonVerifier:
    """도면 비교 모듈 검증기"""

    def __init__(self, samples_dir: Path, verbose: bool = False):
        self.samples_dir = Path(samples_dir)
        self.verbose = verbose
        self.metadata_path = self.samples_dir / 'comparison_samples_metadata.json'
        self.results = []

    def load_metadata(self) -> Dict:
        """메타데이터 로드"""
        if not self.metadata_path.exists():
            raise FileNotFoundError(f"Metadata not found: {self.metadata_path}")

        with open(self.metadata_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def verify_pair(self, pair_info: Dict) -> Dict:
        """
        단일 쌍 검증

        Args:
            pair_info: 쌍 메타데이터

        Returns:
            검증 결과 딕셔너리
        """
        pair_name = pair_info['pair_name']
        old_file = project_root / pair_info['old_file']
        new_file = project_root / pair_info['new_file']
        expected = pair_info['expected_changes']
        expected_summary = pair_info['summary']

        print(f"\n{'='*60}")
        print(f"[검증] {pair_name}: {pair_info['description']}")
        print(f"{'='*60}")

        # DxfComparator로 비교 수행
        config = ComparisonConfig.from_preset(SensitivityPreset.NORMAL)
        comparator = DxfComparator(config=config)
        extractor = DxfEntityExtractor()

        try:
            # 엔티티 추출
            entities_old = extractor.extract_from_file(old_file)
            entities_new = extractor.extract_from_file(new_file)

            # 비교 수행
            result = comparator.compare_with_modified_detection(
                entities_old,
                entities_new
            )

            # 결과 분석 (DxfChangeType enum 사용)
            added = len([c for c in result.changes if c.change_type == DxfChangeType.ADDED])
            deleted = len([c for c in result.changes if c.change_type == DxfChangeType.DELETED])
            modified = len([c for c in result.changes if c.change_type == DxfChangeType.MODIFIED])
            total = len(result.changes)

            # 예상과 비교
            exp_added = expected_summary['added']
            exp_deleted = expected_summary['deleted']
            exp_modified = expected_summary['modified']
            exp_total = expected_summary['total']

            # 결과 출력
            print(f"\n[예상 결과]")
            print(f"  - ADDED: {exp_added}")
            print(f"  - DELETED: {exp_deleted}")
            print(f"  - MODIFIED: {exp_modified}")
            print(f"  - TOTAL: {exp_total}")

            print(f"\n[실제 결과]")
            print(f"  - ADDED: {added}")
            print(f"  - DELETED: {deleted}")
            print(f"  - MODIFIED: {modified}")
            print(f"  - TOTAL: {total}")

            # 정확도 계산
            # ADDED/DELETED는 보통 정확하게 감지됨
            # MODIFIED는 민감도 설정에 따라 다를 수 있음
            match_added = added == exp_added
            match_deleted = deleted == exp_deleted
            # MODIFIED는 ±2 허용 (민감도 차이로 인한 오차)
            match_modified = abs(modified - exp_modified) <= 2

            passed = match_added and match_deleted and match_modified

            if passed:
                print(f"\n[결과] PASS")
            else:
                print(f"\n[결과] REVIEW NEEDED")
                if not match_added:
                    print(f"  - ADDED 불일치: 예상 {exp_added}, 실제 {added}")
                if not match_deleted:
                    print(f"  - DELETED 불일치: 예상 {exp_deleted}, 실제 {deleted}")
                if not match_modified:
                    print(f"  - MODIFIED 차이: 예상 {exp_modified}, 실제 {modified}")

            # 상세 출력 (verbose 모드)
            if self.verbose:
                print(f"\n[상세 변경 사항]")
                for i, change in enumerate(result.changes[:10], 1):
                    print(f"  {i}. [{change.change_type.value}] {change.entity_type} "
                          f"@ Layer '{change.layer}'")
                    if hasattr(change, 'change_detail') and change.change_detail:
                        print(f"      Detail: {change.change_detail}")
                if len(result.changes) > 10:
                    print(f"  ... 외 {len(result.changes) - 10}개")

            # Priority Score 테스트
            calculator = get_default_calculator()
            for change in result.changes:
                if not hasattr(change, 'priority') or change.priority is None:
                    # change_type (string)과 layer_name 필요
                    change_type_str = change.change_type.value if hasattr(change.change_type, 'value') else str(change.change_type)
                    layer_name = getattr(change, 'layer', 'UNKNOWN')
                    score = calculator.calculate(change_type_str, layer_name)
                    change.priority = score.priority_level

            # Top N Filter 테스트
            top5 = filter_top_n(result.changes, 5)
            critical = filter_critical_changes(result.changes)

            print(f"\n[Priority & Filter 검증]")
            print(f"  - Top 5 변경: {len(top5.items)}개")
            print(f"  - Critical 변경: {len(critical.items)}개")

            return {
                'pair_name': pair_name,
                'passed': passed,
                'expected': expected_summary,
                'actual': {
                    'added': added,
                    'deleted': deleted,
                    'modified': modified,
                    'total': total
                },
                'priority_test': True,
                'filter_test': True,
                'error': None
            }

        except Exception as e:
            print(f"\n[ERROR] 비교 실패: {e}")
            import traceback
            if self.verbose:
                traceback.print_exc()
            return {
                'pair_name': pair_name,
                'passed': False,
                'expected': expected_summary,
                'actual': None,
                'error': str(e)
            }

    def verify_all(self) -> Dict:
        """전체 검증 수행"""
        metadata = self.load_metadata()

        print("="*60)
        print("도면 비교 모듈 검증")
        print("="*60)
        print(f"샘플 디렉토리: {self.samples_dir}")
        print(f"총 테스트 쌍: {len(metadata['pairs'])}개")
        print(f"총 예상 변경: {metadata['total_expected_changes']}개")

        results = []
        for pair in metadata['pairs']:
            result = self.verify_pair(pair)
            results.append(result)

        # 최종 요약
        passed = sum(1 for r in results if r['passed'])
        total = len(results)

        print("\n" + "="*60)
        print("검증 요약")
        print("="*60)
        print(f"총 테스트: {total}개")
        print(f"통과: {passed}개")
        print(f"검토 필요: {total - passed}개")
        print(f"성공률: {passed/total*100:.1f}%")

        for r in results:
            status = "PASS" if r['passed'] else "REVIEW"
            error_msg = f" ({r['error']})" if r.get('error') else ""
            print(f"  - {r['pair_name']}: {status}{error_msg}")

        return {
            'total': total,
            'passed': passed,
            'results': results
        }


def main():
    parser = argparse.ArgumentParser(
        description="도면 비교 모듈 검증"
    )
    parser.add_argument(
        '--samples-dir',
        type=str,
        default='test_data/comparison_samples',
        help='샘플 DXF 디렉토리'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='상세 출력 모드'
    )

    args = parser.parse_args()

    verifier = ComparisonVerifier(
        samples_dir=args.samples_dir,
        verbose=args.verbose
    )

    try:
        results = verifier.verify_all()
        sys.exit(0 if results['passed'] == results['total'] else 1)
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        print("먼저 샘플을 생성하세요: python scripts/generate_comparison_samples.py")
        sys.exit(1)


if __name__ == '__main__':
    main()
