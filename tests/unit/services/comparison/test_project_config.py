# -*- coding: utf-8 -*-
"""Project Config (QW-4) 단위 테스트

Phase 3+ QW-4: 프로젝트 설정 저장/로드 기능 테스트
"""

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from src.services.comparison.project_config import (
    ProjectMetadata,
    ProjectConfig,
    RecentProject,
    RecentProjectsManager,
    get_recent_projects_manager,
    save_project_config,
    load_project_config,
)
from src.services.comparison.comparison_config import (
    SensitivityPreset,
    SensitivityConfig,
    ComparisonConfig,
)
from src.services.comparison.visualization_service import ColorConfig


class TestProjectMetadata:
    """ProjectMetadata 테스트"""

    def test_default_values(self):
        """기본값 테스트"""
        meta = ProjectMetadata()
        assert meta.name == "Untitled Project"
        assert meta.description == ""
        assert meta.version == "1.0.0"
        assert meta.created_at != ""  # 자동 설정
        assert meta.updated_at != ""

    def test_custom_values(self):
        """사용자 지정 값 테스트"""
        meta = ProjectMetadata(
            name="Test Project",
            description="A test project",
            old_file_path="/path/to/old.dxf",
            new_file_path="/path/to/new.dxf",
        )
        assert meta.name == "Test Project"
        assert meta.description == "A test project"
        assert meta.old_file_path == "/path/to/old.dxf"
        assert meta.new_file_path == "/path/to/new.dxf"

    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        meta = ProjectMetadata(name="Test")
        data = meta.to_dict()

        assert data["name"] == "Test"
        assert "created_at" in data
        assert "updated_at" in data
        assert "version" in data

    def test_from_dict(self):
        """딕셔너리에서 생성 테스트"""
        data = {
            "name": "Loaded Project",
            "description": "From dict",
            "version": "2.0.0",
        }
        meta = ProjectMetadata.from_dict(data)

        assert meta.name == "Loaded Project"
        assert meta.description == "From dict"
        assert meta.version == "2.0.0"

    def test_touch_updates_time(self):
        """touch() 수정 시간 업데이트 테스트"""
        meta = ProjectMetadata()
        original_updated = meta.updated_at

        import time
        time.sleep(0.01)
        meta.touch()

        assert meta.updated_at != original_updated


class TestProjectConfig:
    """ProjectConfig 테스트"""

    def test_create_new_default(self):
        """새 프로젝트 기본 생성 테스트"""
        config = ProjectConfig.create_new()

        assert config.metadata.name == "Untitled Project"
        assert config.sensitivity_preset == "normal"
        assert config.top_n_filter == 0
        assert config.dwg_render_quality == "auto"
        assert isinstance(config.comparison, ComparisonConfig)
        assert isinstance(config.color, ColorConfig)

    def test_create_new_with_name(self):
        """이름 지정 생성 테스트"""
        config = ProjectConfig.create_new(
            name="My Project",
            description="Test description",
        )

        assert config.metadata.name == "My Project"
        assert config.metadata.description == "Test description"

    def test_create_new_with_preset(self):
        """프리셋 지정 생성 테스트"""
        config = ProjectConfig.create_new(
            name="Strict Project",
            preset=SensitivityPreset.STRICT,
        )

        assert config.sensitivity_preset == "strict"
        assert config.comparison.sensitivity.position_threshold == 0.1

    def test_from_preset(self):
        """from_preset 팩토리 테스트"""
        config = ProjectConfig.from_preset(SensitivityPreset.RELAXED)

        assert "완화" in config.metadata.name
        assert config.sensitivity_preset == "relaxed"
        assert config.comparison.sensitivity.position_threshold == 5.0

    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        config = ProjectConfig.create_new(name="Test")
        data = config.to_dict()

        assert "metadata" in data
        assert "comparison" in data
        assert "color" in data
        assert "sensitivity_preset" in data
        assert "top_n_filter" in data
        assert "dwg_render_quality" in data

    def test_from_dict(self):
        """딕셔너리에서 생성 테스트"""
        data = {
            "metadata": {"name": "Loaded"},
            "comparison": {"sensitivity": {"position_threshold": 2.5}},
            "color": {"show_deleted": False},
            "sensitivity_preset": "strict",
            "top_n_filter": 10,
            "dwg_render_quality": "fast",
        }
        config = ProjectConfig.from_dict(data)

        assert config.metadata.name == "Loaded"
        assert config.comparison.sensitivity.position_threshold == 2.5
        assert config.color.show_deleted is False
        assert config.sensitivity_preset == "strict"
        assert config.top_n_filter == 10
        assert config.dwg_render_quality == "fast"

    def test_apply_preset(self):
        """프리셋 적용 테스트"""
        config = ProjectConfig.create_new()
        assert config.comparison.sensitivity.position_threshold == 1.0

        config.apply_preset(SensitivityPreset.STRICT)

        assert config.sensitivity_preset == "strict"
        assert config.comparison.sensitivity.position_threshold == 0.1

    def test_set_files(self):
        """파일 경로 설정 테스트"""
        config = ProjectConfig.create_new()
        config.set_files("/old/path.dxf", "/new/path.dxf")

        assert config.metadata.old_file_path == "/old/path.dxf"
        assert config.metadata.new_file_path == "/new/path.dxf"

    def test_get_summary(self):
        """요약 정보 테스트"""
        config = ProjectConfig.create_new(name="Summary Test")
        config.top_n_filter = 5
        summary = config.get_summary()

        assert summary["name"] == "Summary Test"
        assert summary["preset"] == "normal"
        assert summary["top_n_filter"] == 5
        assert summary["dwg_render_quality"] == "auto"
        assert "position_threshold" in summary


class TestProjectConfigSaveLoad:
    """ProjectConfig 저장/로드 테스트"""

    def test_save_and_load_json(self):
        """JSON 저장/로드 테스트"""
        config = ProjectConfig.create_new(
            name="JSON Test",
            preset=SensitivityPreset.STRICT,
        )
        config.color.show_deleted = False
        config.top_n_filter = 20
        config.dwg_render_quality = "high"

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            config.save(path)

            assert path.exists()

            loaded = ProjectConfig.load(path)

            assert loaded.metadata.name == "JSON Test"
            assert loaded.sensitivity_preset == "strict"
            assert loaded.color.show_deleted is False
            assert loaded.top_n_filter == 20
            assert loaded.dwg_render_quality == "high"
            assert loaded.comparison.sensitivity.position_threshold == 0.1

    def test_save_and_load_yaml(self):
        """YAML 저장/로드 테스트"""
        config = ProjectConfig.create_new(name="YAML Test")

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.yaml"
            config.save(path)

            assert path.exists()

            loaded = ProjectConfig.load(path)
            assert loaded.metadata.name == "YAML Test"

    def test_save_creates_backup(self):
        """백업 파일 생성 테스트"""
        config = ProjectConfig.create_new(name="Original")

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"

            # 첫 저장
            config.save(path)

            # 수정 후 두 번째 저장
            config.metadata.name = "Modified"
            config.save(path)

            backup_path = path.with_suffix(".json.bak")
            assert backup_path.exists()

            # 백업 파일에 원본 데이터 확인
            with open(backup_path, "r", encoding="utf-8") as f:
                backup_data = json.load(f)
            assert backup_data["metadata"]["name"] == "Original"

    def test_load_nonexistent_file(self):
        """존재하지 않는 파일 로드 테스트"""
        with pytest.raises(FileNotFoundError):
            ProjectConfig.load("/nonexistent/path.json")

    def test_load_invalid_json(self):
        """잘못된 JSON 로드 테스트"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "invalid.json"
            path.write_text("{ invalid json }", encoding="utf-8")

            with pytest.raises(ValueError):
                ProjectConfig.load(path)

    def test_load_or_create_existing(self):
        """load_or_create - 기존 파일 테스트"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "existing.json"

            # 파일 생성
            original = ProjectConfig.create_new(name="Existing")
            original.save(path)

            # load_or_create
            loaded = ProjectConfig.load_or_create(path)
            assert loaded.metadata.name == "Existing"

    def test_load_or_create_new(self):
        """load_or_create - 새 파일 테스트"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "new.json"

            config = ProjectConfig.load_or_create(path, name="New Project")
            assert config.metadata.name == "New Project"

    def test_save_format_auto_detect(self):
        """자동 형식 감지 테스트"""
        config = ProjectConfig.create_new()

        with tempfile.TemporaryDirectory() as tmpdir:
            # .yaml 확장자
            yaml_path = Path(tmpdir) / "config.yaml"
            config.save(yaml_path)

            with open(yaml_path, "r", encoding="utf-8") as f:
                content = f.read()
            # YAML은 중괄호 없이 시작
            assert not content.strip().startswith("{")

            # .json 확장자
            json_path = Path(tmpdir) / "config.json"
            config.save(json_path)

            with open(json_path, "r", encoding="utf-8") as f:
                content = f.read()
            # JSON은 중괄호로 시작
            assert content.strip().startswith("{")


class TestRecentProject:
    """RecentProject 테스트"""

    def test_default_values(self):
        """기본값 테스트"""
        project = RecentProject(path="/path/to/file", name="Test")

        assert project.path == "/path/to/file"
        assert project.name == "Test"
        assert project.preset == "normal"
        assert project.last_opened != ""

    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        project = RecentProject(
            path="/path/to/file",
            name="Test",
            preset="strict",
        )
        data = project.to_dict()

        assert data["path"] == "/path/to/file"
        assert data["name"] == "Test"
        assert data["preset"] == "strict"

    def test_from_dict(self):
        """딕셔너리에서 생성 테스트"""
        data = {
            "path": "/loaded/path",
            "name": "Loaded",
            "preset": "relaxed",
        }
        project = RecentProject.from_dict(data)

        assert project.path == "/loaded/path"
        assert project.name == "Loaded"
        assert project.preset == "relaxed"

    def test_touch(self):
        """touch() 시간 업데이트 테스트"""
        project = RecentProject(path="/path", name="Test")
        original_time = project.last_opened

        import time
        time.sleep(0.01)
        project.touch()

        assert project.last_opened != original_time


class TestRecentProjectsManager:
    """RecentProjectsManager 테스트"""

    def test_add_project(self):
        """프로젝트 추가 테스트"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RecentProjectsManager(config_dir=Path(tmpdir))

            manager.add_project("/path/1", "Project 1")
            manager.add_project("/path/2", "Project 2")

            projects = manager.get_recent_projects()
            assert len(projects) == 2
            # 최근 것이 먼저
            assert projects[0].name == "Project 2"
            assert projects[1].name == "Project 1"

    def test_add_duplicate_moves_to_top(self):
        """중복 추가 시 맨 위로 이동 테스트"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RecentProjectsManager(config_dir=Path(tmpdir))

            manager.add_project("/path/1", "Project 1")
            manager.add_project("/path/2", "Project 2")
            manager.add_project("/path/1", "Project 1 Updated")

            projects = manager.get_recent_projects()
            assert len(projects) == 2
            assert projects[0].name == "Project 1 Updated"
            assert projects[0].path.endswith("1")

    def test_remove_project(self):
        """프로젝트 제거 테스트"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RecentProjectsManager(config_dir=Path(tmpdir))

            manager.add_project("/path/1", "Project 1")
            manager.add_project("/path/2", "Project 2")

            result = manager.remove_project("/path/1")
            assert result is True

            projects = manager.get_recent_projects()
            assert len(projects) == 1
            assert projects[0].name == "Project 2"

    def test_remove_nonexistent(self):
        """존재하지 않는 프로젝트 제거 테스트"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RecentProjectsManager(config_dir=Path(tmpdir))

            result = manager.remove_project("/nonexistent")
            assert result is False

    def test_max_recent_projects(self):
        """최대 개수 제한 테스트"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RecentProjectsManager(config_dir=Path(tmpdir))

            # 15개 추가 (최대 10개)
            for i in range(15):
                manager.add_project(f"/path/{i}", f"Project {i}")

            projects = manager.get_recent_projects()
            assert len(projects) == 10
            # 최근 것부터 (14, 13, 12, ...)
            assert projects[0].name == "Project 14"

    def test_persistence(self):
        """영속성 테스트"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)

            # 첫 번째 매니저에서 추가
            manager1 = RecentProjectsManager(config_dir=config_dir)
            manager1.add_project("/path/1", "Project 1")

            # 두 번째 매니저에서 로드
            manager2 = RecentProjectsManager(config_dir=config_dir)
            projects = manager2.get_recent_projects()

            assert len(projects) == 1
            assert projects[0].name == "Project 1"

    def test_clear(self):
        """모두 삭제 테스트"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RecentProjectsManager(config_dir=Path(tmpdir))

            manager.add_project("/path/1", "Project 1")
            manager.add_project("/path/2", "Project 2")
            manager.clear()

            projects = manager.get_recent_projects()
            assert len(projects) == 0

    def test_get_project_by_path(self):
        """경로로 프로젝트 조회 테스트"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RecentProjectsManager(config_dir=Path(tmpdir))

            manager.add_project("/path/1", "Project 1", preset="strict")

            project = manager.get_project_by_path("/path/1")
            assert project is not None
            assert project.name == "Project 1"
            assert project.preset == "strict"

            # 존재하지 않는 경로
            assert manager.get_project_by_path("/nonexistent") is None

    def test_update_last_opened(self):
        """마지막 열람 시간 업데이트 테스트"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RecentProjectsManager(config_dir=Path(tmpdir))

            manager.add_project("/path/1", "Project 1")
            manager.add_project("/path/2", "Project 2")

            # Project 1 열람 (맨 위로 이동)
            manager.update_last_opened("/path/1")

            projects = manager.get_recent_projects()
            assert projects[0].path.endswith("1")


class TestHelperFunctions:
    """헬퍼 함수 테스트"""

    def test_save_project_config(self):
        """save_project_config 함수 테스트"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()

            config = ProjectConfig.create_new(name="Helper Test")
            file_path = Path(tmpdir) / "project.json"

            # 최근 프로젝트에 추가하지 않음
            save_project_config(config, file_path, add_to_recent=False)

            assert file_path.exists()

    def test_load_project_config(self):
        """load_project_config 함수 테스트"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = ProjectConfig.create_new(name="Load Test")
            file_path = Path(tmpdir) / "project.json"
            config.save(file_path)

            loaded = load_project_config(file_path, update_recent=False)
            assert loaded.metadata.name == "Load Test"


class TestProjectConfigIntegration:
    """통합 테스트"""

    def test_full_workflow(self):
        """전체 워크플로우 테스트"""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            # 1. 새 프로젝트 생성
            config = ProjectConfig.create_new(
                name="Integration Test",
                description="Full workflow test",
                preset=SensitivityPreset.STRICT,
            )

            # 2. 설정 수정
            config.color.show_deleted = False
            config.top_n_filter = 50
            config.set_files(
                "/old/drawing.dxf",
                "/new/drawing.dxf",
            )

            # 3. 저장
            config_path = project_dir / "test_project.json"
            config.save(config_path)

            # 4. 로드
            loaded = ProjectConfig.load(config_path)

            # 5. 검증
            assert loaded.metadata.name == "Integration Test"
            assert loaded.metadata.description == "Full workflow test"
            assert loaded.sensitivity_preset == "strict"
            assert loaded.comparison.sensitivity.position_threshold == 0.1
            assert loaded.color.show_deleted is False
            assert loaded.top_n_filter == 50
            assert loaded.metadata.old_file_path == "/old/drawing.dxf"
            assert loaded.metadata.new_file_path == "/new/drawing.dxf"

    def test_preset_change_and_save(self):
        """프리셋 변경 후 저장 테스트"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "preset_test.json"

            # 기본 프로젝트 생성
            config = ProjectConfig.create_new(name="Preset Test")
            assert config.comparison.sensitivity.position_threshold == 1.0

            # 엄격 모드로 변경
            config.apply_preset(SensitivityPreset.STRICT)
            config.save(config_path)

            # 로드 후 확인
            loaded = ProjectConfig.load(config_path)
            assert loaded.sensitivity_preset == "strict"
            assert loaded.comparison.sensitivity.position_threshold == 0.1

            # 완화 모드로 변경
            loaded.apply_preset(SensitivityPreset.RELAXED)
            loaded.save(config_path)

            # 다시 로드
            reloaded = ProjectConfig.load(config_path)
            assert reloaded.sensitivity_preset == "relaxed"
            assert reloaded.comparison.sensitivity.position_threshold == 5.0

    def test_color_config_persistence(self):
        """색상 설정 영속성 테스트"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "color_test.json"

            # 색상 설정 수정
            config = ProjectConfig.create_new(name="Color Test")
            config.color = ColorConfig.get_colorblind_friendly()
            config.color.show_modified = False
            config.save(config_path)

            # 로드 후 확인
            loaded = ProjectConfig.load(config_path)
            assert loaded.color.added_color == (0, 114, 178)  # 파란색
            assert loaded.color.show_modified is False
