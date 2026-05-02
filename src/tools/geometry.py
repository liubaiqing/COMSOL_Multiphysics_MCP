"""Geometry tools for COMSOL MCP Server."""

from pathlib import Path
from typing import Optional, Sequence
from mcp.server.fastmcp import FastMCP

from .session import session_manager
from ..utils.stl import analyze_binary_stl


def _get_geometry_node(model, geometry_name: Optional[str], component_name: str = "comp1"):
    """Helper to get geometry node via Java API.
    
    Returns:
        tuple: (geom_node, error_message) - geom_node is None if error
    """
    jm = model.java
    
    try:
        comp = jm.component(component_name)
        if comp is None:
            return None, f"Component '{component_name}' not found."
        
        if geometry_name:
            geom = comp.geom(geometry_name)
            if geom is None:
                return None, f"Geometry '{geometry_name}' not found in component '{component_name}'."
        else:
            geoms = list(comp.geom())
            if not geoms:
                return None, "No geometry sequences found. Create one first with geometry_create."
            geom = geoms[0]
        
        return geom, None
    except Exception as e:
        return None, f"Failed to get geometry: {str(e)}"


def _next_tag(existing: Sequence[str], prefix: str) -> str:
    """Return the first unused COMSOL tag for a model entity list."""
    used = set(existing)
    index = 1
    while f"{prefix}{index}" in used:
        index += 1
    return f"{prefix}{index}"


def _import_file_as_geometry(
    model,
    file_path: str,
    geometry_name: Optional[str],
    component_name: str,
    feature_name: Optional[str],
    build: bool,
) -> dict:
    """Import a CAD/STL file as a geometry feature via COMSOL's Java API."""
    path = Path(file_path)
    if not path.exists():
        return {"success": False, "error": f"File not found: {file_path}"}

    geom, error = _get_geometry_node(model, geometry_name, component_name)
    if error:
        return {"success": False, "error": error}

    existing = list(geom.feature().tags())
    feat_name = feature_name or _next_tag(existing, "imp")

    try:
        import_feature = geom.feature().create(feat_name, "Import")
        import_feature.set("filename", str(path.absolute()))

        if path.suffix.lower() == ".stl":
            try:
                import_feature.set("repair", "on")
            except Exception:
                pass

        if build:
            geom.run()

        return {
            "success": True,
            "feature": {
                "name": feat_name,
                "type": "Import",
                "geometry": geometry_name or geom.tag(),
                "component": component_name,
                "file": str(path.absolute()),
                "mode": "geometry",
                "built": build,
            },
        }
    except Exception as exc:
        try:
            if feat_name in list(geom.feature().tags()):
                geom.feature().remove(feat_name)
        except Exception:
            pass
        return {"success": False, "error": f"Failed to import geometry: {exc}"}


def _import_file_as_mesh(
    model,
    file_path: str,
    component_name: str,
    mesh_name: Optional[str] = None,
    feature_name: Optional[str] = None,
) -> dict:
    """Import a mesh file into a COMSOL mesh sequence."""
    path = Path(file_path)
    if not path.exists():
        return {"success": False, "error": f"File not found: {file_path}"}

    try:
        comp = model.java.component(component_name)
        if comp is None:
            return {"success": False, "error": f"Component '{component_name}' not found."}

        existing_meshes = list(comp.mesh().tags())
        target_mesh = mesh_name or _next_tag(existing_meshes, "mesh")
        mesh = comp.mesh(target_mesh) if target_mesh in existing_meshes else comp.mesh().create(target_mesh)

        existing_features = list(mesh.feature().tags())
        import_tag = feature_name or _next_tag(existing_features, "imp")
        import_feature = mesh.feature().create(import_tag, "Import")
        import_feature.set("filename", str(path.absolute()))
        mesh.run()

        analysis = None
        if path.suffix.lower() == ".stl":
            analysis = analyze_binary_stl(path)

        return {
            "success": True,
            "mesh": {
                "name": target_mesh,
                "feature": import_tag,
                "file": str(path.absolute()),
                "mode": "mesh",
            },
            "stl_analysis": analysis,
        }
    except Exception as exc:
        return {"success": False, "error": f"Failed to import mesh: {exc}"}


def register_geometry_tools(mcp: FastMCP) -> None:
    """Register geometry tools with the MCP server."""
    
    @mcp.tool()
    def geometry_list(model_name: Optional[str] = None) -> dict:
        """
        List all geometry sequences in a model.
        
        Args:
            model_name: Model name (default: current model)
        
        Returns:
            List of geometry sequence names
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            geometries = model.geometries()
            return {
                "success": True,
                "geometries": geometries,
                "count": len(geometries),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to list geometries: {str(e)}"}
    
    @mcp.tool()
    def geometry_create(
        geometry_name: Optional[str] = None,
        space_dimension: int = 3,
        component_name: str = "comp1",
        model_name: Optional[str] = None
    ) -> dict:
        """
        Create a new geometry sequence in the model's component.
        
        IMPORTANT: A component must exist first. Use model_create_component if needed.
        
        Args:
            geometry_name: Name for the geometry sequence (default: 'geom1')
            space_dimension: Space dimension - 2 for 2D, 3 for 3D (default: 3)
            component_name: Component name (default: 'comp1')
            model_name: Model name (default: current model)
        
        Returns:
            Created geometry info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            jm = model.java
            
            geom_name = geometry_name or "geom1"
            
            comp = jm.component(component_name)
            if comp is None:
                return {
                    "success": False,
                    "error": f"Component '{component_name}' not found. Create it first with model_create_component."
                }
            
            geom = comp.geom().create(geom_name, space_dimension)
            
            return {
                "success": True,
                "geometry": geom_name,
                "component": component_name,
                "space_dimension": space_dimension,
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to create geometry: {str(e)}"}
    
    @mcp.tool()
    def geometry_add_feature(
        feature_type: str,
        geometry_name: Optional[str] = None,
        feature_name: Optional[str] = None,
        model_name: Optional[str] = None,
        **kwargs
    ) -> dict:
        """
        Add a geometry feature to a geometry sequence.
        
        Common feature types:
        - Block: Rectangular block (3D)
        - Cylinder: Cylinder (3D)
        - Sphere: Sphere (3D)
        - Cone: Cone (3D)
        - WorkPlane: Working plane for 2D geometry
        - Rectangle: Rectangle (2D)
        - Circle: Circle (2D)
        - Polygon: Polygon from points
        - Import: Import CAD geometry
        - Union, Intersection, Difference: Boolean operations
        
        Args:
            feature_type: Type of geometry feature (Block, Cylinder, etc.)
            geometry_name: Geometry sequence name (default: first geometry)
            feature_name: Name for the feature (auto-generated if None)
            model_name: Model name (default: current model)
            **kwargs: Feature-specific properties (position, size, etc.)
        
        Returns:
            Created feature info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            geometries = model.geometries()
            if not geometries:
                return {"success": False, "error": "No geometry sequences found. Create one first."}
            
            target_geom = geometry_name or geometries[0]
            if target_geom not in geometries:
                return {"success": False, "error": f"Geometry not found: {target_geom}"}
            
            geom_node = model / "geometries" / target_geom
            feature_node = geom_node.create(feature_type, feature_name)
            
            for prop_name, prop_value in kwargs.items():
                try:
                    feature_node.property(prop_name, prop_value)
                except Exception:
                    pass
            
            return {
                "success": True,
                "feature": {
                    "name": feature_node.name() if hasattr(feature_node, 'name') else feature_name,
                    "type": feature_type,
                    "geometry": target_geom,
                }
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to add geometry feature: {str(e)}"}
    
    @mcp.tool()
    def geometry_add_block(
        position: Sequence[float] = (0, 0, 0),
        size: Sequence[float] = (1, 1, 1),
        geometry_name: Optional[str] = None,
        component_name: str = "comp1",
        feature_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add a block (rectangular cuboid) to the geometry.
        
        Args:
            position: Base position [x, y, z] in meters (default: origin)
            size: Dimensions [width, depth, height] in meters (default: 1m cube)
            geometry_name: Geometry sequence name (default: first geometry)
            component_name: Component name (default: 'comp1')
            feature_name: Feature name (auto-generated if None)
            model_name: Model name (default: current model)
        
        Returns:
            Created block info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            geom, error = _get_geometry_node(model, geometry_name, component_name)
            if error:
                return {"success": False, "error": error}
            
            feat_name = feature_name or f"blk{len(geom.feature())+1}"
            block = geom.feature().create(feat_name, "Block")
            
            block.set("pos", [str(p) for p in position])
            block.set("size", [str(s) for s in size])
            
            return {
                "success": True,
                "feature": {
                    "name": feat_name,
                    "type": "Block",
                    "position": list(position),
                    "size": list(size),
                }
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to add block: {str(e)}"}
    
    @mcp.tool()
    def geometry_add_cylinder(
        position: Sequence[float] = (0, 0, 0),
        radius: float = 0.5,
        height: float = 1.0,
        geometry_name: Optional[str] = None,
        component_name: str = "comp1",
        feature_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add a cylinder to the geometry.
        
        Args:
            position: Center of base [x, y, z] in meters
            radius: Radius in meters (default: 0.5)
            height: Height in meters (default: 1.0)
            geometry_name: Geometry sequence name (default: first geometry)
            component_name: Component name (default: 'comp1')
            feature_name: Feature name (auto-generated if None)
            model_name: Model name (default: current model)
        
        Returns:
            Created cylinder info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            geom, error = _get_geometry_node(model, geometry_name, component_name)
            if error:
                return {"success": False, "error": error}
            
            feat_name = feature_name or f"cyl{len(geom.feature())+1}"
            cyl = geom.feature().create(feat_name, "Cylinder")
            
            cyl.set("pos", [str(p) for p in position])
            cyl.set("r", str(radius))
            cyl.set("h", str(height))
            
            return {
                "success": True,
                "feature": {
                    "name": feat_name,
                    "type": "Cylinder",
                    "position": list(position),
                    "radius": radius,
                    "height": height,
                }
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to add cylinder: {str(e)}"}
    
    @mcp.tool()
    def geometry_add_sphere(
        position: Sequence[float] = (0, 0, 0),
        radius: float = 0.5,
        geometry_name: Optional[str] = None,
        component_name: str = "comp1",
        feature_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add a sphere to the geometry.
        
        Args:
            position: Center [x, y, z] in meters
            radius: Radius in meters (default: 0.5)
            geometry_name: Geometry sequence name (default: first geometry)
            component_name: Component name (default: 'comp1')
            feature_name: Feature name (auto-generated if None)
            model_name: Model name (default: current model)
        
        Returns:
            Created sphere info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            geom, error = _get_geometry_node(model, geometry_name, component_name)
            if error:
                return {"success": False, "error": error}
            
            feat_name = feature_name or f"sph{len(geom.feature())+1}"
            sphere = geom.feature().create(feat_name, "Sphere")
            
            sphere.set("pos", [str(p) for p in position])
            sphere.set("r", str(radius))
            
            return {
                "success": True,
                "feature": {
                    "name": feat_name,
                    "type": "Sphere",
                    "position": list(position),
                    "radius": radius,
                }
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to add sphere: {str(e)}"}
    
    @mcp.tool()
    def geometry_add_rectangle(
        position: Sequence[float] = (0, 0),
        size: Sequence[float] = (1, 1),
        geometry_name: Optional[str] = None,
        component_name: str = "comp1",
        feature_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add a rectangle to a 2D geometry or work plane.
        
        Args:
            position: Base position [x, y] in meters
            size: Dimensions [width, height] in meters
            geometry_name: Geometry sequence name (default: first geometry)
            component_name: Component name (default: 'comp1')
            feature_name: Feature name (auto-generated if None)
            model_name: Model name (default: current model)
        
        Returns:
            Created rectangle info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            geom, error = _get_geometry_node(model, geometry_name, component_name)
            if error:
                return {"success": False, "error": error}
            
            feat_name = feature_name or f"r{len(geom.feature())+1}"
            rect = geom.feature().create(feat_name, "Rectangle")
            
            rect.set("pos", [str(p) for p in position])
            rect.set("size", [str(s) for s in size])
            
            return {
                "success": True,
                "feature": {
                    "name": feat_name,
                    "type": "Rectangle",
                    "position": list(position),
                    "size": list(size),
                }
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to add rectangle: {str(e)}"}
    
    @mcp.tool()
    def geometry_add_circle(
        position: Sequence[float] = (0, 0),
        radius: float = 0.5,
        geometry_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add a circle to a 2D geometry or work plane.
        
        Args:
            position: Center [x, y] in meters
            radius: Radius in meters (default: 0.5)
            geometry_name: Geometry sequence name
            model_name: Model name (default: current model)
        
        Returns:
            Created circle info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            geometries = model.geometries()
            if not geometries:
                return {"success": False, "error": "No geometry sequences found."}
            
            target_geom = geometry_name or geometries[0]
            geom_node = model / "geometries" / target_geom
            circle_node = geom_node.create("Circle")
            
            if len(position) == 2:
                circle_node.property("pos", list(position))
            circle_node.property("r", radius)
            
            return {
                "success": True,
                "feature": {
                    "name": circle_node.name() if hasattr(circle_node, 'name') else "Circle",
                    "type": "Circle",
                    "geometry": target_geom,
                    "position": list(position),
                    "radius": radius,
                }
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to add circle: {str(e)}"}
    
    @mcp.tool()
    def geometry_boolean_union(
        input_objects: Sequence[str],
        geometry_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Create a boolean union of geometry objects.
        
        Args:
            input_objects: Names of objects to unite
            geometry_name: Geometry sequence name
            model_name: Model name (default: current model)
        
        Returns:
            Created union operation info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            geometries = model.geometries()
            if not geometries:
                return {"success": False, "error": "No geometry sequences found."}
            
            target_geom = geometry_name or geometries[0]
            geom_node = model / "geometries" / target_geom
            union_node = geom_node.create("Union")
            union_node.property("input", list(input_objects))
            
            return {
                "success": True,
                "feature": {
                    "name": union_node.name() if hasattr(union_node, 'name') else "Union",
                    "type": "Union",
                    "geometry": target_geom,
                    "input_objects": list(input_objects),
                }
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to create union: {str(e)}"}
    
    @mcp.tool()
    def geometry_boolean_difference(
        input_object: str,
        objects_to_subtract: Sequence[str],
        geometry_name: Optional[str] = None,
        component_name: str = "comp1",
        feature_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Create a boolean difference (subtract objects from another).
        
        Args:
            input_object: Object to subtract from (e.g., 'blk1')
            objects_to_subtract: Objects to remove (e.g., ['cyl1'])
            geometry_name: Geometry sequence name (default: first geometry)
            component_name: Component name (default: 'comp1')
            feature_name: Feature name (auto-generated if None)
            model_name: Model name (default: current model)
        
        Returns:
            Created difference operation info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            geom, error = _get_geometry_node(model, geometry_name, component_name)
            if error:
                return {"success": False, "error": error}
            
            feat_name = feature_name or f"dif{len(geom.feature())+1}"
            diff = geom.feature().create(feat_name, "Difference")
            
            diff.selection("input").set([input_object])
            diff.selection("input2").set(list(objects_to_subtract))
            
            return {
                "success": True,
                "feature": {
                    "name": feat_name,
                    "type": "Difference",
                    "input_object": input_object,
                    "subtracted": list(objects_to_subtract),
                }
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to create difference: {str(e)}"}
    
    @mcp.tool()
    def geometry_import(
        file_path: str,
        geometry_name: Optional[str] = None,
        import_type: str = "CAD",
        model_name: Optional[str] = None,
        component_name: str = "comp1",
        feature_name: Optional[str] = None,
        build: bool = True,
        fallback_to_mesh: bool = True,
    ) -> dict:
        """
        Import geometry from a CAD file.
        
        Supported formats: STEP, IGES, STL, NASTRAN, etc.
        
        Args:
            file_path: Path to the CAD file
            geometry_name: Geometry sequence name
            import_type: Import type (CAD, geometry, mesh, auto, etc.)
            model_name: Model name (default: current model)
            component_name: Component name (default: 'comp1')
            feature_name: Optional import feature tag
            build: Whether to build the geometry after import
            fallback_to_mesh: If geometry STL import fails, import as mesh
        
        Returns:
            Import operation info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        path = Path(file_path)
        mode = import_type.lower()

        if mode in {"mesh", "stl_mesh", "mesh_import"}:
            return _import_file_as_mesh(model, file_path, component_name, mesh_name=None, feature_name=feature_name)

        result = _import_file_as_geometry(
            model=model,
            file_path=file_path,
            geometry_name=geometry_name,
            component_name=component_name,
            feature_name=feature_name,
            build=build,
        )
        if result["success"]:
            if path.suffix.lower() == ".stl":
                result["stl_analysis"] = analyze_binary_stl(path)
            return result

        should_fallback = (
            fallback_to_mesh
            and path.suffix.lower() == ".stl"
            and mode in {"cad", "geometry", "auto", "stl"}
        )
        if not should_fallback:
            return result

        mesh_result = _import_file_as_mesh(
            model=model,
            file_path=file_path,
            component_name=component_name,
            mesh_name=None,
            feature_name=feature_name,
        )
        if mesh_result["success"]:
            mesh_result["warning"] = (
                "Geometry import failed, so the STL was imported as a mesh. "
                "This is typical for non-manifold or hard-to-repair STL files."
            )
            mesh_result["geometry_import_error"] = result["error"]
        return mesh_result

    @mcp.tool()
    def stl_analyze(file_path: str) -> dict:
        """
        Analyze a binary STL file before importing it into COMSOL.

        Reports triangle count, bounding box, surface area, signed volume,
        boundary edges, and non-manifold edges. Non-manifold STL files often
        need mesh import or external repair before Boolean/CFD workflows.
        """
        return analyze_binary_stl(file_path)
    
    @mcp.tool()
    def geometry_build(
        geometry_name: Optional[str] = None,
        component_name: str = "comp1",
        model_name: Optional[str] = None
    ) -> dict:
        """
        Build the geometry sequence to generate the actual geometry.
        
        This must be called after adding/modifying geometry features.
        
        Args:
            geometry_name: Geometry sequence name (default: build all)
            component_name: Component name (default: 'comp1')
            model_name: Model name (default: current model)
        
        Returns:
            Build status
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            geom, error = _get_geometry_node(model, geometry_name, component_name)
            if error:
                return {"success": False, "error": error}
            
            geom.run()
            
            return {
                "success": True,
                "geometry": geometry_name or "first",
                "message": "Geometry built successfully.",
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to build geometry: {str(e)}"}
    
    @mcp.tool()
    def geometry_list_features(
        geometry_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        List all features in a geometry sequence.
        
        Args:
            geometry_name: Geometry sequence name (default: first geometry)
            model_name: Model name (default: current model)
        
        Returns:
            List of geometry features with their types
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            geometries = model.geometries()
            if not geometries:
                return {"success": False, "error": "No geometry sequences found."}
            
            target_geom = geometry_name or geometries[0]
            if target_geom not in geometries:
                return {"success": False, "error": f"Geometry not found: {target_geom}"}
            
            geom_node = model / "geometries" / target_geom
            features = []
            
            for child in geom_node.children():
                feat_info = {"name": child.name()}
                try:
                    feat_info["type"] = child.type() if hasattr(child, 'type') else "unknown"
                except Exception:
                    pass
                features.append(feat_info)
            
            return {
                "success": True,
                "geometry": target_geom,
                "features": features,
                "count": len(features),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to list features: {str(e)}"}
