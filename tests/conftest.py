#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
통합 Pytest 설정 파일 (conftest.py)

이 파일은 pytest에서 사용할 공통 픽스처와 설정을 정의합니다.
프로젝트 전체 테스트에서 재사용할 수 있는 공유 설정을 제공합니다.

Phase 2.3: 테스트 구조 통합 및 pytest 프레임워크 고도화
"""

import os
import sys
import io
import json
import pytest
import tempfile
import logging
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

# ===============================
# Windows 인코딩 문제 해결
# ===============================
# pytest capture 메커니즘이 한글 문자를 처리할 때 인코딩 오류 방지
if sys.platform == 'win32':
    # stdout/stderr를 UTF-8로 재설정
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    if hasattr(sys.stderr, 'reconfigure'):
        try:
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    # 환경 변수로 Python 인코딩 설정
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    os.environ.setdefault('PYTHONUTF8', '1')

# DWG 백엔드를 native로 고정 — 2026-06-11부터 GUI 기본값이 "설치된 ODA
# 자동 사용"이라, 핀 없이는 ODA가 설치된 개발 PC와 미설치 CI가 서로 다른
# 경로를 타고(설치 PC에선 픽스처 DWG에 실제 변환 subprocess 시도) 테스트가
# 기계 의존적이 된다. 자동 감지 로직 자체는 단위테스트가 env 주입으로 검증.
os.environ.setdefault('DRAWING_COMPARE_DWG_BACKEND', 'native')

# 현재 스크립트 디렉토리 기준으로 src 패키지 경로 추가
script_dir = Path(__file__).parent
project_root = script_dir.parent
sys.path.insert(0, str(project_root))

# 로깅 설정
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("pytest-tekla-mcp")

# MGT 샘플 파일 내용 - 여러 테스트에서 공유
SAMPLE_MGT_CONTENT = """*VERSION
1
*UNIT
N, mm, ton, C
*LEVEL
1, 0.000
2, 4000.000
3, 8000.000
*MATERIAL
1, name=SS400, type=STEEL, E=210000, alpha=1.20E-05, poisson=0.3, density=7.85E-09
2, name=C24, type=CONC, E=24800, alpha=1.00E-05, poisson=0.2, density=2.5E-09
*SECTION
1, name=H300X300X10X15, type=STEELSECTION
2, name=H400X200X8X13, type=STEELSECTION
3, name=B200X200X8, type=STEELSECTION
4, name=P139.8X4.5, type=STEELSECTION
5, name=RC300X500, type=RCBEAM
*NODE
1, 0.000, 0.000, 0.000
2, 4000.000, 0.000, 0.000
3, 4000.000, 4000.000, 0.000
4, 0.000, 4000.000, 0.000
5, 0.000, 0.000, 4000.000
6, 4000.000, 0.000, 4000.000
7, 4000.000, 4000.000, 4000.000
8, 0.000, 4000.000, 4000.000
*ELEMENT BEAM
1, 1, 2, section=1, material=1
2, 2, 3, section=1, material=1
3, 3, 4, section=1, material=1
4, 4, 1, section=1, material=1
5, 5, 6, section=1, material=1
6, 6, 7, section=1, material=1
7, 7, 8, section=1, material=1
8, 8, 5, section=1, material=1
*ELEMENT COLUMN
9, 1, 5, section=2, material=1
10, 2, 6, section=2, material=1
11, 3, 7, section=2, material=1
12, 4, 8, section=2, material=1
*ELEMENT PLATE
13, 1, 2, 3, 4, section=3, material=1
14, 5, 6, 7, 8, section=3, material=1
*BOUNDARY
1, node=1, dx=1, dy=1, dz=1, rx=1, ry=1, rz=1
2, node=2, dx=1, dy=1, dz=1, rx=0, ry=0, rz=0
3, node=3, dx=1, dy=1, dz=1, rx=0, ry=0, rz=0
4, node=4, dx=1, dy=1, dz=1, rx=0, ry=0, rz=0
*LOAD
1, type=POINT, node=5, FZ=-10000.0
2, type=POINT, node=6, FZ=-10000.0
3, type=POINT, node=7, FZ=-10000.0
4, type=POINT, node=8, FZ=-10000.0
*LOADCASE
1, DEAD
2, LIVE
*END"""

# 복합 MGT 파일 (성능 테스트용)
COMPLEX_MGT_CONTENT = """*VERSION
1
*UNIT
N, mm, ton, C
*LEVEL
1, 0.000
2, 4000.000
3, 8000.000
4, 12000.000
5, 16000.000
*MATERIAL
1, name=SS400, type=STEEL, E=210000, alpha=1.20E-05, poisson=0.3, density=7.85E-09
2, name=C24, type=CONC, E=24800, alpha=1.00E-05, poisson=0.2, density=2.5E-09
3, name=STS304, type=STEEL, E=200000, alpha=1.75E-05, poisson=0.3, density=8.0E-09
*SECTION
1, name=H300X300X10X15, type=STEELSECTION
2, name=H400X200X8X13, type=STEELSECTION
3, name=B200X200X8, type=STEELSECTION
4, name=P139.8X4.5, type=STEELSECTION
5, name=RC300X500, type=RCBEAM
6, name=RC400X400, type=RCCOLUMN
*NODE
1, 0.000, 0.000, 0.000
2, 4000.000, 0.000, 0.000
3, 8000.000, 0.000, 0.000
4, 0.000, 4000.000, 0.000
5, 4000.000, 4000.000, 0.000
6, 8000.000, 4000.000, 0.000
7, 0.000, 8000.000, 0.000
8, 4000.000, 8000.000, 0.000
9, 8000.000, 8000.000, 0.000
10, 0.000, 0.000, 4000.000
11, 4000.000, 0.000, 4000.000
12, 8000.000, 0.000, 4000.000
13, 0.000, 4000.000, 4000.000
14, 4000.000, 4000.000, 4000.000
15, 8000.000, 4000.000, 4000.000
16, 0.000, 8000.000, 4000.000
17, 4000.000, 8000.000, 4000.000
18, 8000.000, 8000.000, 4000.000
*ELEMENT BEAM
1, 1, 2, section=1, material=1
2, 2, 3, section=1, material=1
3, 4, 5, section=1, material=1
4, 5, 6, section=1, material=1
5, 7, 8, section=1, material=1
6, 8, 9, section=1, material=1
7, 1, 4, section=1, material=1
8, 2, 5, section=1, material=1
9, 3, 6, section=1, material=1
10, 4, 7, section=1, material=1
11, 5, 8, section=1, material=1
12, 6, 9, section=1, material=1
*ELEMENT COLUMN
13, 1, 10, section=2, material=1
14, 2, 11, section=2, material=1
15, 3, 12, section=2, material=1
16, 4, 13, section=2, material=1
17, 5, 14, section=2, material=1
18, 6, 15, section=2, material=1
19, 7, 16, section=2, material=1
20, 8, 17, section=2, material=1
21, 9, 18, section=2, material=1
*ELEMENT PLATE
22, 1, 2, 5, 4, section=3, material=1
23, 2, 3, 6, 5, section=3, material=1
24, 4, 5, 8, 7, section=3, material=1
25, 5, 6, 9, 8, section=3, material=1
*BOUNDARY
1, node=1, dx=1, dy=1, dz=1, rx=1, ry=1, rz=1
2, node=2, dx=1, dy=1, dz=1, rx=0, ry=0, rz=0
3, node=3, dx=1, dy=1, dz=1, rx=0, ry=0, rz=0
4, node=4, dx=1, dy=1, dz=1, rx=0, ry=0, rz=0
5, node=5, dx=1, dy=1, dz=1, rx=0, ry=0, rz=0
6, node=6, dx=1, dy=1, dz=1, rx=0, ry=0, rz=0
7, node=7, dx=1, dy=1, dz=1, rx=0, ry=0, rz=0
8, node=8, dx=1, dy=1, dz=1, rx=0, ry=0, rz=0
9, node=9, dx=1, dy=1, dz=1, rx=0, ry=0, rz=0
*LOAD
1, type=POINT, node=10, FZ=-10000.0
2, type=POINT, node=11, FZ=-10000.0
3, type=POINT, node=12, FZ=-10000.0
4, type=POINT, node=13, FZ=-10000.0
5, type=POINT, node=14, FZ=-10000.0
6, type=POINT, node=15, FZ=-10000.0
7, type=POINT, node=16, FZ=-10000.0
8, type=POINT, node=17, FZ=-10000.0
9, type=POINT, node=18, FZ=-10000.0
*LOADCASE
1, DEAD
2, LIVE
3, WIND
4, SEISMIC
*END"""

# ===============================
# 기본 픽스처들 (기존 호환성 유지)
# ===============================

@pytest.fixture(scope="session")
def sample_mgt_file():
    """샘플 MGT 파일 생성 픽스처 (세션 전체 유지)"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.mgt', delete=False) as tmp:
        tmp.write(SAMPLE_MGT_CONTENT)
        tmp_path = tmp.name
    
    yield tmp_path
    
    # 테스트 세션 종료 후 파일 삭제
    if os.path.exists(tmp_path):
        os.unlink(tmp_path)

@pytest.fixture(scope="session")
def invalid_mgt_file():
    """유효하지 않은 MGT 파일 생성 픽스처 (세션 전체 유지)"""
    invalid_content = """*VERSION
1
*INVALID_SECTION
this is not a valid mgt file
*END"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.mgt', delete=False) as tmp:
        tmp.write(invalid_content)
        tmp_path = tmp.name
    
    yield tmp_path
    
    # 테스트 세션 종료 후 파일 삭제
    if os.path.exists(tmp_path):
        os.unlink(tmp_path)

@pytest.fixture
def output_dir():
    """출력 디렉토리 생성 픽스처"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield tmp_dir

@pytest.fixture(scope="session")
def test_project_structure():
    """테스트용 프로젝트 디렉토리 구조 생성"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # 디렉토리 구조 생성
        os.makedirs(os.path.join(tmp_dir, "models"), exist_ok=True)
        os.makedirs(os.path.join(tmp_dir, "exports"), exist_ok=True)
        os.makedirs(os.path.join(tmp_dir, "logs"), exist_ok=True)
        
        # 기본 MGT 파일 생성
        with open(os.path.join(tmp_dir, "models", "model.mgt"), 'w') as f:
            f.write(SAMPLE_MGT_CONTENT)
        
        # 설정 파일 생성
        config = {
            "input_directory": os.path.join(tmp_dir, "models"),
            "output_directory": os.path.join(tmp_dir, "exports"),
            "log_directory": os.path.join(tmp_dir, "logs"),
            "default_encoding": "utf-8"
        }
        
        with open(os.path.join(tmp_dir, "config.json"), 'w') as f:
            json.dump(config, f, indent=2)
            
        yield tmp_dir

# ===============================
# 새로운 고도화된 픽스처들
# ===============================

@pytest.fixture(scope="session")
def complex_mgt_file():
    """복합 MGT 파일 생성 픽스처 (성능 테스트용)"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.mgt', delete=False) as tmp:
        tmp.write(COMPLEX_MGT_CONTENT)
        tmp_path = tmp.name
    
    yield tmp_path
    
    if os.path.exists(tmp_path):
        os.unlink(tmp_path)

@pytest.fixture(scope="session")
def large_mgt_file():
    """대용량 MGT 파일 생성 픽스처 (청크 처리 테스트용)"""
    # 대용량 파일 생성 (10,000개 노드)
    content = "*VERSION\n1\n*UNIT\nN, mm, ton, C\n*LEVEL\n1, 0.000\n"
    content += "*MATERIAL\n1, name=SS400, type=STEEL, E=210000, alpha=1.20E-05, poisson=0.3, density=7.85E-09\n"
    content += "*SECTION\n1, name=H300X300X10X15, type=STEELSECTION\n"
    content += "*NODE\n"
    
    # 10,000개 노드 생성
    for i in range(1, 10001):
        x = (i % 100) * 4000
        y = ((i // 100) % 100) * 4000
        z = (i // 10000) * 4000
        content += f"{i}, {x}.000, {y}.000, {z}.000\n"
    
    content += "*ELEMENT BEAM\n"
    # 빔 요소 생성
    for i in range(1, 5000):
        content += f"{i}, {i}, {i+1}, section=1, material=1\n"
    
    content += "*END\n"
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.mgt', delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    
    yield tmp_path
    
    if os.path.exists(tmp_path):
        os.unlink(tmp_path)

@pytest.fixture
def mock_tekla_environment():
    """Tekla 환경 모킹 픽스처"""
    with patch('sys.path') as mock_path:
        # Tekla API 경로 모킹
        mock_path.append.return_value = None
        
        # Tekla 모듈 모킹
        tekla_model = MagicMock()
        tekla_model.GetConnectionStatus.return_value = True
        
        with patch.dict('sys.modules', {'Tekla': MagicMock(), 'Tekla.Structures': MagicMock()}):
            yield tekla_model

@pytest.fixture
def mock_mcp_server():
    """MCP 서버 모킹 픽스처"""
    mock_server = MagicMock()
    mock_server.is_running = True
    mock_server.port = 8080
    mock_server.start.return_value = True
    mock_server.stop.return_value = True
    
    yield mock_server

@pytest.fixture
def event_loop():
    """이벤트 루프 픽스처 (비동기 테스트용)"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture
def test_data_dir():
    """테스트 데이터 디렉토리 픽스처"""
    return Path(__file__).parent / "data"

@pytest.fixture
def performance_benchmark():
    """성능 벤치마크 픽스처"""
    import time
    
    class PerformanceBenchmark:
        def __init__(self):
            self.start_time = None
            self.end_time = None
            
        def start(self):
            self.start_time = time.time()
            
        def end(self):
            self.end_time = time.time()
            
        @property
        def elapsed(self):
            if self.start_time and self.end_time:
                return self.end_time - self.start_time
            return None
    
    return PerformanceBenchmark()

# ===============================
# 테스트 설정 및 훅
# ===============================

def pytest_configure(config):
    """pytest 설정 구성"""
    # 커스텀 마커 등록
    config.addinivalue_line(
        "markers", "unit: 단위 테스트 마커"
    )
    config.addinivalue_line(
        "markers", "integration: 통합 테스트 마커"
    )
    config.addinivalue_line(
        "markers", "slow: 느린 테스트 마커"
    )
    config.addinivalue_line(
        "markers", "performance: 성능 테스트 마커"
    )

def pytest_collection_modifyitems(config, items):
    """테스트 아이템 수정"""
    for item in items:
        # 느린 테스트 자동 표시
        if "slow" in item.keywords:
            item.add_marker(pytest.mark.slow)
        
        # 성능 테스트 자동 표시
        if "performance" in item.keywords:
            item.add_marker(pytest.mark.performance) 