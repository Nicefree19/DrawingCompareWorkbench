# DXF Sample Acquisition Guide - Day 3 Pre-Validation

**작성일**: 2025-10-29
**우선순위**: ⚠️ **CRITICAL** - Day 3 착수 전 필수
**예상 시간**: 1.5-2시간

---

## 📋 개요

DXF Parser 구현 전, **실제 도면 3종 이상**을 확보하여 그리드 명명 규칙과 좌표계를 사전 검증합니다.

**목표**:
- ✅ 실제 도면 3종 확보 (일반 건축, 제조 시설, 하이테크 플랜트)
- ✅ `scripts/analyze_dxf_grids.py` 실행하여 패턴 분석
- ✅ `data/construction_terminology/terms.yaml`에 누락 패턴 추가
- ✅ `docs/api/DXF_PARSER.md` API 문서 업데이트

---

## 🎯 필요한 도면 종류

### 1. 일반 건축 도면 (Priority: P0)

**그리드 패턴 예상**:
- X축: A1, A2, A3, B1, B2, C1, C2, ...
- Y축: Y1, Y2, Y3, Y4, ...

**확보 방법**:
1. 기존 프로젝트 도면 확인 (test_data/ 또는 실제 프로젝트)
2. 무료 샘플 도면 다운로드:
   - [GrabCAD Community](https://grabcad.com/library) - "building plan" 검색
   - [CAD Blocks Free](https://www.cadblocksfree.com/) - "architectural grid" 검색
3. AutoCAD/DraftSight로 간단한 샘플 생성

**예상 특징**:
- 정규 그리드 (6m × 8m 간격)
- GRID 또는 AXIS 레이어 사용
- 한글 라벨 가능 ("통 1", "통 2")

---

### 2. 제조 시설 도면 (Priority: P1)

**그리드 패턴 예상**:
- FAB-1, FAB-2, FAB-3, ... (제조 라인)
- UTIL-1, UTIL-2, ... (유틸리티 지지대)
- X1, X2, Y1, Y2 (명시적 X/Y 접두사)

**확보 방법**:
1. 하이테크 프로젝트 아카이브 확인
2. 제조업체 표준 도면 요청
3. 온라인 커뮤니티:
   - [r/AutoCAD](https://www.reddit.com/r/AutoCAD/) - 샘플 요청 게시
   - [CAD Forum](https://www.cadforum.cz/) - "factory layout" 검색

**예상 특징**:
- 비정규 그리드 (장비 배치 기준)
- 여러 레이어 (FAB-GRID, UTIL-GRID)
- Prime notation (X1', X1'', X2')

---

### 3. 하이테크 플랜트 도면 (Priority: P2)

**그리드 패턴 예상**:
- ZONE-A1, ZONE-A2, ZONE-B1, ... (구역별 그리드)
- 장비기초 그리드 (EQ-1, EQ-2, ...)
- 복합 패턴 (A1-1, A1-2, B2-3, ...)

**확보 방법**:
1. 기존 MGT 파일과 대응되는 DXF 확인
2. 플랜트 엔지니어링 업체 협조
3. 교육용 샘플:
   - [PlantPAx Examples](https://literature.rockwellautomation.com/) - Rockwell Automation
   - [SmartPlant Foundation](https://hexagonppm.com/) - 교육 자료

**예상 특징**:
- 초고밀도 그리드 (1m × 1m 간격)
- 다층 레이어 구조
- 좌표 원점 오프셋 필요

---

## 🛠️ 도면 확보 후 검증 절차

### Step 1: 파일 저장

```bash
# 디렉토리 생성
mkdir -p test_data/dxf_samples

# 도면 저장
cp /path/to/sample1.dxf test_data/dxf_samples/sample_building.dxf
cp /path/to/sample2.dxf test_data/dxf_samples/sample_factory.dxf
cp /path/to/sample3.dxf test_data/dxf_samples/sample_plant.dxf
```

---

### Step 2: 그리드 분석 실행

```bash
# ezdxf 설치 (없는 경우)
pip install ezdxf

# 분석 스크립트 실행
python scripts/analyze_dxf_grids.py test_data/dxf_samples/sample_building.dxf
python scripts/analyze_dxf_grids.py test_data/dxf_samples/sample_factory.dxf --layer FAB-GRID
python scripts/analyze_dxf_grids.py test_data/dxf_samples/sample_plant.dxf --layer AXIS --tolerance 100
```

**출력 예시**:
```
📄 DXF 파일 분석: sample_building.dxf
======================================================================

🔍 레이어 'GRID'에서 그리드 라벨 추출 중...
✅ 발견된 라벨: 24개

📊 X/Y 그리드 분류 중 (tolerance: ±50mm)...
✅ X축 그리드: 12개
✅ Y축 그리드: 12개

======================================================================
📊 분석 결과 요약
======================================================================

🌐 좌표계 정보:
   단위: Millimeters (코드: 4)
   도면 범위: (0, 0) ~ (100000, 80000)

🔢 그리드 라벨:
   총 라벨: 24개
   X축 그리드: 12개
   Y축 그리드: 12개

📐 X축 그리드:
   A1: 0.00mm
   A2: 6000.00mm
   A3: 12000.00mm
   ...

🏷️  명명 패턴:
   ✓ Alphabet + Numeric
   ✓ Y-Prefix

📏 그리드 간격:
   X축 평균: 6000.00mm
   Y축 평균: 8000.00mm

======================================================================

💾 결과 저장: test_data/dxf_samples/sample_building.grid_analysis.json
```

---

### Step 3: 패턴 비교 및 업데이트

분석 결과를 `docs/api/DXF_PARSER.md`의 "Supported Grid Naming Patterns" 섹션과 비교합니다.

**발견된 패턴이 문서에 없는 경우**:

1. **`docs/api/DXF_PARSER.md` 업데이트**:
   ```markdown
   ### Special Patterns (실제 도면 검증 완료)

   | Pattern | Example | Description | Verified |
   |---------|---------|-------------|----------|
   | **FAB Series** | FAB-1, FAB-2 | Manufacturing line grids | ✅ sample_factory.dxf |
   | **ZONE Prefix** | ZONE-A1 | Area-specific grids | ✅ sample_plant.dxf |
   ```

2. **`data/construction_terminology/terms.yaml` 업데이트** (필요 시):
   ```yaml
   grid_patterns:
     - pattern: "^ZONE-[A-Z]+\\d+$"
       priority: 1
       description: "구역별 그리드 (하이테크 플랜트)"
       examples:
         - "ZONE-A1"
         - "ZONE-B2"
   ```

---

### Step 4: 좌표계 검증

분석된 좌표계 정보를 기록합니다:

| 도면 | 단위 | 원점 | X 방향 | Y 방향 | 비고 |
|------|------|------|--------|--------|------|
| sample_building.dxf | mm | (0, 0) | 오른쪽 | 위 | 표준 |
| sample_factory.dxf | m | (-5000, -5000) | 오른쪽 | 위 | 원점 오프셋 필요 |
| sample_plant.dxf | mm | (0, 0) | 오른쪽 | 위 | 표준 |

**원점 오프셋이 필요한 경우**:
```python
# DXF Parser API에서 origin_offset 파라미터 사용
grid = parser.extract_grid_system(
    dxf_path="sample_factory.dxf",
    origin_offset=(5000, 5000)  # 원점 조정
)
```

---

## 📊 검증 완료 체크리스트

### 도면 확보
- [ ] 일반 건축 도면 1종 (A1-A5, Y1-Y3 패턴)
- [ ] 제조 시설 도면 1종 (FAB-1, UTIL-1 패턴)
- [ ] 하이테크 플랜트 도면 1종 (ZONE-A1 또는 복합 패턴)

### 분석 실행
- [ ] 3종 도면에 대해 `analyze_dxf_grids.py` 실행 완료
- [ ] JSON 결과 파일 3개 생성 (`*.grid_analysis.json`)
- [ ] 명명 패턴 5종 이상 확인

### 문서 업데이트
- [ ] `docs/api/DXF_PARSER.md` 검증된 패턴 표시
- [ ] `data/construction_terminology/terms.yaml` 누락 패턴 추가
- [ ] 좌표계 검증 표 작성

### 통합 테스트 준비
- [ ] 테스트 데이터 디렉토리 정리 (`test_data/dxf_samples/`)
- [ ] 예상 출력 JSON 작성 (expected_outputs/)
- [ ] 단위 테스트 케이스 10개 설계

---

## 🚨 문제 해결

### Q1: 레이어 이름을 모르겠어요

**A**: DXF 파일을 AutoCAD/DraftSight로 열고 레이어 관리자를 확인합니다.

**대안**:
```python
import ezdxf
doc = ezdxf.readfile("drawing.dxf")
layers = [layer.dxf.name for layer in doc.layers]
print("사용 가능한 레이어:", layers)
```

---

### Q2: 그리드 라벨을 찾지 못했어요

**A**: 다음을 확인하세요:
1. 레이어 이름이 정확한지 (GRID vs G-GRID vs AXIS)
2. TEXT/MTEXT 엔티티인지 (LINE은 지원 안 됨)
3. 레이어가 frozen/locked 상태가 아닌지

**대안**:
```bash
# 모든 TEXT 엔티티 검색
python scripts/analyze_dxf_grids.py drawing.dxf --layer "*"
```

---

### Q3: 좌표가 이상해요 (음수 또는 초대형)

**A**: 단위 변환 또는 원점 오프셋이 필요합니다.

**단위 변환**:
- DXF Units: Meters (6) → mm 단위로 ×1000 필요
- `docs/api/DXF_PARSER.md`의 "Unit Conversion" 섹션 참조

**원점 오프셋**:
- DXF 원점 ≠ 프로젝트 원점인 경우
- `origin_offset` 파라미터로 조정

---

## 📅 타임라인

| 작업 | 예상 시간 | 담당 | 완료 기한 |
|------|-----------|------|----------|
| 도면 3종 확보 | 1-1.5시간 | 개발자 | Day 3 착수 전 |
| 분석 스크립트 실행 | 30분 | 개발자 | Day 3 착수 전 |
| 패턴 비교 및 문서 업데이트 | 30분 | 개발자 | Day 3 착수 전 |
| 테스트 케이스 설계 | 30분 | 개발자 | Day 3 중 |

**총 예상 시간**: 1.5-2시간 (병렬 작업 시 1시간)

---

## 📚 참고 자료

### 온라인 리소스
- [GrabCAD Library](https://grabcad.com/library) - 무료 CAD 모델
- [CAD Blocks Free](https://www.cadblocksfree.com/) - 무료 DXF 블록
- [Autodesk Knowledge Network](https://knowledge.autodesk.com/) - DXF 포맷 표준

### 커뮤니티
- [r/AutoCAD](https://www.reddit.com/r/AutoCAD/) - Reddit 커뮤니티
- [CAD Forum](https://www.cadforum.cz/) - 국제 CAD 포럼
- [건축CAD동호회](https://cafe.naver.com/archicad) - 네이버 카페

---

**작성자**: Claude Code SuperClaude
**다음 단계**: Day 3 DXF Parser 구현 착수
**예상 소요**: 6-8시간 (도면 확보 1.5-2시간 + 구현 4.5-6시간)
