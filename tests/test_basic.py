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


class TestGeometryImportHelpers:
    """Tests for geometry import cleanup paths without COMSOL."""

    def test_failed_geometry_import_removes_created_feature(self, tmp_path):
        from src.tools.geometry import _import_file_as_geometry

        cad_file = tmp_path / "bad.stl"
        cad_file.write_bytes(b"not a real stl")
        model = FakeGeometryImportModel()

        result = _import_file_as_geometry(
            model=model,
            file_path=str(cad_file),
            geometry_name="geom1",
            component_name="comp1",
            feature_name="imp_test",
            build=True,
        )

        assert result["success"] is False
        assert "imp_test" not in model.java.components["comp1"].geometries["geom1"].features.created

    def test_mesh_import_creates_default_geometry_when_missing(self, tmp_path):
        from src.tools.geometry import _import_file_as_mesh

        mesh_file = tmp_path / "mesh.stl"
        write_binary_tetra_stl(mesh_file)
        model = FakeMeshImportModel()

        result = _import_file_as_mesh(
            model=model,
            file_path=str(mesh_file),
            component_name="comp1",
        )

        component = model.java.components["comp1"]
        import_feature = component.meshes.created["mesh1"].features.created["imp1"]
        assert result["success"] is True
        assert "geom1" in component.geometries.created
        assert "mesh1" in component.meshes.created
        assert import_feature.properties["createdom"] == "on"
        assert import_feature.properties["domelem"] == "on"
        assert import_feature.properties["selectionstl"] == "on"


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


class FakeImportFeature:
    def __init__(self, tag):
        self.tag = tag
        self.properties = {}

    def set(self, key, value):
        self.properties[key] = value


class FakeGeometryFeatureCollection:
    def __init__(self):
        self.created = {}

    def tags(self):
        return list(self.created)

    def create(self, tag, feature_type):
        feature = FakeImportFeature(tag)
        feature.feature_type = feature_type
        self.created[tag] = feature
        return feature

    def remove(self, tag):
        self.created.pop(tag, None)


class FakeGeometryForImport:
    def __init__(self):
        self.features = FakeGeometryFeatureCollection()

    def feature(self):
        return self.features

    def tag(self):
        return "geom1"

    def run(self):
        raise RuntimeError("build failed")


class FakeComponentForImport:
    def __init__(self):
        self.geometries = {"geom1": FakeGeometryForImport()}

    def geom(self, name):
        return self.geometries.get(name)


class FakeJavaModelForImport:
    def __init__(self):
        self.components = {"comp1": FakeComponentForImport()}

    def component(self, name):
        return self.components.get(name)


class FakeGeometryImportModel:
    def __init__(self):
        self.java = FakeJavaModelForImport()


def write_binary_tetra_stl(path):
    triangles = [
        ((0, 0, -1), (0, 0, 0), (1, 0, 0), (0, 1, 0)),
        ((0, -1, 0), (0, 0, 0), (0, 0, 1), (1, 0, 0)),
        ((-1, 0, 0), (0, 0, 0), (0, 1, 0), (0, 0, 1)),
        ((1, 1, 1), (1, 0, 0), (0, 0, 1), (0, 1, 0)),
    ]
    with path.open("wb") as handle:
        handle.write(b"test tetra".ljust(80, b"\0"))
        handle.write(struct.pack("<I", len(triangles)))
        for normal, v1, v2, v3 in triangles:
            handle.write(struct.pack("<12fH", *normal, *v1, *v2, *v3, 0))


class FakeGeometryCollectionForMesh:
    def __init__(self):
        self.created = {}

    def tags(self):
        return list(self.created)

    def create(self, tag, dimension):
        self.created[tag] = {"dimension": dimension}
        return self.created[tag]


class FakeMeshImportFeature:
    def __init__(self, tag):
        self.tag = tag
        self.properties = {}

    def set(self, key, value):
        self.properties[key] = value


class FakeMeshFeatureCollection:
    def __init__(self):
        self.created = {}

    def tags(self):
        return list(self.created)

    def create(self, tag, feature_type):
        feature = FakeMeshImportFeature(tag)
        feature.feature_type = feature_type
        self.created[tag] = feature
        return feature


class FakeMeshSequence:
    def __init__(self):
        self.features = FakeMeshFeatureCollection()
        self.ran = False

    def feature(self):
        return self.features

    def run(self):
        self.ran = True


class FakeMeshCollectionForImport:
    def __init__(self):
        self.created = {}

    def tags(self):
        return list(self.created)

    def create(self, tag):
        mesh = FakeMeshSequence()
        self.created[tag] = mesh
        return mesh

    def __call__(self, tag):
        return self.created.get(tag)


class FakeComponentForMeshImport:
    def __init__(self):
        self.geometries = FakeGeometryCollectionForMesh()
        self.meshes = FakeMeshCollectionForImport()

    def geom(self):
        return self.geometries

    def mesh(self, tag=None):
        if tag is None:
            return self.meshes
        return self.meshes(tag)


class FakeJavaModelForMeshImport:
    def __init__(self):
        self.components = {"comp1": FakeComponentForMeshImport()}

    def component(self, name):
        return self.components.get(name)


class FakeMeshImportModel:
    def __init__(self):
        self.java = FakeJavaModelForMeshImport()


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
