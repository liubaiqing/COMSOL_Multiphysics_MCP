"""Basic tests for COMSOL MCP Server."""

import pytest
from pathlib import Path
import struct
import threading


class TestVersioning:
    """Tests for version naming utilities."""
    
    def test_generate_version_name(self):
        from src.utils.versioning import generate_version_name
        
        result = generate_version_name("model.mph")
        assert result.startswith("model_")
        assert result.endswith(".mph")
        assert len(result) > len("model.mph")
    
    def test_generate_version_name_no_extension(self):
        from src.utils.versioning import generate_version_name
        
        result = generate_version_name("model")
        assert result.startswith("model_")
        assert result.endswith(".mph")
    
    def test_generate_version_path(self):
        from src.utils.versioning import generate_version_path
        
        result = generate_version_path("/path/to/model.mph")
        path = Path(result)
        assert path.parent.name == "model"
        assert path.name.startswith("model_")
        assert result.endswith(".mph")
    
    def test_parse_version_info_valid(self):
        from src.utils.versioning import parse_version_info
        
        result = parse_version_info("model_20260215_143022.mph")
        assert result is not None
        assert result["base_name"] == "model"
        assert result["timestamp"] == "20260215_143022"
    
    def test_parse_version_info_invalid(self):
        from src.utils.versioning import parse_version_info
        
        result = parse_version_info("model.mph")
        assert result is None
        
        result = parse_version_info("model_20260215.mph")
        assert result is None


class TestSessionManager:
    """Tests for session manager (without actual COMSOL)."""
    
    def test_session_manager_singleton(self):
        from src.tools.session import SessionManager
        
        sm1 = SessionManager()
        sm2 = SessionManager()
        assert sm1 is sm2
    
    def test_session_manager_initial_state(self):
        from src.tools.session import SessionManager
        
        sm = SessionManager()
        assert sm.client is None
        assert not sm.is_connected
        assert sm.current_model is None
        assert sm.models == {}
    
    def test_get_status_disconnected(self):
        from src.tools.session import SessionManager
        
        sm = SessionManager()
        status = sm.get_status()
        assert status["connected"] is False

    def test_start_async_connects_in_background(self, monkeypatch):
        import src.tools.session as session_module
        from src.tools.session import SessionManager

        reset_session_manager(session_module)
        monkeypatch.setattr(session_module.mph, "Client", FakeClient)

        sm = SessionManager()
        result = sm.start_async(cores=2, version="6.3")
        assert result["success"] is True
        assert result["status"] == "starting"

        sm._startup_thread.join(timeout=2)
        status = sm.get_status()
        assert status["connected"] is True
        assert status["version"] == "6.3"
        assert status["cores"] == 2
        assert status["startup"]["status"] == "connected"

        reset_session_manager(session_module)

    def test_start_async_reuses_in_progress_startup(self, monkeypatch):
        import src.tools.session as session_module
        from src.tools.session import SessionManager

        reset_session_manager(session_module)
        ready = threading.Event()
        release = threading.Event()

        class BlockingClient(FakeClient):
            def __init__(self, cores=None, version=None):
                ready.set()
                release.wait(timeout=2)
                super().__init__(cores=cores, version=version)

        monkeypatch.setattr(session_module.mph, "Client", BlockingClient)

        sm = SessionManager()
        first = sm.start_async()
        assert ready.wait(timeout=2)
        second = sm.start_async()

        assert first["success"] is True
        assert second["success"] is True
        assert second["status"] == "starting"
        release.set()
        sm._startup_thread.join(timeout=2)

        reset_session_manager(session_module)

    def test_start_async_reports_failure(self, monkeypatch):
        import src.tools.session as session_module
        from src.tools.session import SessionManager

        reset_session_manager(session_module)

        class FailingClient:
            def __init__(self, cores=None, version=None):
                raise RuntimeError("COMSOL failed")

        monkeypatch.setattr(session_module.mph, "Client", FailingClient)

        sm = SessionManager()
        result = sm.start_async()
        assert result["success"] is True

        sm._startup_thread.join(timeout=2)
        status = sm.get_status()
        assert status["connected"] is False
        assert status["startup"]["status"] == "failed"
        assert "COMSOL failed" in status["startup"]["error"]

        reset_session_manager(session_module)


class TestMaterials:
    """Tests for material helper logic without COMSOL."""

    def test_create_material_sets_properties_and_selection(self):
        from src.tools.materials import _create_material

        model = FakeModel()
        result = _create_material(
            model=model,
            material_name="Aluminum",
            properties={
                "density": "2700[kg/m^3]",
                "youngsmodulus": "70e9[Pa]",
            },
            component_name="comp1",
            domain_selection=[1, 2],
        )

        material = model.java.components["comp1"].materials.created["mat_aluminum"]
        assert result["success"] is True
        assert result["material"]["tag"] == "mat_aluminum"
        assert result["material"]["label"] == "Aluminum"
        assert material.label_value == "Aluminum"
        assert material.groups["def"].properties == {
            "density": "2700[kg/m^3]",
            "youngsmodulus": "70e9[Pa]",
        }
        assert material.selection_value == [1, 2]

    def test_create_material_requires_properties(self):
        from src.tools.materials import _create_material

        result = _create_material(
            model=FakeModel(),
            material_name="Aluminum",
            properties={},
        )

        assert result["success"] is False
        assert "properties" in result["error"]

    def test_create_material_missing_component(self):
        from src.tools.materials import _create_material

        result = _create_material(
            model=FakeModel(),
            material_name="Aluminum",
            properties={"density": "2700[kg/m^3]"},
            component_name="missing",
        )

        assert result["success"] is False
        assert "Component 'missing' not found" in result["error"]

    def test_create_material_rejects_invalid_domain_selection_before_create(self):
        from src.tools.materials import _create_material

        model = FakeModel()
        result = _create_material(
            model=model,
            material_name="Aluminum",
            properties={"density": "2700[kg/m^3]"},
            domain_selection=["not-a-domain"],
        )

        assert result["success"] is False
        assert "Invalid domain_selection" in result["error"]
        assert model.java.components["comp1"].materials.created == {}


class TestSTLAnalysis:
    """Tests for dependency-light STL diagnostics."""

    def test_analyze_binary_stl_reports_closed_tetrahedron(self, tmp_path):
        from src.utils.stl import analyze_binary_stl

        stl_path = tmp_path / "tetra.stl"
        triangles = [
            ((0, 0, -1), (0, 0, 0), (1, 0, 0), (0, 1, 0)),
            ((0, -1, 0), (0, 0, 0), (0, 0, 1), (1, 0, 0)),
            ((-1, 0, 0), (0, 0, 0), (0, 1, 0), (0, 0, 1)),
            ((1, 1, 1), (1, 0, 0), (0, 0, 1), (0, 1, 0)),
        ]
        with stl_path.open("wb") as handle:
            handle.write(b"test tetra".ljust(80, b"\0"))
            handle.write(struct.pack("<I", len(triangles)))
            for normal, v1, v2, v3 in triangles:
                handle.write(struct.pack("<12fH", *normal, *v1, *v2, *v3, 0))

        result = analyze_binary_stl(stl_path)

        assert result["success"] is True
        assert result["triangle_count"] == 4
        assert result["unique_vertices"] == 4
        assert result["boundary_edges"] == 0
        assert result["nonmanifold_edges"] == 0
        assert result["is_edge_manifold"] is True
        assert result["bounding_box"]["dimensions"] == [1.0, 1.0, 1.0]

    def test_analyze_binary_stl_rejects_ascii_or_bad_size(self, tmp_path):
        from src.utils.stl import analyze_binary_stl

        stl_path = tmp_path / "bad.stl"
        stl_path.write_text("solid bad\nendsolid bad\n")

        result = analyze_binary_stl(stl_path)

        assert result["success"] is False
        assert "binary STL" in result["error"] or "too small" in result["error"]


class FakePropertyGroup:
    def __init__(self):
        self.properties = {}

    def set(self, key, value):
        self.properties[key] = value


class FakeSelection:
    def __init__(self, material):
        self.material = material

    def set(self, domains):
        self.material.selection_value = domains


class FakeMaterial:
    def __init__(self, tag):
        self.tag = tag
        self.label_value = None
        self.selection_value = None
        self.groups = {}

    def label(self, value):
        self.label_value = value

    def propertyGroup(self, name):
        if name not in self.groups:
            self.groups[name] = FakePropertyGroup()
        return self.groups[name]

    def selection(self):
        return FakeSelection(self)


class FakeMaterialCollection:
    def __init__(self):
        self.created = {}

    def create(self, tag, material_type):
        material = FakeMaterial(tag)
        material.material_type = material_type
        self.created[tag] = material
        return material


class FakeComponent:
    def __init__(self):
        self.materials = FakeMaterialCollection()

    def material(self):
        return self.materials


class FakeJavaModel:
    def __init__(self):
        self.components = {"comp1": FakeComponent()}

    def component(self, name):
        return self.components.get(name)


class FakeModel:
    def __init__(self):
        self.java = FakeJavaModel()


class FakeClient:
    def __init__(self, cores=None, version=None):
        self.version = version or "6.3"
        self.cores = cores
        self.standalone = True
        self.cleared = False

    def clear(self):
        self.cleared = True

    def names(self):
        return []


def reset_session_manager(session_module):
    sm = session_module.SessionManager()
    sm._client = None
    sm._models.clear()
    sm._current_model = None
    sm._startup_thread = None
    sm._startup_status = None
