"""Add soft head-only morph targets to the accepted perched crow GLB."""

from pathlib import Path
import math

import bpy
from mathutils import Matrix, Vector


ROOT = Path(__file__).resolve().parents[1]
GLB = ROOT / "app/static/models/art/crow-perched.glb"


def smoothstep(edge0, edge1, value):
    t = max(0.0, min(1.0, (value - edge0) / (edge1 - edge0)))
    return t * t * (3.0 - 2.0 * t)


def head_weight(co):
    # Full influence on the crown/beak, tapering out along the upper neck.
    along_neck = 1.0 - smoothstep(-0.17, -0.075, co.y)
    upper_neck = smoothstep(0.105, 0.155, co.z)
    return along_neck * upper_neck


def add_pose(obj, name, rotation_axis, angle, pivot):
    key = obj.shape_key_add(name=name, from_mix=False)
    pivot = Vector(pivot)
    transform = Matrix.Rotation(angle, 4, rotation_axis)
    for vertex in obj.data.vertices:
        base = vertex.co
        weight = head_weight(base)
        if weight <= 0.0:
            continue
        rotated = transform @ (base - pivot) + pivot
        key.data[vertex.index].co = base.lerp(rotated, weight)
    return key


def main():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.import_scene.gltf(filepath=str(GLB))
    crow = next(
        obj for obj in bpy.context.scene.objects
        if obj.type == "MESH" and obj.name.startswith("Crow_Perched")
    )
    if not crow.data.shape_keys:
        crow.shape_key_add(name="Basis", from_mix=False)
    key_names = {key.name for key in crow.data.shape_keys.key_blocks}
    if "Crow_Head_Turn_Left" not in key_names:
        add_pose(crow, "Crow_Head_Turn_Left", "Z", math.radians(11), (0, -0.095, 0.165))
    if "Crow_Head_Turn_Right" not in key_names:
        add_pose(crow, "Crow_Head_Turn_Right", "Z", math.radians(-11), (0, -0.095, 0.165))
    if "Crow_Head_Dip" not in key_names:
        add_pose(crow, "Crow_Head_Dip", "X", math.radians(13), (0, -0.095, 0.165))

    bpy.ops.object.select_all(action="DESELECT")
    crow.select_set(True)
    bpy.context.view_layer.objects.active = crow
    bpy.ops.export_scene.gltf(
        filepath=str(GLB),
        export_format="GLB",
        use_selection=True,
        export_apply=False,
        export_animations=False,
        export_yup=True,
    )


if __name__ == "__main__":
    main()
