"""Material tools for COMSOL MCP Server."""

from typing import Optional, Sequence
import re

from mcp.server.fastmcp import FastMCP

from .session import session_manager


def _sanitize_material_tag(material_name: str) -> str:
    """Create a stable COMSOL tag from a material name."""
    clean = re.sub(r"[^0-9A-Za-z_]+", "_", material_name.strip()).strip("_").lower()
    if not clean:
        clean = "material"
    if clean[0].isdigit():
        clean = f"mat_{clean}"
    elif not clean.startswith("mat"):
        clean = f"mat_{clean}"
    return clean


def _create_material(
    model,
    material_name: str,
    properties: dict[str, str],
    component_name: str = "comp1",
    material_tag: Optional[str] = None,
    property_group: str = "def",
    domain_selection: Optional[Sequence[int]] = None,
) -> dict:
    if not material_name or not material_name.strip():
        return {"success": False, "error": "material_name must not be empty."}
    if not properties:
        return {"success": False, "error": "properties must contain at least one material property."}
    if not property_group or not property_group.strip():
        return {"success": False, "error": "property_group must not be empty."}

    normalized_properties = {}
    for key, value in properties.items():
        if not key or not str(key).strip():
            return {"success": False, "error": "Material property names must not be empty."}
        normalized_properties[str(key)] = str(value)

    selected_domains = None
    if domain_selection is not None:
        try:
            selected_domains = [int(domain) for domain in domain_selection]
        except (TypeError, ValueError) as e:
            return {"success": False, "error": f"Invalid domain_selection: {str(e)}"}

    try:
        jm = model.java
        comp = jm.component(component_name)
        if comp is None:
            return {
                "success": False,
                "error": f"Component '{component_name}' not found. Create it first with model_create_component.",
            }

        tag = material_tag or _sanitize_material_tag(material_name)
        mat = comp.material().create(tag, "Common")
        mat.label(material_name)

        group = mat.propertyGroup(property_group)
        for key, value in normalized_properties.items():
            group.set(key, value)

        if domain_selection is not None:
            mat.selection().set(selected_domains)

        return {
            "success": True,
            "material": {
                "tag": tag,
                "label": material_name,
                "component": component_name,
                "property_group": property_group,
                "properties": normalized_properties,
                "domain_selection": selected_domains,
            },
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to create material: {str(e)}"}


def register_material_tools(mcp: FastMCP) -> None:
    """Register material tools with the MCP server."""

    @mcp.tool()
    def material_create(
        material_name: str,
        properties: dict[str, str],
        component_name: str = "comp1",
        material_tag: Optional[str] = None,
        property_group: str = "def",
        domain_selection: Optional[Sequence[int]] = None,
        model_name: Optional[str] = None,
    ) -> dict:
        """
        Create a material and set material properties.

        Args:
            material_name: Display label for the material.
            properties: COMSOL material property keys and values.
            component_name: Component to create the material in (default: 'comp1').
            material_tag: Optional COMSOL material tag. Generated from material_name if omitted.
            property_group: Material property group to write to (default: 'def').
            domain_selection: Optional domain numbers to assign to this material.
            model_name: Model name (default: current model).

        Returns:
            Created material information, or an error message.
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}",
            }

        return _create_material(
            model=model,
            material_name=material_name,
            properties=properties,
            component_name=component_name,
            material_tag=material_tag,
            property_group=property_group,
            domain_selection=domain_selection,
        )
