"""Mesh tools for COMSOL MCP Server."""

from pathlib import Path
from typing import Optional
from mcp.server.fastmcp import FastMCP

from .session import session_manager
from ..utils.stl import analyze_binary_stl


def _next_tag(existing, prefix: str) -> str:
    used = set(existing)
    index = 1
    while f"{prefix}{index}" in used:
        index += 1
    return f"{prefix}{index}"


def _reset_default_view(model, component_name: str, stl_analysis: Optional[dict] = None) -> dict:
    """Reset the component's default 3D view to a centered isometric view."""
    try:
        comp = model.java.component(component_name)
        if comp is None:
            return {"success": False, "error": f"Component '{component_name}' not found."}

        view_tags = list(comp.view().tags())
        view = comp.view("view1") if "view1" in view_tags else comp.view().create("view1", 3)
        camera = view.camera()

        center = [0.0, 0.0, 0.0]
        max_dim = 1.0
        if stl_analysis and stl_analysis.get("success"):
            bbox = stl_analysis.get("bounding_box", {})
            center = [float(v) for v in bbox.get("center", center)]
            dimensions = [abs(float(v)) for v in bbox.get("dimensions", [])]
            if dimensions:
                max_dim = max(max(dimensions), 1.0)

        position = [
            center[0] - 4.7 * max_dim,
            center[1] - 6.25 * max_dim,
            center[2] + 4.7 * max_dim,
        ]
        orthoscale = 2.5 * max_dim

        camera.set("projection", "perspective")
        camera.set("target", [str(v) for v in center])
        camera.set("rotationpoint", [str(v) for v in center])
        camera.set("position", [str(v) for v in position])
        camera.set("up", ["0.3086974", "0.4115966", "0.8574929"])
        camera.set("orthoscale", str(orthoscale))
        camera.set("viewscaletype", "none")
        camera.set("autocontext", "isotropic")
        camera.set("autoupdate", "off")

        try:
            axis = view.axis()
            axis.set("viewscaletype", "none")
            axis.set("autocontext", "isotropic")
            axis.set("autoupdate", "off")
        except Exception:
            pass

        return {
            "success": True,
            "view": view.tag(),
            "target": center,
            "position": position,
            "orthoscale": orthoscale,
        }
    except Exception as exc:
        return {"success": False, "error": f"Failed to reset view: {exc}"}


def register_mesh_tools(mcp: FastMCP) -> None:
    """Register mesh tools with the MCP server."""
    
    @mcp.tool()
    def mesh_list(model_name: Optional[str] = None) -> dict:
        """
        List all mesh sequences in a model.
        
        Args:
            model_name: Model name (default: current model)
        
        Returns:
            List of mesh sequence names
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            meshes = model.meshes()
            return {
                "success": True,
                "meshes": meshes,
                "count": len(meshes),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to list meshes: {str(e)}"}
    
    @mcp.tool()
    def mesh_create(
        mesh_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Run a mesh sequence to generate the mesh.
        
        This executes the meshing operations defined in the mesh sequence.
        
        Args:
            mesh_name: Mesh sequence name (default: run all mesh sequences)
            model_name: Model name (default: current model)
        
        Returns:
            Mesh generation status
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            model.mesh(mesh_name)
            return {
                "success": True,
                "mesh": mesh_name,
                "message": f"Mesh created: {mesh_name or 'all meshes'}",
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to create mesh: {str(e)}"}
    
    @mcp.tool()
    def mesh_info(
        mesh_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Get information about a mesh.
        
        Args:
            mesh_name: Mesh sequence name (default: first mesh)
            model_name: Model name (default: current model)
        
        Returns:
            Mesh statistics including element counts
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            meshes = model.meshes()
            if not meshes:
                return {"success": False, "error": "No meshes defined in model."}
            
            target = mesh_name or meshes[0]
            if target not in meshes:
                return {"success": False, "error": f"Mesh not found: {target}"}
            
            mesh_node = model / "meshes" / target
            
            info = {
                "name": target,
            }
            
            try:
                java_mesh = mesh_node.java
                if hasattr(java_mesh, 'getVertex'):
                    info["num_vertices"] = java_mesh.getVertex().size()
                if hasattr(java_mesh, 'getElement'):
                    info["num_elements"] = java_mesh.getElement().size()
            except Exception:
                pass
            
            try:
                children = [child.name() for child in mesh_node.children()]
                info["features"] = children
            except Exception:
                pass
            
            return {
                "success": True,
                "mesh": info,
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to get mesh info: {str(e)}"}

    @mcp.tool()
    def mesh_import(
        file_path: str,
        mesh_name: Optional[str] = None,
        component_name: str = "comp1",
        feature_name: Optional[str] = None,
        model_name: Optional[str] = None,
        reset_view: bool = True,
    ) -> dict:
        """
        Import an external mesh file, such as STL, into a COMSOL mesh sequence.

        This is the preferred path for STL files that fail geometry import due
        to non-manifold edges or repair problems.
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}",
            }

        path = Path(file_path)
        if not path.exists():
            return {"success": False, "error": f"File not found: {file_path}"}

        try:
            comp = model.java.component(component_name)
            if comp is None:
                return {"success": False, "error": f"Component '{component_name}' not found."}

            if not list(comp.geom().tags()):
                comp.geom().create("geom1", 3)

            existing_meshes = list(comp.mesh().tags())
            target_mesh = mesh_name or _next_tag(existing_meshes, "mesh")
            mesh = comp.mesh(target_mesh) if target_mesh in existing_meshes else comp.mesh().create(target_mesh)

            existing_features = list(mesh.feature().tags())
            import_tag = feature_name or _next_tag(existing_features, "imp")
            import_feature = mesh.feature().create(import_tag, "Import")
            import_feature.set("filename", str(path.absolute()))
            if path.suffix.lower() == ".stl":
                for prop, value in (("domelem", "on"), ("createdom", "on"), ("selectionstl", "on")):
                    try:
                        import_feature.set(prop, value)
                    except Exception:
                        pass
            mesh.run()

            result = {
                "success": True,
                "mesh": {
                    "name": target_mesh,
                    "feature": import_tag,
                    "file": str(path.absolute()),
                },
            }
            if path.suffix.lower() == ".stl":
                result["stl_analysis"] = analyze_binary_stl(path)
            if reset_view:
                result["view_reset"] = _reset_default_view(
                    model,
                    component_name,
                    result.get("stl_analysis"),
                )
            return result
        except Exception as e:
            return {"success": False, "error": f"Failed to import mesh: {str(e)}"}
