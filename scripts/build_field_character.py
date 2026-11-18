"""Собирает сменные детали полевого персонажа на риге Quaternius (CC0).
Запуск из корня проекта: blender --background --python scripts/build_field_character.py
"""
import math
import pathlib
import random
import sys
from array import array

import bmesh
import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import barycentric_transform, tessellate_polygon

root = pathlib.Path.cwd()
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=str(root / 'app/static/models/characters/candidates/quaternius_hoodie.glb'))
scene = bpy.context.scene
rig = next(o for o in scene.objects if o.type == 'ARMATURE')
source = {o.name: o for o in scene.objects if o.type == 'MESH'}
bones = rig.pose.bones
world_to_rig = rig.matrix_world.inverted().to_3x3()
sides = [('L', 1), ('R', -1)]
clip_fps = 15


def color(hex_color):
    rgb = [int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(c / 12.92 if c < .04045 else ((c + .055) / 1.055) ** 2.4 for c in rgb) + (1,)


def material(name, tone, roughness=.88):
    result = bpy.data.materials.new(name)
    result.diffuse_color = color(tone)
    result.use_nodes = True
    shader = result.node_tree.nodes.get('Principled BSDF')
    shader.inputs['Base Color'].default_value = result.diffuse_color
    shader.inputs['Roughness'].default_value = roughness
    return result


fabric = material('Свитер', '626c50')
rib = material('Резинка свитера', '535c42')
denim = material('Деним', '354e65')
denim_seam = material('Швы джинсов', '476073')
canvas = material('Палаточная ткань', '77755a')
panel = material('Усиления Горки', '464d3b')
zipper = material('Молния и застёжки', '3b3b32')
leather = material('Кожа берцев', '413a31')
rubber = material('Подошва', '292824')
laces = material('Шнурки', '696051')
mouth = material('Линия губ', '765748')
hair_mat = material('Волосы', '483226')


def separate(original, name, wanted):
    obj = original.copy()
    obj.data = original.data.copy()
    scene.collection.objects.link(obj)
    obj.name = name
    mesh = bmesh.new()
    mesh.from_mesh(obj.data)
    slots = original.data.materials
    bmesh.ops.delete(mesh, geom=[f for f in mesh.faces if slots[f.material_index].name not in wanted], context='FACES_ONLY')
    bmesh.ops.delete(mesh, geom=[v for v in mesh.verts if not v.link_faces], context='VERTS')
    mesh.to_mesh(obj.data)
    mesh.free()
    return obj


def body_weights(z):
    if z <= 1.01:
        return {'Hips': 1}
    if z <= 1.22:
        ratio = (z - 1.01) / .21
        return {'Hips': 1 - ratio, 'Abdomen': ratio}
    ratio = min(1, (z - 1.22) / .19)
    return {'Abdomen': 1 - ratio, 'Chest': ratio}


class Garment:
    def __init__(self, name, materials):
        self.name = name
        self.materials = materials
        self.vertices = []
        self.faces = []
        self.weights = []
        self.finish = []

    def quad(self, points, weights, finish):
        first = len(self.vertices)
        self.vertices.extend(points)
        self.weights.extend(weights)
        self.faces.append(tuple(range(first, first + len(points))))
        self.finish.append(finish)

    def loft(self, rings, segments, finish, direction=0, close=True, omit=None):
        first = len(self.vertices)
        for center, a, b, weights in rings:
            for i in range(segments):
                angle = math.tau * i / segments
                u, v = math.cos(angle) * a, math.sin(angle) * b
                x, y, z = center
                self.vertices.append((x, y + u, z + v * direction) if direction else (x + u, y + v, z))
                self.weights.append(weights)
        for ring in range(len(rings) - 1):
            for i in range(segments):
                if omit and omit(ring, i):
                    continue
                a = first + ring * segments + i
                b = first + ring * segments + (i + 1) % segments
                self.faces.append((a, b, b + segments, a + segments))
                self.finish.append(finish(ring, (i + .5) * math.tau / segments))
        if close:
            self.faces.extend([tuple(reversed(range(first, first + segments))),
                tuple(range(len(self.vertices) - segments, len(self.vertices)))])
            self.finish.extend([finish(0, 0), finish(len(rings) - 2, 0)])
        return first

    def extend_opening(self, outline, rings, plane, direction, finish):
        """Продолжает существующий контур, сохраняя общие вершины на стыке."""
        u, v = plane
        points = [self.vertices[i] for i in outline]
        def extent(axis):
            values = [p[axis] for p in points]
            return (min(values) + max(values)) / 2, (max(values) - min(values)) / 2
        center_u, radius_u = extent(u)
        center_v, radius_v = extent(v)
        winding = sum(p[u] * q[v] - p[v] * q[u] for p, q in zip(points, points[1:] + points[:1]))
        if winding * direction < 0:
            outline = list(reversed(outline))
            points.reverse()
        angles = [math.atan2((p[v] - center_v) / radius_v, (p[u] - center_u) / radius_u) for p in points]
        previous = outline
        for row, (center, width, depth, weights) in enumerate(rings):
            current = []
            for angle in angles:
                point = list(center)
                point[u] += math.cos(angle) * width
                point[v] += math.sin(angle) * depth
                current.append(len(self.vertices))
                self.vertices.append(point)
                self.weights.append(weights)
            for i in range(len(current)):
                n = (i + 1) % len(current)
                self.faces.append((previous[i], previous[n], current[n], current[i]))
                self.finish.append(finish(row, angles[i]))
            previous = current
        self.faces.append(tuple(previous))
        self.finish.append(finish(len(rings) - 1, 0))

    def object(self):
        mesh = bpy.data.meshes.new(self.name)
        mesh.from_pydata(self.vertices, [], self.faces)
        mesh.update()
        obj = bpy.data.objects.new(self.name, mesh)
        scene.collection.objects.link(obj)
        for mat in self.materials:
            mesh.materials.append(mat)
        for poly, finish in zip(mesh.polygons, self.finish):
            poly.material_index = finish
            poly.use_smooth = len(poly.vertices) == 4
        groups = {}
        for index, values in enumerate(self.weights):
            for name, weight in values.items():
                if weight <= 0:
                    continue
                if name not in groups:
                    groups[name] = obj.vertex_groups.new(name=name)
                groups[name].add([index], weight, 'REPLACE')
        obj.modifiers.new('Общий скелет', 'ARMATURE').object = rig
        return obj


def ellipse_rings(profile, cy=-.047):
    return [((0, cy, z), x, y, body_weights(z)) for z, x, y in profile]


def surface_inlays(obj, axes, areas, on_surface):
    """Карманы и клапаны занимают грани ткани и деформируются вместе с ней."""
    mesh = bmesh.new()
    mesh.from_mesh(obj.data)
    for index, axis in enumerate(axes):
        borders = sorted({bound for area in areas for bound in area[index * 2:index * 2 + 2]})
        normal = Vector((0, 0, 0))
        normal[axis] = 1
        for border in borders:
            bmesh.ops.bisect_plane(mesh, geom=[*mesh.verts, *mesh.edges, *mesh.faces],
                                   dist=.00001, plane_co=normal * border, plane_no=normal)
    u, v = axes
    for face in mesh.faces:
        point = face.calc_center_median()
        if on_surface(point):
            for u0, u1, v0, v1, finish in areas:
                if u0 < point[u] < u1 and v0 < point[v] < v1:
                    face.material_index = finish
    mesh.to_mesh(obj.data)
    mesh.free()
    obj.data.update()


upper_ring_segments = 20


def tailored_upper(garment, profile, sleeves, finish, sleeve_finish):
    sides_count = upper_ring_segments
    port_rows = [4, 5, 6, 7]
    first = garment.loft(ellipse_rings(profile), sides_count, finish, close=False,
        omit=lambda row, column: row in port_rows[:-1] and column in (18, 19, 0, 1, 8, 9, 10, 11))
    torso_faces = list(garment.faces)
    collar = first + (len(profile) - 1) * sides_count
    # Изнанка загибается внутрь к шее и закрывает ворот при взгляде из положения лёжа.
    garment.extend_opening(list(range(collar, collar + sides_count)), [
        ((0, -.045, 1.533), .060, .053, {'Chest': .65, 'Neck': .35}),
        ((0, -.042, 1.502), .037, .034, {'Chest': .25, 'Neck': .75}),
    ], (0, 1), 1, lambda row, angle: 1)
    for side, sign in sides:
        columns = [-2, -1, 0, 1, 2] if sign > 0 else [8, 9, 10, 11, 12]
        contour = [(row, columns[-1]) for row in port_rows]
        contour += [(port_rows[-1], col) for col in reversed(columns[:-1])]
        contour += [(row, columns[0]) for row in reversed(port_rows[:-1])]
        contour += [(port_rows[0], col) for col in columns[1:-1]]
        outline = [first + row * sides_count + col % sides_count for row, col in contour]
        upper, lower = 'UpperArm.' + side, 'LowerArm.' + side
        for index in outline:
            weights = garment.weights[index]
            garment.weights[index] = {**{bone: weight * .77 for bone, weight in weights.items()}, upper: .23}
        rings = [((x * sign, y, z), a, b,
                  {upper: 1 - blend, lower: blend}) for x, y, z, a, b, blend in sleeves]
        garment.extend_opening(outline, rings, (1, 2), sign, sleeve_finish)
    return torso_faces


gorka_sleeve = [
    (.272, -.061, 1.424, .078, .076, .12), (.302, -.068, 1.432, .073, .071, .25),
    (.355, -.074, 1.432, .068, .066, .75), (.436, -.077, 1.429, .067, .065, 1),
    (.481, -.080, 1.432, .066, .064, 1), (.552, -.085, 1.432, .057, .054, 1),
    (.596, -.087, 1.433, .054, .051, 1),
]


def shirt(gorka):
    garment = Garment('Куртка Горка' if gorka else 'Свитер',
        [canvas, panel, zipper] if gorka else [fabric, rib])
    profile = [
        (.967, .236, .161), (.986, .240, .163), (1.04, .230, .151),
        (1.12, .222, .140), (1.22, .241, .157), (1.36, .269, .165),
        (1.445, .261, .148), (1.485, .209, .126), (1.518, .095, .082), (1.549, .075, .069),
    ]
    sleeves = [
        (.278, -.061, 1.424, .096, .093, .12), (.305, -.068, 1.432, .088, .087, .25),
        (.355, -.074, 1.432, .078, .075, .75), (.436, -.077, 1.429, .075, .072, 1),
        *gorka_sleeve[4:],
    ]
    if gorka:
        profile = [
            (.877, .232, .150), (.907, .235, .154), (.974, .230, .150),
            (1.12, .185, .110), (1.22, .210, .123), (1.36, .247, .139),
            (1.445, .240, .127), (1.485, .196, .111), (1.518, .097, .082), (1.549, .077, .070),
        ]
        sleeves = gorka_sleeve
    tailored_upper(garment, profile, sleeves,
        lambda row, angle: int(row >= 6 if gorka else row in (0, 8)),
        lambda row, angle: int(row == len(sleeves) - 1 or (gorka and (row < 2 or row in (3, 4) and math.sin(angle) < .3))))
    if gorka:
        hood = [((0, y, z), a, b, {'Chest': 1}) for z, y, a, b in [
            (1.36, .089, .119, .037), (1.42, .105, .132, .044),
            (1.49, .079, .116, .050), (1.535, .052, .089, .036),
        ]]
        garment.loft(hood, 14, lambda ring, angle: 1)
    obj = garment.object()
    if gorka:
        areas = [(-.009, .009, profile[0][0], profile[-1][0], 2)]
        for x in (-.111, .111):
            areas.extend([
                (x - .052, x + .052, 1.007, 1.133, 1),
                (x - .055, x + .055, 1.112, 1.139, 2),
                (x - .035, x + .035, 1.277, 1.355, 1),
            ])
        surface_inlays(obj, (0, 2), areas, lambda point: point.y < -.047)
    return obj


def coat_skirt(garment, trench):
    # Каждая пола охватывает бедро снаружи. Разрезы спереди и сзади
    # дают ногам двигаться независимо при шаге и глубоком приседе.
    count = upper_ring_segments // 2
    hem = .557 if trench else .500
    rows = [(.916, .247, .169, .13, .13), (.889, .252, .173, .23, .23),
            (.850, .259, .171, .43, .43), (.798, .265, .166, 1, 1),
            (.710, .274, .165, 1, 1), (.586, .279, .158, 1, .75),
            (hem + .012, .281, .156, 1, .62 if trench else .43),
            (hem, .279, .154, 1, .60 if trench else .39)]
    for side, sign in sides:
        previous = [(-count // 2 + sign * i) % upper_ring_segments for i in range(count + 1)]
        for row, (height, width, depth, split, bend) in enumerate(rows):
            current = []
            for i in range(count + 1):
                t = i / count
                angle = (2 * t - 1) * (math.pi / 2 + .67 * split)
                center = .12 * split
                fold = .005 * split * math.cos(3 * angle + (0 if trench else .4))
                front = max(0, -math.sin(angle))
                edge = abs(2 * t - 1)**6
                overlap = .063 * split * front * edge
                x = sign * (max(.004 + .012 * split, center + (width - center) * math.cos(angle)) + fold - overlap)
                edge_room = 1 + .20 * split * edge
                clearance = .045 * max(0, 1 - abs(height - .895) / .17)
                y = -.047 - .034 * split + (depth + fold) * math.sin(angle) * edge_room - clearance * front
                if sign > 0:
                    y -= .011 * front * edge * split
                z = height + .009 * split * (1 - math.sin(math.pi * t))
                current.append(len(garment.vertices))
                garment.vertices.append((x, y, z))
                garment.weights.append({'Body': 1 - split, 'UpperLeg.' + side: bend, 'LowerLeg.' + side: split - bend})
            for i in range(count):
                face = (previous[i], current[i], current[i + 1], previous[i + 1])
                garment.faces.append(face if sign > 0 else face[::-1])
                garment.finish.append(1 if row == len(rows) - 1 else 0)
            previous = current


def coat_details(garment, torso_faces, trench):
    # Подгоняем детали к самой поверхности. Веса берём с тех же треугольников,
    # чтобы клапаны и лацканы не отрывались при движении плеч и корпуса.
    def flatten(p):
        return Vector((p[0], p[2]))

    def clip(points, triangle):
        for a, b in zip(triangle, (*triangle[1:], triangle[0])):
            if not points:
                break
            edge = b - a
            polygon, points = points, []
            prev = polygon[-1]
            prev_dist = edge.cross(prev - a)
            for curr in polygon:
                curr_dist = edge.cross(curr - a)
                if (curr_dist < 0) != (prev_dist < 0):
                    points.append(prev.lerp(curr, prev_dist / (prev_dist - curr_dist)))
                if curr_dist >= 0:
                    points.append(curr)
                prev, prev_dist = curr, curr_dist
        return points

    basis = [Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))]
    faces = {True: [], False: []}
    for indices in torso_faces:
        points = [garment.vertices[i] for i in indices]
        a, b, c = [flatten(p) for p in points]
        direction = (b - a).cross(c - a)
        if abs(direction) < 1e-8:
            continue
        if direction < 0:
            indices = indices[::-1]
            points.reverse()
        faces[direction > 0].append((indices, points, tuple(flatten(p) for p in points)))

    def patch(outline, finish, relief=.004, front=True):
        sign = -1 if front else 1
        outline_3d = [Vector((x, 0, z)) for x, z in outline]
        outline_2d = [flatten(p) for p in outline_3d]
        for patch_indices in tessellate_polygon([outline_3d]):
            shape = [outline_2d[i] for i in patch_indices]
            for indices, vertices, triangle in faces[front]:
                polygon = clip(shape, triangle)
                if len(polygon) < 3:
                    continue
                winding = sum(p.cross(q) for p, q in zip(polygon, polygon[1:] + polygon[:1]))
                if abs(winding) < 1e-8:
                    continue
                if winding * -sign < 0:
                    polygon.reverse()
                positions, weights = [], []
                for p in polygon:
                    factors = barycentric_transform(Vector((*p, 0)),
                        *(Vector((*q, 0)) for q in triangle), *basis)
                    positions.append((p.x, sum(v[1] * f for v, f in zip(vertices, factors)) + sign * relief, p.y))
                    weights.append({})
                    for index, factor in zip(indices, factors):
                        for bone, weight in garment.weights[index].items():
                            weights[-1][bone] = weights[-1].get(bone, 0) + max(0, factor) * weight
                garment.quad(positions, weights, finish)

    def button(x, z, radius=.012, front=True):
        patch([(x + radius * math.cos(a * math.tau / 8), z + radius * math.sin(a * math.tau / 8))
               for a in range(8)], 3, .006, front)

    patch([(-.024, .958), (-.015, .958), (-.015, 1.25), (-.024, 1.25)], 2, .003)
    for _, sign in sides:
        if trench:
            lapel = [(.041, 1.531), (.095, 1.505), (.127, 1.437),
                     (.105, 1.418), (.157, 1.395), (.066, 1.277), (.021, 1.301)]
            flap = [(.069, 1.066), (.173, 1.089), (.185, 1.051), (.155, 1.034), (.075, 1.029)]
        else:
            lapel = [(.037, 1.530), (.110, 1.502), (.151, 1.450),
                     (.096, 1.435), (.163, 1.414), (.014, 1.239), (.041, 1.338)]
            flap = [(.086, 1.052), (.164, 1.108), (.176, 1.092), (.098, 1.035)]
        patch([(sign * x, z) for x, z in lapel], 1, .007)
        patch([(sign * x, z) for x, z in flap], 2 if not trench else 1, .006)
        for z, x in [(1.139, .069), (1.241, .077), (1.343, .095)]:
            button(sign * x, z, .011 if trench else .010)
        if trench:
            epaulette = [(sign * x, z) for x, z in [(.101, 1.483), (.191, 1.463), (.183, 1.443), (.108, 1.465)]]
            patch(epaulette, 1, .005)
            patch(epaulette, 1, .005, front=False)
            button(sign * .120, 1.469, .007)
    if trench:
        patch([(-.171, 1.402), (0, 1.371), (.171, 1.402), (.144, 1.468),
               (0, 1.484), (-.144, 1.468)], 1, .005, front=False)
    else:
        patch([(-.008, .965), (.006, .965), (.006, 1.509), (-.008, 1.509)], 2, .003, front=False)
        patch([(-.156, 1.100), (.156, 1.100), (.153, 1.145), (-.153, 1.145)],
              1, .007, front=False)
        for x in (-.105, .105):
            button(x, 1.122, front=False)


def coat(trench):
    garment = Garment('Мужской тренч' if trench else 'Кожаный плащ', [fabric, rib, zipper, laces])
    profile = [
        (.954, .249, .188), (.982, .241, .171), (1.04, .222, .148),
        (1.12, .191 if trench else .198, .118), (1.22, .213, .133),
        (1.36, .247 if trench else .254, .149), (1.445, .244, .131),
        (1.485, .207, .118), (1.518, .102, .088), (1.552, .076, .073),
    ]
    sleeves = [*gorka_sleeve[:4], (.481, -.080, 1.432, .064, .061, 1),
               (.553, -.085, 1.432, .060, .054, 1), (.596, -.087, 1.433, .056, .051, 1)]
    torso = tailored_upper(garment, profile, sleeves, lambda row, angle: int(row == 8),
        lambda row, angle: int(row >= len(sleeves) - 2))
    for i in range(upper_ring_segments * 2):
        garment.weights[i] = {'Body': 1}
    triangles = [(face[0], face[i], face[i + 1]) for face in torso for i in range(1, len(face) - 1)]
    garment.faces[:len(torso)] = triangles
    garment.finish[:len(torso)] = [finish for finish, face in zip(garment.finish, torso) for _ in range(len(face) - 2)]
    coat_skirt(garment, trench)
    coat_details(garment, triangles, trench)
    result = garment.object()
    for polygon in result.data.polygons:
        polygon.use_smooth = polygon.material_index != 3
    return result


def trousers(style):
    names = {'jeans': 'Джинсы', 'gorka': 'Штаны Горка', 'tactical': 'Тактические штаны', 'hunting': 'Охотничьи штаны'}
    gorka = style == 'gorka'
    pants = Garment(names[style], [denim, denim_seam] if style == 'jeans' else [canvas, panel, zipper])
    first = pants.loft([((0, -.05, z), a, b, {'Body': 1}) for z, a, b in [
        (.889, .211, .135), (.934, .223, .142), (.97, .225, .143),
        (1.004, .211, .128), (1.024, .211, .127),
    ]], 24, lambda ring, angle: int(ring == 3), close=False)
    crotch = []
    for i in range(1, 7):
        t = i / 7
        crotch.append(len(pants.vertices))
        pants.vertices.append((0, .085 - .27 * t, .889 - .052 * math.sin(math.pi * t)))
        pants.weights.append({'Body': 1})
    for side, sign in sides:
        if sign > 0:
            outline = [first + i % 24 for i in range(-6, 7)] + crotch
        else:
            outline = [first + i for i in range(6, 19)] + list(reversed(crotch))
        rows = [
            (.157, -.066, .061, .062, 0), (.188, -.068, .065, .066, 0),
            (.229, -.069, .075, .073, 0), (.282, -.073, .076, .081, 0), (.416, -.081, .083, .087, .18),
            (.522, -.085, .085, .093, .45), (.586, -.083, .087, .091, .75),
            (.71, -.064, .100, .110, 1), (.798, -.053, .101, .115, 1),
        ]
        upper, lower = 'UpperLeg.' + side, 'LowerLeg.' + side
        rings = [((.12 * sign, y, z), a * (1.09 if gorka else 1), b * (1.10 if gorka else 1),
                  {upper: blend, lower: 1 - blend}) for z, y, a, b, blend in reversed(rows)]
        pants.extend_opening(outline, rings, (0, 1), -1, lambda row, angle: int(
            (row == len(rings) - 1 or (2 <= row <= 4 and math.sin(angle) < -.14)) if gorka
            else style == 'jeans' and math.cos(angle) * sign > .985))
    obj = pants.object()
    if gorka:
        surface_inlays(obj, (1, 2), [(-.142, .016, .655, .800, 1),
                                    (-.154, .025, .775, .817, 2)],
                       lambda point: abs(point.x) > .15)
    if style == 'tactical':
        surface_inlays(obj, (1, 2), [(-.137, .042, .644, .769, 1),
                                    (-.150, .050, .755, .779, 2)],
                       lambda point: abs(point.x) > .151)
        pads = [(sign * .12 - .072, sign * .12 + .072, .446, .615, 1) for _, sign in sides]
        surface_inlays(obj, (0, 2), pads, lambda point: point.y < -.134)
    if style == 'hunting':
        surface_inlays(obj, (1, 2), [(-.106, .026, .692, .814, 1),
                                    (-.121, .033, .789, .811, 2)],
                       lambda point: abs(point.x) > .152)
        guards = [(sign * .12 - .082, sign * .12 + .082, .295, .590, 1) for _, sign in sides]
        surface_inlays(obj, (0, 2), guards, lambda point: point.y < -.122)
    return obj


def boots():
    shoes = Garment('Берцы', [leather, rubber, laces])
    areas = []
    for side, sign in sides:
        foot, shin = 'Foot.' + side, 'LowerLeg.' + side
        x = sign * .120
        # Подошва следует за стопой, манжета движется с голенью и краем брючины.
        shoes.loft([((x, y, z), w, d, {foot: 1 - blend, shin: blend}) for z, y, w, d, blend in [
            (0, -.111, .078, .174, 0), (.026, -.111, .080, .174, 0),
            (.044, -.111, .079, .172, 0), (.069, -.114, .074, .165, 0),
            (.103, -.098, .073, .142, 0), (.140, -.063, .077, .086, .65),
            (.165, -.066, .080, .085, 1), (.182, -.068, .081, .085, 1),
            (.190, -.068, .079, .082, 1),
        ]], 14, lambda ring, angle: int(ring < 2))
        for z in (.080, .100, .120, .140, .160):
            areas.append((x - .032, x + .032, z, z + .005, 2))
    obj = shoes.object()
    surface_inlays(obj, (0, 2), areas, lambda point: point.y < -.107)
    return obj


def is_ear_point(point):
    return .066 < abs(point.x) < .109 and -.093 < point.y < -.069 and 1.652 < point.z < 1.708


def faces():
    src = source['Casual_Head']
    calm = separate(src, 'Спокойное лицо', {'Skin', 'Eyebrows', 'Eye'})
    wounded = separate(src, 'Раненое лицо', {'Skin', 'Eyebrows', 'Eye'})
    world = wounded.matrix_world
    inverse = world.inverted()
    eyebrow, eye = set(), set()
    for polygon in wounded.data.polygons:
        name = wounded.data.materials[polygon.material_index].name
        if name == 'Eyebrows':
            eyebrow.update(polygon.vertices)
        if name == 'Eye':
            eye.update(polygon.vertices)
    for index in eyebrow:
        point = world @ wounded.data.vertices[index].co
        point.z += .014 * (1 - min(1, abs(point.x) / .073)) - .003
        wounded.data.vertices[index].co = inverse @ point
    for index in eye:
        point = world @ wounded.data.vertices[index].co
        point.z = 1.699 + (point.z - 1.699) * .62
        wounded.data.vertices[index].co = inverse @ point
    hair = separate(src, 'Короткая стрижка', {'Hair'})
    hair.data.materials[3] = hair_mat
    for vertex in hair.data.vertices:
        point = hair.matrix_world @ vertex.co
        point.z = 1.75 + (point.z - 1.75) * .83
        vertex.co = hair.matrix_world.inverted() @ point

    # У исходной головы есть только левое ухо. Восстанавливаем правое по его контуру.
    ear = Garment('Правое ухо', [calm.data.materials['Skin']])
    source_points = [calm.matrix_world @ vertex.co for vertex in calm.data.vertices]
    mirrored = {}
    for polygon in calm.data.polygons:
        if not all(source_points[i].x > 0 and is_ear_point(source_points[i]) for i in polygon.vertices):
            continue
        for i in polygon.vertices:
            if i not in mirrored:
                x, y, z = source_points[i]
                mirrored[i] = len(ear.vertices)
                ear.vertices.append((-x, y, z))
                ear.weights.append({'Head': 1})
        ear.faces.append(tuple(mirrored[i] for i in reversed(polygon.vertices[:])))
        ear.finish.append(0)
    right_ear = ear.object()

    skin_faces = [p.vertices[:] for p in calm.data.polygons if calm.data.materials[p.material_index].name == 'Skin']
    skin = BVHTree.FromPolygons([calm.matrix_world @ v.co for v in calm.data.vertices], skin_faces)
    def lips(hurt):
        lip = Garment('Губы', [mouth])
        rows = []
        for i in range(7):
            x = (i - 3) * (.012 if hurt else .010)
            z = 1.632 - (.010 * (i / 6) ** 2 if hurt else .0025 * abs(i - 3) / 3)
            point, _, _, _ = skin.ray_cast(Vector((x, -.35, z)), Vector((0, 1, 0)))
            y = point.y - .0013 if point is not None else -.112
            opening = (.006 if hurt else .0025) * math.sin(math.pi * i / 6)
            rows.append([(x, y, z - opening / 2), (x, y, z + opening / 2 + .0008)])
        for left, right in zip(rows, rows[1:]):
            lip.quad([left[0], right[0], right[1], left[1]], [{'Head': 1}] * 4, 0)
        return lip.object()
    wounded_ear = right_ear.copy()
    scene.collection.objects.link(wounded_ear)
    return [calm, lips(False), right_ear], [wounded, lips(True), wounded_ear], [hair]


def neck_skin(head):
    skin = head.data.materials['Skin']
    neck = Garment('Шея под воротом', [skin])
    rim = {}
    for vertex in head.data.vertices:
        point = head.matrix_world @ vertex.co
        if abs(point.z - 1.52) < .0002:
            rim.setdefault(tuple(round(v, 5) for v in point), vertex)
    for point, vertex in sorted(rim.items(), key=lambda item: math.atan2(item[0][1] + .043, item[0][0])):
        neck.vertices.append(head.matrix_world @ vertex.co)
        neck.weights.append({head.vertex_groups[g.group].name: g.weight for g in vertex.groups})
    # Верхняя кромка повторяет вершины и веса головы. Нижняя уходит под оба воротника.
    neck.extend_opening(list(range(len(neck.vertices))), [
        ((0, -.041, 1.485), .050, .045, {'Neck': .63, 'Chest': .37}),
        ((0, -.041, 1.448), .058, .051, {'Neck': .20, 'Chest': .80}),
        ((0, -.041, 1.403), .077, .065, {'Chest': 1}),
    ], (0, 1), -1, lambda row, angle: 0)
    return neck.object()


calm, wounded, hair = faces()
parts = {
    'base': [separate(source['Casual_Body'], 'Кисти', {'Skin'}), boots(), neck_skin(calm[0])],
    'top_sweater': [shirt(False)], 'top_gorka': [shirt(True)],
    'top_leather_coat': [coat(False)], 'top_trench_coat': [coat(True)],
    'bottom_jeans': [trousers('jeans')], 'bottom_gorka': [trousers('gorka')],
    'bottom_tactical': [trousers('tactical')], 'bottom_hunting': [trousers('hunting')],
    'face_calm': calm, 'face_wounded': wounded, 'hair_short': hair,
}
for obj in source.values():
    bpy.data.objects.remove(obj, do_unlink=True)

# Исходная анимация даёт исходную стойку, остальные положения задаются на том же риге.
for track in rig.animation_data.nla_tracks:
    track.mute = track.name != 'Idle_Neutral'
scene.frame_set(0)
bpy.context.view_layer.update()
neutral = {b.name: b.matrix_basis.copy() for b in bones}
ankles = {side: rig.matrix_world @ bones['LowerLeg.' + side].tail for side, _ in sides}
rig.animation_data_clear()


def reset():
    for bone in bones:
        bone.matrix_basis = neutral[bone.name]
    bpy.context.view_layer.update()


def change_bone(name, matrix):
    bones[name].matrix = matrix
    bpy.context.view_layer.update()


def pivot(name, axis, angle):
    bone = bones[name]
    rotation = Matrix.Rotation(angle, 4, world_to_rig @ Vector(axis))
    change_bone(name, Matrix.Translation(bone.head) @ rotation @ Matrix.Translation(-bone.head) @ bone.matrix)


def aim(name, direction, next_name=None):
    bone = bones[name]
    endpoint = bones[next_name].head if next_name else bone.tail
    target = (world_to_rig @ Vector(direction)).normalized()
    rotation = (endpoint - bone.head).normalized().rotation_difference(target).to_matrix().to_4x4()
    change_bone(name, Matrix.Translation(bone.head) @ rotation @ Matrix.Translation(-bone.head) @ bone.matrix)


def aim_chain(upper, lower, end_name, target, pole):
    world = rig.matrix_world
    start = world @ bones[upper].head
    joint = world @ bones[lower].head
    endpoint = world @ (bones[end_name].head if end_name else bones[lower].tail)
    a, b = (joint - start).length, (endpoint - joint).length
    delta = target - start
    distance = min(a + b - .001, max(abs(a - b) + .001, delta.length))
    axis = delta.normalized()
    along = (a * a - b * b + distance * distance) / (2 * distance)
    bend = (pole - axis * axis.dot(pole)).normalized()
    aim(upper, axis * along + bend * math.sqrt(max(0, a * a - along * along)), lower)
    aim(lower, start + axis * distance - world @ bones[lower].head, end_name)


def aim_at_height(name, next_name, direction, height):
    world = rig.matrix_world
    start, end = world @ bones[name].head, world @ bones[next_name].head
    length = (end - start).length
    dz = max(-length + .002, min(length - .002, height - start.z))
    x, y, _ = Vector(direction).normalized() * math.sqrt(length ** 2 - dz ** 2)
    aim(name, (x, y, dz), next_name)


def sample_joints(capture, frames, mapping=None):
    samples = []
    for frame in frames:
        scene.frame_set(int(frame), subframe=frame % 1)
        names = mapping or {bone.name: bone.name for bone in capture.pose.bones}
        joints = {name: capture.matrix_world @ capture.pose.bones[source].head for name, source in names.items()}
        if not mapping:
            for side in ('Left', 'Right'):
                joints[side + 'HandTip'] = capture.matrix_world @ capture.pose.bones[side + 'Hand'].tail
        lateral = joints['LeftUpLeg'] - joints['RightUpLeg']
        if not samples:
            align = Matrix.Rotation(-math.atan2(lateral.y, lateral.x), 3, 'Z')
            origin = joints['Hips'].copy()
        samples.append({name: align @ (point - origin) for name, point in joints.items()})
    return samples


def accad_samples(filename, frames):
    bpy.ops.import_anim.bvh(filepath=str(root / 'assets/sources/accad' / filename), global_scale=.01)
    capture = bpy.context.object
    samples = sample_joints(capture, frames)
    bpy.data.objects.remove(capture, do_unlink=True)
    return samples


def quaternius_samples(names, *, loop):
    existing = set(scene.objects)
    path = root / 'assets/sources/quaternius/AnimationLibrary_Godot_Standard.glb'
    bpy.ops.import_scene.gltf(filepath=str(path))
    imported = set(scene.objects) - existing
    capture = next(obj for obj in imported if obj.type == 'ARMATURE')
    mapping = {'Hips': 'DEF-hips', 'Spine1': 'DEF-spine.002', 'Neck': 'DEF-neck', 'Head': 'DEF-head'}
    for short, long in [('L', 'Left'), ('R', 'Right')]:
        for name, joint in [('Arm', 'upper_arm'), ('ForeArm', 'forearm'), ('Hand', 'hand'),
                            ('HandTip', 'f_middle.01'), ('UpLeg', 'thigh'), ('Leg', 'shin'),
                            ('Foot', 'foot'), ('ToeBase', 'toe')]:
            mapping[long + name] = 'DEF-' + joint + '.' + short
    clips = []
    for name in names:
        action = next(track.strips[0].action for track in capture.animation_data.nla_tracks if track.name == name)
        capture.animation_data.use_nla = False
        capture.animation_data.action = action
        start, end = action.frame_range
        duration = (end - start) / scene.render.fps
        count = round(duration * clip_fps)
        frames = [start + (end - start) * i / count for i in range(count + (not loop))]
        samples = sample_joints(capture, frames, mapping)
        clips.append([*samples, samples[0]] if loop else samples)
    for obj in imported:
        bpy.data.objects.remove(obj, do_unlink=True)
    return clips


def kaykit_crawl_samples():
    existing = set(scene.objects)
    path = root / 'assets/sources/kaykit/Rig_Medium_MovementAdvanced.glb'
    bpy.ops.import_scene.gltf(filepath=str(path))
    imported = set(scene.objects) - existing
    capture = next(obj for obj in imported if obj.type == 'ARMATURE')
    capture.animation_data.use_nla = False
    capture.animation_data.action = next(track.strips[0].action for track in capture.animation_data.nla_tracks
                                         if track.name == 'Crawling')
    mapping = {'Hips': 'hips', 'Chest': 'chest'}
    for short, long in [('l', 'Left'), ('r', 'Right')]:
        mapping.update({long + 'UpLeg': 'upperleg.' + short, long + 'Foot': 'foot.' + short,
                        long + 'Hand': 'hand.' + short})
    start, end = capture.animation_data.action.frame_range
    count = round((end - start) * clip_fps / scene.render.fps)
    samples = sample_joints(capture, [start + (end - start) * i / count for i in range(count)], mapping)
    for obj in imported:
        bpy.data.objects.remove(obj, do_unlink=True)
    return [*samples, samples[0]]


def match_arm(side, joints, prefix):
    arm, forearm, wrist = 'UpperArm.' + side, 'LowerArm.' + side, 'Wrist.' + side
    aim(arm, joints[prefix + 'ForeArm'] - joints[prefix + 'Arm'], forearm)
    aim(forearm, joints[prefix + 'Hand'] - joints[prefix + 'ForeArm'], wrist)
    aim(wrist, joints[prefix + 'HandTip'] - joints[prefix + 'Hand'], 'Middle1.' + side)


def match_boot(side, ankle, toe, rest_pitch=0):
    foot_bone = bones['Foot.' + side].bone
    rest_offset = foot_bone.head_local - bones['LowerLeg.' + side].bone.tail_local
    rest_toe = Vector((0, -math.cos(rest_pitch), -math.sin(rest_pitch)))
    rotation = (world_to_rig @ rest_toe).normalized().rotation_difference((world_to_rig @ toe).normalized())
    matrix = rotation.to_matrix().to_4x4() @ foot_bone.matrix_local
    matrix.translation = rig.matrix_world.inverted() @ ankle + rotation @ rest_offset
    change_bone('Foot.' + side, matrix)


def side_levels(source):
    posed = source.evaluated_get(bpy.context.evaluated_depsgraph_get())
    points = [posed.matrix_world @ vertex.co for vertex in posed.data.vertices]
    return {side: min(point.z for original, point in zip(source.data.vertices, points)
                      if original.co.x * sign > 0) for side, sign in sides}


def boot_levels():
    return side_levels(parts['base'][1])


def upright(joints, support, crouching):
    center = (support['LeftFoot'] + support['RightFoot']) * .5
    body = bones['Body'].matrix.copy()
    if not crouching:
        # В исходном idle таз и грудь повёрнуты в разные стороны.
        body = bones['Body'].bone.matrix_local.copy()
        body.translation = bones['Body'].head
        for name in ('Hips', 'Abdomen', 'Torso', 'Chest', 'Neck', 'Head'):
            bones[name].matrix_basis = Matrix.Identity(4)
    shift = joints['Hips'] - center
    height = -.43 if crouching else .031
    body.translation += world_to_rig @ Vector((shift.x * .88, .05 + shift.y * .88, height + joints['Hips'].z))
    change_bone('Body', body)
    for bone, start, end, child, straighten in [('Abdomen', 'Hips', 'Spine1', 'Torso', .07),
                                               ('Torso', 'Spine1', 'Neck', 'Chest', .145),
                                               ('Neck', 'Neck', 'Head', 'Head', .25)]:
        direction = joints[end] - joints[start]
        if not crouching:
            direction.x = 0
            # Выпрямляем грудь и шею, сохраняя небольшие движения исходного idle.
            direction.y += direction.z * straighten
        aim(bone, direction, child)
    if crouching:
        aim('Head', (0, -.02, 1))
    supports = {}
    for side, sign in sides:
        prefix = 'Left' if side == 'L' else 'Right'
        thigh, shin = 'UpperLeg.' + side, 'LowerLeg.' + side
        offset = (support[prefix + 'Foot'] - center) * .88
        ankle = Vector((offset.x, .05 + offset.y, ankles[side].z + (offset.z if crouching else 0)))
        knee = joints[prefix + 'Leg'] - joints[prefix + 'UpLeg']
        aim_chain(thigh, shin, None, ankle, knee)
        toe = support[prefix + 'ToeBase'] - support[prefix + 'Foot']
        if not crouching:
            toe.z = 0
        match_boot(side, ankle, toe, .53 if crouching else 0)
        if crouching:
            match_arm(side, joints, prefix)
        else:
            shoulder, arm, forearm, wrist = (name + '.' + side for name in ('Shoulder', 'UpperArm', 'LowerArm', 'Wrist'))
            for name in (shoulder, arm, forearm, wrist):
                bones[name].matrix_basis = Matrix.Identity(4)
            bpy.context.view_layer.update()
            shoulder_pose = bones[shoulder].matrix.copy()
            shoulder_pose.translation += world_to_rig @ Vector((sign * .045, .008, 0))
            change_bone(shoulder, shoulder_pose)
            aim(arm, (sign * .46, .10, -.88), forearm)
            aim(forearm, (sign * .12, -.12, -.98), wrist)
        supports[side] = (ankle, toe, knee)
    levels = boot_levels()
    ground = min(levels.values())
    for side, (ankle, toe, knee) in supports.items():
        ankle.z -= levels[side] - ground
        aim_chain('UpperLeg.' + side, 'LowerLeg.' + side, None, ankle, knee)
        match_boot(side, ankle, toe, .53 if crouching else 0)


def plant_prone_hands(floor, relaxed=False, rest_on_elbows=False):
    world = rig.matrix_world
    for side, sign in sides:
        arm, forearm, wrist = ('UpperArm.' + side, 'LowerArm.' + side, 'Wrist.' + side)
        if rest_on_elbows:
            aim_at_height(arm, forearm, (sign * .75, 1, 0), floor + .069)
            aim_at_height(forearm, wrist, (sign * .13, -1, 0), floor + .035)
        else:
            shoulder = world @ bones[arm].head
            target = shoulder + Vector((sign * .15, -.14, 0))
            target.z = floor + .035
            aim_chain(arm, forearm, wrist, target, Vector((sign * .55, .9, -.35)))
        # Кисти лежат ладонями вниз. При потере опоры пальцы смотрят чуть в стороны.
        hand = Matrix.Rotation(-sign * math.pi / 2, 4, world_to_rig @ Vector((0, 0, 1))) @ bones[wrist].bone.matrix_local
        hand.translation = bones[wrist].head
        change_bone(wrist, hand)
        if relaxed:
            pivot(wrist, (0, 0, 1), sign * .38)


def ground_move(joints, support, resting=False):
    torso = joints['Neck'] - joints['Spine1']
    tilt = math.pi / 2 if resting else min(math.pi / 2, max(0, math.atan2(-torso.y, torso.z)))
    pivot('Root', (1, 0, 0), tilt)
    aim('Torso', (torso.x, -1, .04) if resting else torso, 'Chest')
    aim('Neck', joints['Head'] - joints['Neck'], 'Head')
    # В игровой позе на животе взгляд направлен вдоль земли.
    look_ahead = min(1, max(0, (tilt - 1.18) / .37))
    pivot('Neck', (1, 0, 0), -.20 * look_ahead)
    head = bones['Head']
    direction = rig.matrix_world.to_3x3() @ (head.tail - head.head)
    aim('Head', direction.normalized().lerp(Vector((0, -.02, 1)), look_ahead))
    feet = {}
    for side, sign in sides:
        prefix = 'Left' if side == 'L' else 'Right'
        thigh, shin = 'UpperLeg.' + side, 'LowerLeg.' + side
        knee = support[prefix + 'Leg'] - support[prefix + 'UpLeg']
        aim(thigh, knee, shin)
        aim(shin, support[prefix + 'Foot'] - support[prefix + 'Leg'])
        ankle = rig.matrix_world @ bones[shin].tail
        toe = support[prefix + 'ToeBase'] - support[prefix + 'Foot']
        if resting:
            toe = Vector((sign * .12, .8, -.58))
            feet[side] = (ankle, toe, sign)
        match_boot(side, ankle, toe, .50)
        if not resting:
            match_arm(side, joints, prefix)
    if resting:
        world = rig.matrix_world
        # Таз и грудь лежат вдоль одной плоскости. Кисти и берцы подгоняются к ней.
        floor = (world @ bones['Body'].head).z - .21
        plant_prone_hands(floor)
        levels = boot_levels()
        for side, (ankle, toe, sign) in feet.items():
            ankle += Vector((sign * .045, .02, floor - levels[side]))
            aim_chain('UpperLeg.' + side, 'LowerLeg.' + side, None, ankle, Vector((sign, 0, -.2)))
            match_boot(side, ankle, toe, .50)


def fall_back(joints, support):
    torso = joints['Neck'] - joints['Spine1']
    pivot('Root', (1, 0, 0), math.atan2(-torso.y, torso.z))
    body = bones['Body'].matrix.copy()
    body.translation += world_to_rig @ Vector((joints['Hips'].x * .65, joints['Hips'].y * .65, 0))
    change_bone('Body', body)
    aim('Torso', torso, 'Chest')
    aim('Neck', joints['Head'] - joints['Neck'], 'Head')
    for side, _ in sides:
        prefix = 'Left' if side == 'L' else 'Right'
        thigh, shin = 'UpperLeg.' + side, 'LowerLeg.' + side
        aim(thigh, joints[prefix + 'Leg'] - joints[prefix + 'UpLeg'], shin)
        aim(shin, joints[prefix + 'Foot'] - joints[prefix + 'Leg'])
        ankle = rig.matrix_world @ bones[shin].tail
        match_boot(side, ankle, joints[prefix + 'ToeBase'] - joints[prefix + 'Foot'])
        match_arm(side, joints, prefix)


def mesh_floor(sources):
    graph = bpy.context.evaluated_depsgraph_get()
    return min((posed.matrix_world @ v.co).z for obj in sources
               for posed in [obj.evaluated_get(graph)] for v in posed.data.vertices)


def head_drop_target(floor, direction):
    neck = bones['Neck']
    neutral_neck = neck.matrix_basis.copy()
    faces = parts['face_calm'] + parts['face_wounded'] + parts['hair_short']
    low, high = 0, .95
    for _ in range(12):
        angle = (low + high) / 2
        neck.matrix_basis = neutral_neck
        bpy.context.view_layer.update()
        pivot('Neck', (1, 0, 0), direction * angle)
        if mesh_floor(faces) > floor + .003:
            low = angle
        else:
            high = angle
    neck.matrix_basis = neutral_neck
    bpy.context.view_layer.update()
    pivot('Neck', (1, 0, 0), direction * low)
    shift = max(0, mesh_floor(faces) - floor - .003)
    neck.matrix_basis = neutral_neck
    bpy.context.view_layer.update()
    return direction * low, shift


def tilt_head(angle, lower=0):
    pivot('Neck', (1, 0, 0), angle)
    if lower:
        head = bones['Head'].matrix.copy()
        head.translation -= world_to_rig @ Vector((0, 0, lower))
        change_bone('Head', head)


def grounded_back(joints, support):
    fall_back(joints, support)
    aim('Abdomen', (0, 1, -.05), 'Torso')
    aim('Torso', (0, 1, -.045), 'Chest')
    aim('Chest', (0, 1, -.05), 'Neck')
    aim('Neck', (0, 1, -.11), 'Head')
    head = bones['Head']
    head_pose = Matrix.Rotation(-math.pi / 2, 4, world_to_rig @ Vector((1, 0, 0))) @ head.bone.matrix_local
    head_pose.translation = head.head
    change_bone('Head', head_pose)
    aim('Head', (0, 1, 0))
    floor = mesh_floor(parts['top_sweater'] + parts['bottom_jeans'])
    world = rig.matrix_world
    for side, sign in sides:
        arm, forearm, wrist = ('UpperArm.' + side, 'LowerArm.' + side, 'Wrist.' + side)
        # Локти опираются на пол, кисти продолжают линию предплечий у боков куртки.
        aim_at_height(arm, forearm, (sign * (.86 if side == 'L' else 1), -1, 0), floor + .040)
        aim_at_height(forearm, wrist, (sign * .16, -1, 0), floor + .036)
        palm_angle = .50 if side == 'L' else .37
        palm = Matrix.Rotation(-sign * (math.pi / 2 - .10), 4,
                               world_to_rig @ Vector((0, 0, 1))) @ bones[wrist].bone.matrix_local
        palm.translation = bones[wrist].head
        change_bone(wrist, palm)
        pivot(wrist, (0, -1, 0), sign * palm_angle)
        for finger, flex in [('Index', .14), ('Middle', .20), ('Ring', .27), ('Pinky', .34)]:
            for segment, scale in [(1, .42), (2, 1), (3, .65)]:
                name = f'{finger}{segment}.{side}'
                bones[name].matrix_basis = Matrix.Identity(4)
                bpy.context.view_layer.update()
                pivot(name, (math.cos(palm_angle), 0, -sign * math.sin(palm_angle)), flex * scale)
        pivot('Thumb1.' + side, (0, 0, 1), sign * .24)

    floor = mesh_floor(parts['top_sweater'] + parts['top_gorka'] + parts['bottom_jeans']
                       + parts['bottom_gorka'] + parts['base'][:1])
    for side, sign in sides:
        thigh, shin = 'UpperLeg.' + side, 'LowerLeg.' + side
        hip, knee, ankle = world @ bones[thigh].head, world @ bones[shin].head, world @ bones[shin].tail
        leg_reach = (knee - hip).length + (ankle - knee).length
        ankle_target = Vector((hip.x + sign * .075, hip.y, floor + .08))
        toe = Vector((sign * (.31 if side == 'L' else .39), -.44, .84))
        for _ in range(4):
            leg_extension = .004 if side == 'L' else .012
            ankle_target.y = hip.y - math.sqrt(max(0, (leg_reach - leg_extension) ** 2
                                                   - .075 ** 2 - (ankle_target.z - hip.z) ** 2))
            aim_chain(thigh, shin, None, ankle_target, Vector((sign * .15, .15, 1)))
            match_boot(side, world @ bones[shin].tail, toe)
            ankle_target.z += floor + .004 - boot_levels()[side]


def back_head_drop(landed):
    frames = []
    for step in range(6):
        for bone in bones:
            bone.matrix_basis = landed[bone.name]
        bpy.context.view_layer.update()
        pivot('Neck', (1, 0, 0), .19 * (1 - ease(step, 0, 6)))
        frames.append({bone.name: bone.matrix_basis.copy() for bone in bones})
    return [*frames, landed]


def fit_contact_frames(frames, limbs=False, fit_head=True, head_parts=('face_calm',)):
    for bone in bones:
        bone.matrix_basis = frames[-1][bone.name]
    bpy.context.view_layer.update()
    # Часть вершин лица зависит и от шеи, поэтому уточняем поправку по деформированной сетке.
    shifts = {}
    if fit_head:
        head = bones['Head']
        head_shift = 0
        head_meshes = [mesh for part in head_parts for mesh in parts[part]]
        for _ in range(4):
            gap = mesh_floor(head_meshes)
            head_shift += gap
            head_pose = head.matrix.copy()
            head_pose.translation -= world_to_rig @ Vector((0, 0, gap))
            change_bone('Head', head_pose)
        shifts['Head'] = head_shift
    if limbs:
        hands, boots_at_rest = side_levels(parts['base'][0]), boot_levels()
        for side, _ in sides:
            shifts['Wrist.' + side] = hands[side]
            shifts['Foot.' + side] = boots_at_rest[side]
    fitted = []
    for index, frame in enumerate(frames):
        for bone in bones:
            bone.matrix_basis = frame[bone.name]
        bpy.context.view_layer.update()
        weight = index / (len(frames) - 1) if len(frames) > 1 else 1
        for name, height in shifts.items():
            bone_pose = bones[name].matrix.copy()
            shift = height * weight if name == 'Head' else max(0, height)
            bone_pose.translation -= world_to_rig @ Vector((0, 0, shift))
            change_bone(name, bone_pose)
        fitted.append({bone.name: bone.matrix_basis.copy() for bone in bones})
    return fitted


def canvas_textures():
    """Бесшовное полотняное плетение с небольшими перепадами оттенка."""
    size, pitch = 256, 8
    rng = random.Random(53)
    ridge = [math.sin(math.pi * (i + .5) / pitch) ** 2 for i in range(pitch)]
    warp_yarn = [rng.uniform(.84, 1.08) for _ in range(size // pitch)]
    weft_yarn = [rng.uniform(.84, 1.08) for _ in range(size // pitch)]
    height = []
    for y in range(size):
        for x in range(size):
            warp = ridge[x % pitch] * warp_yarn[x // pitch]
            weft = ridge[y % pitch] * weft_yarn[y // pitch]
            if (x // pitch + y // pitch) % 2:
                height.append(max(warp, weft * .55))
            else:
                height.append(max(warp * .55, weft))
    return cloth_images('Плетение ткани', 'Рельеф ткани', size, height, rng, .87, .10)


def cloth_images(color_name, normal_name, size, height, rng, base, contrast):
    tone, relief = array('f'), array('f')
    for y in range(size):
        for x in range(size):
            gray = min(1, base + contrast * height[y * size + x] + rng.uniform(-.023, .023))
            tone.extend((gray, gray, gray, 1))
            dx = height[y * size + (x + 1) % size] - height[y * size + (x - 1) % size]
            dy = height[((y + 1) % size) * size + x] - height[((y - 1) % size) * size + x]
            nx, ny, nz = Vector((-dx * .7, -dy * .7, 1)).normalized()
            relief.extend((.5 + nx / 2, .5 + ny / 2, .5 + nz / 2, 1))
    images = []
    for name, pixels, space in [(color_name, tone, 'sRGB'), (normal_name, relief, 'Non-Color')]:
        image = bpy.data.images.new(name, width=size, height=size, alpha=False)
        image.colorspace_settings.name = space
        image.pixels.foreach_set(pixels)
        image.pack()
        images.append(image)
    return images


def knit_textures():
    size, pitch = 256, 16
    rng = random.Random(61)
    yarn = [rng.uniform(.86, 1.08) for _ in range(size // pitch)]
    height = []
    for y in range(size):
        row = (y % pitch + .5) / pitch
        # Две ножки петли сходятся внизу и расходятся к следующему ряду.
        width = .08 + .26 * row
        for x in range(size):
            across = abs((x % pitch + .5) / pitch - .5)
            stitch = math.exp(-((across - width) / .095) ** 2)
            height.append(stitch * (.76 + .24 * math.sin(math.pi * row)) * yarn[x // pitch])
    return cloth_images('Мелкая вязка', 'Рельеф вязки', size, height, rng, .89, .085)


def denim_textures():
    size, pitch = 256, 8
    rng = random.Random(71)
    ridge = [math.sin(math.pi * (i + .5) / pitch) ** 2 for i in range(pitch)]
    yarn = [rng.uniform(.88, 1.08) for _ in range(size // pitch)]
    height = []
    for y in range(size):
        for x in range(size):
            warp = ridge[x % pitch] * yarn[x // pitch]
            weft = ridge[y % pitch]
            # Саржа 3/1. Переплетение смещается на одну нить в каждом ряду.
            warp_over = (x // pitch - y // pitch) % 4 != 0
            height.append(max(warp, weft * .45) if warp_over else max(warp * .4, weft))
    return cloth_images('Саржа денима', 'Рельеф денима', size, height, rng, .90, .095)


def leather_textures():
    size, pitch = 256, 8
    rng = random.Random(97)
    cells = size // pitch
    grain = [[(rng.uniform(.13, .87), rng.uniform(.13, .87)) for _ in range(cells)] for _ in range(cells)]
    height = []
    for y in range(size):
        for x in range(size):
            u, v = x / pitch, y / pitch
            col, row = int(u), int(v)
            distances = sorted((u - gx - grain[gy % cells][gx % cells][0])**2
                               + (v - gy - grain[gy % cells][gx % cells][1])**2
                               for gy in range(row - 1, row + 2) for gx in range(col - 1, col + 2))
            # Нерегулярные мелкие поры, глубже на границах зёрен.
            height.append(min(1, (distances[1] - distances[0]) * 3) * (.85 + .15 * math.sin(u * math.tau / cells)))
    return cloth_images('Зерно кожи плаща', 'Рельеф кожи плаща', size, height, rng, .88, .105)


cloth_uv_scale = {'top_gorka': 8, 'bottom_gorka': 8, 'top_sweater': 9, 'bottom_jeans': 10,
                  'bottom_tactical': 9, 'bottom_hunting': 8, 'top_leather_coat': 6, 'top_trench_coat': 8}
canvas_maps = canvas_textures()
knit_maps = knit_textures()


def bake_material(name, cloth_maps=None, normal_strength=.28, roughness=None):
    result = material(name, 'ffffff', roughness if roughness is not None else .98 if cloth_maps else .88)
    nodes, links = result.node_tree.nodes, result.node_tree.links
    shader = nodes.get('Principled BSDF')
    attribute = nodes.new('ShaderNodeVertexColor')
    attribute.layer_name = 'Color'
    color_socket = attribute.outputs['Color']
    if cloth_maps:
        tone = nodes.new('ShaderNodeTexImage')
        tone.image = cloth_maps[0]
        multiply = nodes.new('ShaderNodeMix')
        multiply.data_type = 'RGBA'
        multiply.blend_type = 'MULTIPLY'
        multiply.inputs[0].default_value = 1
        links.new(color_socket, multiply.inputs[6])
        links.new(tone.outputs['Color'], multiply.inputs[7])
        color_socket = multiply.outputs[2]
        relief = nodes.new('ShaderNodeTexImage')
        relief.image = cloth_maps[1]
        normal = nodes.new('ShaderNodeNormalMap')
        normal.inputs['Strength'].default_value = normal_strength
        links.new(relief.outputs['Color'], normal.inputs['Color'])
        links.new(normal.outputs['Normal'], shader.inputs['Normal'])
        shader.inputs['Specular IOR Level'].default_value = .25
    links.new(color_socket, shader.inputs['Base Color'])
    return result


baked_materials = {
    'base': bake_material('field_base'),
    'top_sweater': bake_material('sweater_fabric', knit_maps, .31),
    'top_gorka': bake_material('gorka_jacket', canvas_maps),
    'top_leather_coat': bake_material('coat_leather', leather_textures(), .30, .61),
    'top_trench_coat': bake_material('trench_fabric', canvas_maps, .21, .91),
    'bottom_jeans': bake_material('jeans_fabric', denim_textures(), .27),
    'bottom_gorka': bake_material('gorka_trousers', canvas_maps),
    'bottom_tactical': bake_material('tactical_fabric', canvas_maps, .24),
    'bottom_hunting': bake_material('hunting_fabric', canvas_maps, .27),
    'face_calm': bake_material('field_face'),
    'hair_short': bake_material('hair_color'),
}
baked_materials['face_wounded'] = baked_materials['face_calm']
ear_material = bake_material('field_ear')
coat_buttons = {
    'top_leather_coat': material('coat_buttons', '797570', .58),
    'top_trench_coat': material('trench_buttons', '897451', .66),
}


def balaclava_face_offset(point):
    # Кончик носа под тканью. Переносица, видимая в прорези, сохраняет исходную форму.
    height = ease(point.z, 1.610, 1.640) * (1 - ease(point.z, 1.650, 1.683))
    return .018 * height * (1 - ease(abs(point.x), .020, .052)) * ease(-point.y, .145, .163)


def export_part(kind, sources):
    copies = []
    for source in sources:
        copy = source.copy()
        copy.data = source.data.copy()
        scene.collection.objects.link(copy)
        mesh = copy.data
        palette = [tuple(m.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value)
                   for m in mesh.materials]
        if kind == 'top_sweater':
            palette = [(1, 1, 1, 1), (.72, .72, .72, 1)]
        elif kind == 'bottom_jeans':
            palette = [(1, 1, 1, 1), (.74, .74, .74, 1)]
        elif kind in ('bottom_tactical', 'bottom_hunting'):
            shade = .59 if kind == 'bottom_tactical' else .71
            palette = [(1, 1, 1, 1), (shade, shade, shade, 1), (.45, .45, .45, 1)]
        elif kind == 'hair_short':
            palette = [(1, 1, 1, 1)] * len(palette)
        elif kind in coat_buttons:
            palette = [(1, 1, 1, 1), (.88, .88, .88, 1), (.69, .69, .69, 1), (1, 1, 1, 1)]
        attr = mesh.color_attributes.new(name='Color', type='BYTE_COLOR', domain='CORNER')
        mesh.color_attributes.active_color = attr
        finishes = [polygon.material_index for polygon in mesh.polygons]
        mesh.materials.clear()
        mesh.materials.append(baked_materials[kind])
        is_face = kind in ('face_calm', 'face_wounded')
        if is_face:
            mesh.materials.append(ear_material)
        world = copy.matrix_world
        if kind in coat_buttons:
            mesh.materials.append(coat_buttons[kind])
        for polygon, finish in zip(mesh.polygons, finishes):
            for index in polygon.loop_indices:
                attr.data[index].color = palette[finish]
            polygon.material_index = int(
                (kind in coat_buttons and finish == 3)
                or (is_face and all(is_ear_point(world @ mesh.vertices[i].co) for i in polygon.vertices))
            )
        if kind in cloth_uv_scale:
            scale = cloth_uv_scale[kind]
            uv = mesh.uv_layers.new(name='Cloth')
            for polygon in mesh.polygons:
                axis = max(range(3), key=lambda axis: abs(polygon.normal[axis]))
                u, v = (axis + 1) % 3, (axis + 2) % 3
                if kind == 'top_sweater' and abs(polygon.center.x) > .28 and axis != 0:
                    # На рукаве столбики петель идут вдоль руки в позе привязки.
                    u, v = 3 - axis, 0
                elif kind in ('top_sweater', 'bottom_jeans') and axis == 1:
                    u, v = 0, 2
                for index in polygon.loop_indices:
                    point = mesh.vertices[mesh.loops[index].vertex_index].co
                    uv.data[index].uv = (point[u] * scale, point[v] * scale)
        if kind == 'hair_short':
            copy.shape_key_add(name='Basis')
            fitted = copy.shape_key_add(name='soft_headwear_fit')
            world = source.matrix_world
            local = world.inverted()
            for vertex in fitted.data:
                point = world @ vertex.co
                amount = ease(point.z, 1.747, 1.787)
                point.x *= 1 - .14 * amount
                point.y = -.041 + (point.y + .041) * (1 - .14 * amount)
                point.z -= .011 * ease(point.z, 1.785, 1.830)
                vertex.co = local @ point
        copies.append(copy)
    bpy.ops.object.select_all(action='DESELECT')
    for copy in copies:
        copy.select_set(True)
    bpy.context.view_layer.objects.active = copies[0]
    if len(copies) > 1:
        bpy.ops.object.join()
    result = bpy.context.object
    result.name = kind
    if kind in ('face_calm', 'face_wounded'):
        result.shape_key_add(name='Basis')
        fitted = result.shape_key_add(name='under_balaclava')
        world = result.matrix_world
        local = world.inverted()
        for vertex in fitted.data:
            point = world @ vertex.co
            point.y += balaclava_face_offset(point)
            vertex.co = local @ point
    return result


def mp50_model():
    existing = set(scene.objects)
    images = set(bpy.data.images)
    bpy.ops.import_scene.gltf(filepath=str(root / 'app/static/models/equipment/mp50.glb'))
    imported = set(scene.objects) - existing
    mask = next(obj for obj in imported if obj.type == 'MESH')
    points = [mask.matrix_world @ vertex.co for vertex in mask.data.vertices]
    low = Vector(tuple(min(point[i] for point in points) for i in range(3)))
    high = Vector(tuple(max(point[i] for point in points) for i in range(3)))
    center = (low + high) * .5
    size = high - low
    scale = Matrix.Diagonal((.218 / size.x, .289 / size.y, .270 / size.z, 1))
    mask.data.transform(Matrix.Translation((0, -.095, 1.674)) @ scale
                        @ Matrix.Translation(-center) @ mask.matrix_world)
    # Задняя часть ремней огибает голову и волосы, положение линз и фильтров остаётся прежним.
    for vertex in mask.data.vertices:
        rear = ease(vertex.co.y, -.10, .049)
        vertex.co.x *= 1 + .36 * rear
        vertex.co.y += .046 * rear
        hair_depth = .079 + .012 * ease(vertex.co.z, 1.674, 1.740)
        vertex.co.y -= .85 * max(0, vertex.co.y - hair_depth)
        # Кромка над окулярами прилегает к скальпу без просвета у висков.
        brow = ease(vertex.co.z, 1.729, 1.772) * ease(-vertex.co.y, .073, .129)
        vertex.co.y -= .013 * brow
        vertex.co.x *= 1 + .07 * brow
        vertex.co.z += .017 * brow * ease(vertex.co.z, 1.757, 1.793)
    mask.shape_key_add(name='Basis')
    bare_head_strap = mask.shape_key_add(name='bare_head_strap')
    for vertex in bare_head_strap.data:
        scalp_depth = .054 + .010 * ease(vertex.co.z, 1.674, 1.735)
        vertex.co.y -= .85 * max(0, vertex.co.y - scalp_depth)
    fitted = mask.shape_key_add(name='under_headwear')
    for vertex in fitted.data:
        x, y, z = vertex.co
        brow = ease(z, 1.752, 1.794) * (1 - ease(abs(x), .088, .125))
        vertex.co.y += max(0, -.169 - y) * brow
        vertex.co.z -= .010 * brow
    mask.parent = rig
    mask.matrix_world = Matrix.Identity(4)
    mask.name = 'gear_mp50'
    mask.vertex_groups.new(name='Head').add(list(range(len(mask.data.vertices))), 1, 'REPLACE')
    mask.modifiers.new('Общий скелет', 'ARMATURE').object = rig
    albedos = {link.from_node.image for material in mask.data.materials
               for node in material.node_tree.nodes if node.type == 'BSDF_PRINCIPLED'
               for link in node.inputs['Base Color'].links if link.from_node.type == 'TEX_IMAGE'}
    packed_maps = {node.image for material in mask.data.materials for node in material.node_tree.nodes
                   if node.type == 'TEX_IMAGE' and node.image not in albedos
                   and any(link.to_node.type in {'SEPARATE_COLOR', 'SEPRGB'} for link in node.outputs['Color'].links)}
    for obj in imported - {mask}:
        bpy.data.objects.remove(obj, do_unlink=True)
    for image in set(bpy.data.images) - images:
        width, height = image.size
        if max(width, height) > 256:
            scale = 256 / max(width, height)
            image.scale(round(width * scale), round(height * scale))
        if image in albedos:
            # Нейтральная серая маска. Сохраняем светотень и рисунок исходной карты.
            pixels = array('f', image.pixels[:])
            for index in range(0, len(pixels), 4):
                red, green, blue = pixels[index:index + 3]
                tone = .2126 * red + .7152 * green + .0722 * blue
                tone = .065 + .88 * tone + .22 * min(1, tone / .12) * (1 - tone) ** 2
                pixels[index:index + 3] = array('f', (tone, tone, tone))
            image.pixels.foreach_set(pixels)
        elif image in packed_maps:
            # У резины слабее металлические блики и запечённое затемнение.
            pixels = array('f', image.pixels[:])
            for index in range(0, len(pixels), 4):
                pixels[index] = .36 + .64 * pixels[index]
                pixels[index + 2] *= .34
            image.pixels.foreach_set(pixels)
        image.pack()
    return mask


def mp50_ear_seals():
    rubber = material('mp50_side_rubber', '444645', .77)
    rubber.use_backface_culling = False
    seals = Garment('gear_mp50_ear_seals', [rubber])
    rows = (
        ((.081, -.156, 1.758), (.102, -.113, 1.750), (.095, -.050, 1.733)),
        ((.092, -.147, 1.702), (.116, -.077, 1.699), (.099, -.033, 1.690)),
        ((.097, -.141, 1.672), (.112, -.074, 1.670), (.095, -.037, 1.658)),
        ((.085, -.137, 1.645), (.096, -.083, 1.640), (.081, -.056, 1.640)),
    )
    for sign in (-1, 1):
        for a, b in zip(rows, rows[1:]):
            for i in range(len(a) - 1):
                points = [(sign * x, y, z) for x, y, z in (a[i], a[i + 1], b[i + 1], b[i])]
                seals.quad(points if sign > 0 else list(reversed(points)), [{'Head': 1}] * 4, 0)
    obj = seals.object()
    obj.parent = rig
    obj.matrix_world = Matrix.Identity(4)
    return obj


def gas_mask_models():
    head = {'Head': 1}
    strap = material('gasmask_strap', '494a45', .91)
    strap.use_backface_culling = False
    trim = material('gasmask_trim', '4d514e', .64)
    dark = material('gasmask_grille', '393e3b', .86)
    steel = material('gasmask_steel', '777b73', .55)
    green = material('gasmask_filter_olive', '697849', .72)
    blue = material('gasmask_filter_blue', '4c6167', .68)
    brass = material('gasmask_ochre_trim', '88704c', .56)
    clear = material('gasmask_clear_lens', 'adc2c1', .26)
    smoke = material('gasmask_smoked_lens', '82969a', .25)
    for glass, opacity in ((clear, .27), (smoke, .58)):
        glass.surface_render_method = 'BLENDED'
        glass.use_backface_culling = True
        glass.node_tree.nodes.get('Principled BSDF').inputs['Alpha'].default_value = opacity
    rng = random.Random(379)
    grain = cloth_images('Резина противогазов', 'Рельеф резины противогазов', 64,
                         [rng.random() for _ in range(64 * 64)], rng, .94, .045)
    face_mesh = parts['face_calm'][0]
    skin_points, skin_faces = [], []
    for part in (face_mesh, parts['face_calm'][2]):
        offset = len(skin_points)
        skin_points.extend(part.matrix_world @ v.co for v in part.data.vertices)
        skin_faces.extend([offset + i for i in p.vertices] for p in part.data.polygons
                          if part.data.materials[p.material_index].name == 'Skin')
    skin = BVHTree.FromPolygons(skin_points, skin_faces)
    scalp_top = max(v.z for v in skin_points)
    output = []
    for style in ('respirator', 'gp5', 'gp9v', 'gp7', 'sp10'):
        rubber = bake_material('gasmask_' + style + '_rubber', grain, .10, .68)
        materials = [rubber, strap, trim, dark, steel, green, blue, brass, clear, smoke]
        mask = Garment('gear_gasmask_' + style, materials)
        bare_points = {}
        rim = 7 if style in {'sp10', 'gp9v'} else 4 if style == 'gp5' else 2

        def ring(points):
            start = len(mask.vertices)
            mask.vertices.extend(points)
            mask.weights.extend([head] * len(points))
            return list(range(start, len(mask.vertices)))

        def face(indices, finish):
            mask.faces.append(tuple(indices))
            mask.finish.append(finish)

        def bridge(a, b, finish, closed=True):
            for i in range(len(a) if closed else len(a) - 1):
                j = (i + 1) % len(a)
                face((a[i], a[j], b[j], b[i]), finish)

        def profile(center, axis, radius, rows, segments=20):
            normal = Vector(axis).normalized()
            across = normal.cross(Vector((0, 0, 1))).normalized()
            vertical = normal.cross(across)
            sections = []
            for offset, scale, _ in rows:
                sections.append(ring([Vector(center) + normal * offset + scale * (
                    across * radius[0] * math.cos(math.tau * i / segments)
                    + vertical * radius[1] * math.sin(math.tau * i / segments)) for i in range(segments)]))
            face(reversed(sections[0]), rows[0][2])
            for a, b, (_, _, finish) in zip(sections, sections[1:], rows[1:]):
                bridge(a, b, finish)
            face(sections[-1], rows[-1][2])

        def cartridge(center, axis, radius, depth, finish, ribs=False):
            profile(center, axis, radius, ((-.014, .74, 2), (0, .89, 2), (.004, 1, finish),
                    (.011, 1, finish), (depth - .006, .96, finish), (depth, .90, finish)))
            normal = Vector(axis).normalized()
            grille = Vector(center) + normal * depth
            profile(grille, axis, (radius[0] * .76, radius[1] * .76),
                    ((-.002, 1, finish), (.002, 1, finish), (.004, .87, 3)))
            across = normal.cross(Vector((0, 0, 1))).normalized()
            up = normal.cross(across)
            for step in (-2, -1, 0, 1, 2):
                p = grille + up * (radius[1] * .236 * step) + normal * .0045
                profile(p, axis, (radius[0] * (.59 - .055 * abs(step)), .0015),
                        ((-.001, 1, finish), (.001, .94, finish)), 8)
            if ribs:
                for i in range(16):
                    a = i * math.tau / 16
                    radial = math.cos(a) * across + math.sin(a) * up
                    pos = Vector(center) + normal * (.55 * depth) + radial * (radius[0] * .97)
                    profile(pos, axis, (.0028, .0028), ((-.32 * depth, 1, rim), (.30 * depth, .75, finish)), 6)

        def against_head(angle, z, clearance):
            center = Vector((0, -.045, z))
            ray = Vector((math.sin(angle), -math.cos(angle), 0))
            point, _, _, _ = skin.ray_cast(center + .25 * ray, -ray, .25)
            return point + clearance * ray if point is not None else center + (.078 + clearance) * ray

        def body_y(x, z):
            nose = .025 * (1 - ease(abs(x), .012, .072)) * ease(z, 1.585, 1.668)
            return -.170 + .064 * (x / .117)**2 + .023 * ease(1.64 - z, 0, .072) - nose

        def belt(points, width, anchors=None):
            rows = []
            for i, (angle, z) in enumerate(points):
                tangent = Vector(points[min(i + 1, len(points) - 1)]) - Vector(points[max(i - 1, 0)])
                across = Vector((-tangent.y, tangent.x * .11)).normalized()
                coords = [(angle + across.x * side * width / .10, z + across.y * side * width)
                          for side in (-.5, .5)]
                row = ring([against_head(a, h, .020) for a, h in coords])
                bare_points.update((idx, against_head(a, h, .008)) for idx, (a, h) in zip(row, coords))
                rows.append(row)
            if anchors:
                left, right = [ring([(x, y, z - width * .5), (x, y, z + width * .5)]) for x, y, z in anchors]
                rows = [left, *rows, right]
            for a, b in zip(rows, rows[1:]):
                bridge(a, b, 1, closed=False)

        if style != 'gp5':
            # Две ленты огибают голову. Сдвиг под шлем сокращает запас под волосы.
            upper = ((1.34, 1.703), (1.85, 1.727), (2.42, 1.748))
            lower = ((1.19, 1.630), (1.82, 1.654), (2.39, 1.668))
            for half, rear in ((upper, 1.752), (lower, 1.678)):
                anchors = [Vector((sign * .077, -.139, 1.636 if half == upper else 1.613))
                           for sign in (1, -1)] if style == 'respirator' else None
                belt([*half, (math.pi, rear), *((math.tau - a, z) for a, z in reversed(half))], .014, anchors)

        if style == 'respirator':
            # Кромка обхватывает подбородок, а выпуклая оболочка проходит перед носом.
            outline = ((-.091, 1.642), (-.071, 1.663), (-.035, 1.682), (-.013, 1.698), (0, 1.701),
                       (.013, 1.698), (.035, 1.682), (.071, 1.663), (.091, 1.642), (.089, 1.612),
                       (.068, 1.585), (.034, 1.565), (0, 1.561), (-.034, 1.565), (-.068, 1.585),
                       (-.089, 1.612))
            outline = tuple(reversed(outline))

            def face_depth(x, z):
                hit, _, _, _ = skin.ray_cast(Vector((x, -.33, z)), Vector((0, 1, 0)), .31)
                return hit.y if hit is not None else body_y(x, z) + .022

            def sample_outline(points):
                return [(x + (u - x) * t, z + (v - z) * t)
                        for (x, z), (u, v) in zip(points, points[1:] + points[:1])
                        for t in (0, .5)]

            outline = sample_outline(outline)
            center_z = 1.630
            seal = ring([(x, face_depth(x, z) + (.028 if z < 1.607 else .002),
                          z - .009 * (1 - ease(z, 1.568, 1.603))) for x, z in outline])
            previous = seal
            for fraction, clearance in ((1, .006), (.80, .013), (.52, .018), (.24, .021)):
                points = [(x * fraction, center_z + (z - center_z) * fraction) for x, z in outline]
                section = ring([(x, min(face_depth(x, z) - clearance,
                                        body_y(x, z) + .026 * fraction**2 - .009), z) for x, z in points])
                bridge(previous, section, 0)
                previous = section
            center = ring([(0, min(face_depth(0, center_z) - .022, body_y(0, center_z) - .009), center_z)])[0]
            for i, point in enumerate(previous):
                face((point, previous[(i + 1) % len(previous)], center), 0)
            for sign in (-1, 1):
                cartridge((sign * .074, -.152, 1.619), (sign * .72, -1, -.18),
                          (.034, .036), .026, 6, ribs=True)
            profile((0, -.183, 1.603), (0, -1, -.30), (.021, .018),
                    ((-.010, .83, 2), (.004, 1, 2), (.010, .78, 3)))
        elif style == 'gp5':
            # Круглые окуляры вшиты в общую сетку шлем-маски.
            angles = [i * math.tau / 64 for i in range(64)]
            heights = (1.552, 1.570, 1.595, 1.620, 1.645, 1.665, 1.680, 1.700, 1.720,
                       1.739, 1.757, 1.778, 1.797, 1.812, scalp_top + .002, scalp_top + .006)
            rows = []
            for z in heights:
                points = []
                for a in angles:
                    front = max(0, math.cos(a))
                    actual_z = z + .022 * (1 - front) * (1 - ease(z, 1.555, 1.624))
                    center = Vector((0, -.046, actual_z))
                    direction = Vector((math.sin(a), -math.cos(a), 0))
                    hit, _, _, _ = skin.ray_cast(center + .25 * direction, -direction, .25)
                    chin = .024 * front**6 * (1 - ease(z, 1.563, 1.617))
                    radius = (hit - center).length + .008 if hit is not None else .052 + .030 * front
                    if z >= 1.778:
                        crown = .070 * math.sqrt(max(.012, 1 - ((z - 1.755) / (scalp_top + .006 - 1.755))**2))
                        radius = max(crown, (hit - center).length + .005) if hit is not None else crown
                    radius += chin + .013 * front**2 * ease(z, 1.618, 1.665) * (1 - ease(z, 1.732, 1.756))
                    points.append(center + direction * radius)
                rows.append(ring(points))
            eye_spans = ((1, 10), (54, 63))
            for row, (a, b) in enumerate(zip(rows, rows[1:])):
                for i in range(64):
                    if 5 <= row < 9 and any(start <= i < end for start, end in eye_spans):
                        continue
                    j = (i + 1) % 64
                    face((a[i], a[j], b[j], b[i]), 0)
            face(rows[-1], 0)
            hem = ring([(x * .87, -.046 + (y + .046) * .88, z + .004)
                        for x, y, z in (mask.vertices[i] for i in rows[0])])
            bridge(hem, rows[0], 0)

            for (start, end), sign in zip(eye_spans, (1, -1)):
                outline = [*rows[5][start:end], *(rows[i][end] for i in range(5, 9)),
                           *reversed(rows[9][start + 1:end + 1]), *(rows[i][start] for i in range(9, 5, -1))]
                cx, cz = sign * .048, 1.701
                center = Vector((cx, -.183, cz))
                radial = [(math.cos(a), math.sin(a)) for a in (
                    math.atan2((mask.vertices[i].z - cz) / .036, (mask.vertices[i].x - cx) / .043)
                    for i in outline)]
                gasket = ring([center + Vector((.041 * x, .008, .036 * z)) for x, z in radial])
                bezel = ring([center + Vector((.036 * x, -.008, .032 * z)) for x, z in radial])
                glass_edge = ring([center + Vector((.030 * x, -.005, .027 * z)) for x, z in radial])
                bridge(outline, gasket, 0)
                bridge(gasket, bezel, rim)
                bridge(bezel, glass_edge, 2)
                glass_center = ring([center + Vector((0, -.006, 0))])[0]
                for i, point in enumerate(glass_edge):
                    face((point, glass_edge[(i + 1) % len(glass_edge)], glass_center), 8)
            cartridge((0, -.170, 1.599), (0, -.62, -.78), (.038, .038), .046, 5)
        else:
            # Контуры окон построены с проёмами, чтобы через стекло были видны глаза.
            def window(cx, cz, rx, rz, bounds, combined=False):
                xmin, xmax, zmin, zmax = bounds
                angles = [i * math.tau / 20 for i in range(20)]
                corners = ((xmin, zmin), (xmax, zmin), (xmax, zmax), (xmin, zmax))
                corner_angles = [math.atan2((z - cz) / rz, (x - cx) / rx) % math.tau for x, z in corners]
                angles = sorted(set(round(a, 6) for a in (*angles, *corner_angles)))
                outside, seal, bezel, aperture = [], [], [], []
                for a in angles:
                    dx, dz = math.cos(a), math.sin(a)
                    if combined:
                        dx, dz = math.copysign(abs(dx)**.63, dx), math.copysign(abs(dz)**.66, dz)
                    x, z = cx + dx * rx, cz + dz * rz
                    if combined:
                        z -= .010 * abs(dx)**2 * max(0, dz)
                    # Внешний контур строится на прямых лучах, чтобы соседние панели сходились без щели.
                    sx, sz = math.cos(a) * rx, math.sin(a) * rz
                    scale = min((xmax - cx if sx > 0 else xmin - cx) / sx if sx else math.inf,
                                (zmax - cz if sz > 0 else zmin - cz) / sz if sz else math.inf)
                    ox, oz = cx + sx * scale, cz + sz * scale
                    outside.append(Vector((ox, body_y(ox, oz) + .006, oz)))
                    seal.append((x + .009 * dx, body_y(x, z) - .001, z + .009 * dz))
                    bezel.append((x + .004 * dx, body_y(x, z) - .010, z + .004 * dz))
                    aperture.append((x, body_y(x, z) - .008, z))
                outer, outer_rim, inner_rim, inner = map(ring, (outside, seal, bezel, aperture))
                bridge(outer, outer_rim, 0)
                bridge(outer_rim, inner_rim, rim)
                bridge(inner_rim, inner, 2)
                lens = ring([(x, y + .001, z) for x, y, z in aperture])
                center = ring([(cx, body_y(cx, cz) - .013, cz)])[0]
                for i in range(len(lens)):
                    face((lens[i], lens[(i + 1) % len(lens)], center), 8 if style in {'gp5', 'gp9v'} else 9)
                return outer, outside

            def side_panels(outer, points, side):
                indices = [i for i, point in enumerate(points) if abs(point.x - side) < 2e-6]
                sign = math.copysign(1, side)
                indices.sort(key=lambda i: points[i].z, reverse=sign < 0)
                edge = [outer[i] for i in indices]
                cheek = ring([against_head(sign * 1.04, points[i].z, .022) for i in indices])
                ear_front = ring([Vector((sign * .113, -.077, points[i].z)) for i in indices])
                back = ring([against_head(sign * 1.93, points[i].z, .011) for i in indices])
                bridge(edge, cheek, 0, closed=False)
                bridge(cheek, ear_front, 0, closed=False)
                bridge(ear_front, back, 0, closed=False)
                return [mask.vertices[row[0 if sign > 0 else -1]] for row in (cheek, ear_front, back)]

            def brow_seal(outer, points):
                indices = [i for i, point in enumerate(points) if abs(point.z - 1.752) < .0011]
                indices.sort(key=lambda i: points[i].x)
                edge = [outer[i] for i in indices]
                back = ring([(point.x, point.y, 1.758) for point in [
                    against_head(math.asin(max(-1, min(1, points[i].x / .110))), 1.758, .006)
                    for i in indices]])
                bridge(edge, back, 0, closed=False)

            if style == 'gp9v':
                outer, points = window(0, 1.699, .087, .042, (-.104, .104, 1.647, 1.753), combined=True)
                lower_sides = [side_panels(outer, points, side) for side in (-.104, .104)]
                brow_seal(outer, points)
            else:
                radius_x, radius_z = (.035, .030) if style == 'gp7' else (.038, .035)
                lower_sides = []
                for sign in (-1, 1):
                    side = sign * .104
                    outer, points = window(sign * .047, 1.701, radius_x, radius_z,
                                           (*sorted((0, side)), 1.651, 1.752))
                    lower_sides.append(side_panels(outer, points, side))
                    brow_seal(outer, points)

            # Выраженные скулы и подбородок переходят в кромку лицевой части.
            lower = ((1.652, .104, .010), (1.628, .103, .005), (1.607, .091, .009),
                     (1.582, .072, .022), (1.568, .051, .036))

            def jaw(a, z, width, setback):
                side = abs(a) / 1.57
                x = width * math.sin(a)
                z += .007 * side**4
                y = body_y(x, z) + setback + .057 * side**5
                hit, _, _, _ = skin.ray_cast(Vector((x, -.33, z)), Vector((0, 1, 0)), .31)
                if hit is not None:
                    y = min(y, hit.y - (.018 if z < 1.632 else .007))
                return (x, y, z)

            rows = []
            for index, (z, width, setback) in enumerate(lower):
                taper = 1 - index / (len(lower) - 1)
                sides = []
                for sign, side in zip((-1, 1), lower_sides):
                    edge = Vector(jaw(sign * 1.57, z, width, setback))
                    ends = [edge.lerp(Vector(p), taper) for p in side]
                    sides.append(ends)
                rows.append(ring([*reversed(sides[0]), *(jaw((i - 12) * 1.57 / 12, z, width, setback)
                                  for i in range(25)), *sides[1]]))
            for a, b in zip(rows, rows[1:]):
                bridge(b, a, 0, closed=False)
            underside = ring([(x * .92, y + .037 * (1 - ease(abs(x), .031, .051)), z - .010)
                              for x, y, z in (mask.vertices[i] for i in rows[-1])])
            bridge(underside, rows[-1], 0, closed=False)
            if style == 'gp9v':
                cartridge((0, -.178, 1.606), (0, -.62, -.78), (.038, .038), .051, 2, ribs=True)
            else:
                side = -.078 if style == 'gp7' else .078
                cartridge((side, -.151, 1.629), (math.copysign(.72, side), -1, -.19),
                          (.046, .046), .035 if style == 'gp7' else .045, 5 if style == 'gp7' else 6, ribs=True)
                profile((0, -.170, 1.620), (0, -1, -.25), (.027, .028),
                        ((-.010, .90, 2), (.005, 1, rim), (.015, .80, rim), (.017, .64, 3)))
                if style == 'gp7':
                    profile((0, -.155, 1.583), (0, -.46, -.89), (.017, .017),
                            ((-.010, 1, 2), (.014, .83, 2), (.021, .73, 3)), 12)

        obj = mask.object()
        mesh = obj.data
        tint = mesh.color_attributes.new(name='Color', type='BYTE_COLOR', domain='CORNER')
        mesh.color_attributes.active_color = tint
        uv = mesh.uv_layers.new(name='GasMask')
        for polygon in mesh.polygons:
            axis = max(range(3), key=lambda i: abs(polygon.normal[i]))
            u, v = (axis + 1) % 3, (axis + 2) % 3
            for index in polygon.loop_indices:
                point = mesh.vertices[mesh.loops[index].vertex_index].co
                tint.data[index].color = (1, 1, 1, 1)
                uv.data[index].uv = (point[u] * 5, point[v] * 5)
        if bare_points:
            obj.shape_key_add(name='Basis')
            fitted = obj.shape_key_add(name='bare_head_strap')
            for index, point in bare_points.items():
                fitted.data[index].co = point
        if style != 'respirator':
            if not bare_points:
                obj.shape_key_add(name='Basis')
            fitted = obj.shape_key_add(name='under_headwear')
            for vertex in fitted.data:
                x, y, z = vertex.co
                # Верх резины заходит под кромку убора, сохраняя посадку окуляров.
                weight = ease(z, 1.726, 1.747) * (1 - ease(z, 1.772, 1.802))
                weight *= 1 - ease(abs(x), .091, .118)
                vertex.co.y += max(0, -.147 - y) * weight
                if style == 'gp5':
                    # Под куполом оставляем зазор на лбу и над макушкой.
                    scalp = ease(z, 1.747, 1.776)
                    vertex.co.x *= 1 - .06 * scalp
                    vertex.co.y += (-.046 - vertex.co.y) * .10 * scalp
                    vertex.co.z -= .008 * ease(z, 1.770, 1.834)
            if style != 'gp5':
                fitted = obj.shape_key_add(name='under_pervoprohodets')
                for vertex in fitted.data:
                    x, y, z = vertex.co
                    brow = ease(z, 1.741 if style == 'gp9v' else 1.736, 1.752)
                    brow *= (1 - ease(abs(x), .078, .110)) * ease(-y, .096, .142)
                    vertex.co.z += (.014 if style == 'gp9v' else .018) * brow
                    vertex.co.y += max(0, (-.157 if style == 'gp9v' else -.171) - y) * brow
        obj.parent = rig
        obj.matrix_world = Matrix.Identity(4)
        output.append(obj)
    return output


def helmet_68g_model():
    paint = material('helmet_68g_olive_paint', '7a7d4b', .51)
    paint.node_tree.nodes.get('Principled BSDF').inputs['Metallic'].default_value = .12
    shell = Garment('gear_helmet_68g', [paint])
    weights = {'Head': 1}
    segments = 40
    # Высота, поперечный радиус, продольный радиус и подъём кромки над лицом.
    profile = [
        (1.699, .127, .155, 1), (1.703, .128, .156, 1), (1.709, .124, .152, 1),
        (1.726, .120, .148, .74), (1.751, .115, .142, .48),
        (1.776, .107, .132, .26), (1.802, .095, .115, .12),
        (1.825, .080, .096, .03), (1.845, .060, .073, 0),
        (1.859, .035, .044, 0), (1.866, .011, .014, 0),
    ]

    def surface(row, angle, inset=0):
        height, width, depth, edge = row
        around, along = math.cos(angle), math.sin(angle)
        rim = .027 * max(0, -along) ** 2 - .016 * max(0, along) ** 2
        # Расширение у лба оставляет место ремням МП-50 под кромкой.
        flare = 1 + .14 * max(0, -along) * edge
        return Vector(((width - inset) * around * flare, -.039 + (depth - inset) * along * flare,
                       height + edge * rim - inset))

    def add_ring(points):
        ring = list(range(len(shell.vertices), len(shell.vertices) + len(points)))
        shell.vertices.extend(points)
        shell.weights.extend([weights] * len(points))
        return ring

    def face(indices):
        shell.faces.append(tuple(indices))
        shell.finish.append(0)

    def bridge(first, second):
        for index in range(len(first)):
            after = (index + 1) % len(first)
            face((first[index], first[after], second[after], second[index]))

    angles = [math.tau * index / segments for index in range(segments)]
    outer = [add_ring([surface(row, angle) for angle in angles]) for row in profile]
    inner = [add_ring([surface(row, angle, .0035) for angle in angles]) for row in profile]
    for lower, higher in zip(outer, outer[1:]):
        bridge(lower, higher)
    for lower, higher in zip(inner, inner[1:]):
        bridge(higher, lower)
    bridge(inner[0], outer[0])
    face(outer[-1])
    face(reversed(inner[-1]))

    # Три маленькие головки заклёпок под той же краской.
    for row, angle in [(profile[5], -2.22), (profile[5], -0.92), (profile[4], math.pi / 2)]:
        point = surface(row, angle)
        normal = Vector((math.cos(angle), math.sin(angle), .3)).normalized()
        tangent = Vector((-normal.y, normal.x, 0)).normalized()
        bitangent = normal.cross(tangent)
        radial = [tangent * math.cos(index * math.tau / 8) + bitangent * math.sin(index * math.tau / 8)
                  for index in range(8)]
        edge = add_ring([point + offset * .0025 - normal * .0004 for offset in radial])
        crown = add_ring([point + offset * .0017 + normal * .0011 for offset in radial])
        bridge(edge, crown)
        face(crown)

    helmet = shell.object()
    helmet.shape_key_add(name='Basis')
    fitted = helmet.shape_key_add(name='over_gp5')
    for vertex in fitted.data:
        _, y, z = vertex.co
        brow = ease(-y, .127, .175) * (1 - ease(z, 1.720, 1.802))
        vertex.co.z += .009 * brow
    helmet.parent = rig
    helmet.matrix_world = Matrix.Identity(4)
    return helmet


def tactical_helmets():
    rng = random.Random(171)
    paint_maps = cloth_images('Шагрень шлема', 'Рельеф шагрени шлема', 64,
                              [rng.random() for _ in range(64 * 64)], rng, .91, .044)
    webbing = bake_material('tactical_helmet_webbing', canvas_maps, .18)
    hardware = bake_material('tactical_helmet_hardware', roughness=.54)
    face = parts['face_calm'][0]
    skin_faces = [p.vertices[:] for p in face.data.polygons if face.data.materials[p.material_index].name == 'Skin']
    skin = BVHTree.FromPolygons([face.matrix_world @ vertex.co for vertex in face.data.vertices], skin_faces)

    def against_head(x, y, z, clearance=.009):
        center = Vector((0, -.046, z))
        direction = (Vector((x, y, z)) - center).normalized()
        hit, _, _, _ = skin.ray_cast(center + direction * .25, -direction, .25)
        return hit + direction * clearance if hit is not None else Vector((x, y, z))

    profiles = {
        'vityaz': (.134, .148, 1.869, 1.745, 1.677, .67, 1.47),
        'udarnik': (.124, .141, 1.861, 1.739, 1.674, 1.92, 2.58),
        'udarnik_m': (.131, .146, 1.867, 1.741, 1.665, .93, 1.78),
        'pervoprohodets': (.130, .146, 1.874, 1.750, 1.663, .89, 1.26),
    }
    # Относительная высота и радиусы колец от кромки до макушки.
    rings = ((0, 1, 1), (.026, 1.004, 1.003), (.069, 1.003, 1),
             (.17, .985, .991), (.34, .946, .960), (.50, .876, .895),
             (.66, .774, .793), (.81, .624, .649), (.92, .448, .478),
             (.98, .233, .260), (1, .036, .042))
    segments = 48
    head = {'Head': 1}
    result = []
    for style, (width, depth, crown, front, low, cut_start, cut_end) in profiles.items():
        paint = bake_material('helmet_' + style + '_shell', paint_maps, .07, .64 if style == 'vityaz' else .71)
        name = 'gear_helmet_' + style
        helmet = Garment(name, [paint, webbing, hardware])

        def edge_height(angle):
            side = abs(math.remainder(angle, math.tau))
            drop = ease(side, cut_start, cut_end)
            if style == 'vityaz':
                return front + (low - front) * drop + .024 * ease(side, 1.84, 2.82)
            if style == 'udarnik':
                return front + .013 * ease(side, .62, 1.35) + (low - front - .013) * drop
            if style == 'pervoprohodets':
                return front + (low - front) * drop + .019 * ease(side, 1.90, 2.63)
            return front + (low - front) * drop + .006 * ease(side, 2.05, 2.85)

        def point(angle, row, outward=0):
            phase, x_scale, y_scale = row
            rim = edge_height(angle)
            lip = .009 * (1 - ease(phase, 0, .22)) * max(0, math.cos(angle))**2
            return Vector(((width * x_scale + outward) * math.sin(angle),
                           -.043 - (depth * y_scale + lip + outward) * math.cos(angle),
                           rim + (crown - rim) * phase))

        def add_ring(points):
            first = len(helmet.vertices)
            helmet.vertices.extend(points)
            helmet.weights.extend([head] * len(points))
            return tuple(range(first, len(helmet.vertices)))

        def add_face(indices, finish):
            helmet.faces.append(tuple(indices))
            helmet.finish.append(finish)

        def bridge(a, b, finish, closed=True):
            count = len(a)
            for index in range(count if closed else count - 1):
                after = (index + 1) % count
                add_face((a[index], a[after], b[after], b[index]), finish)

        angles = [math.tau * index / segments for index in range(segments)]
        outside = [add_ring([point(angle, row) for angle in angles]) for row in rings]
        for index, (lower, upper) in enumerate(zip(outside, outside[1:])):
            finish = 5 if style == 'vityaz' and index < 2 else 3 if index == 0 else 0
            bridge(lower, upper, finish)
        add_face(outside[-1], 0)
        inside = add_ring([point(angle, rings[0], -.006) for angle in angles])
        lining = add_ring([point(angle, rings[3], -.006) for angle in angles])
        bridge(inside, outside[0], 1)
        bridge(lining, inside, 1)
        add_face(reversed(lining), 1)

        def strap(points, width, finish=1):
            centers = [Vector(point) for point in points]
            sections = []
            for index, center in enumerate(centers):
                tangent = (centers[min(index + 1, len(centers) - 1)] - centers[max(0, index - 1)]).normalized()
                outward = Vector((center.x, center.y + .047, -.055 if center.z < 1.59 else 0))
                across = outward.cross(tangent).normalized() * (width / 2)
                depth = tangent.cross(across).normalized() * .0013
                sections.append(add_ring([center - across - depth, center + across - depth,
                                          center + across + depth, center - across + depth]))
            for a, b in zip(sections, sections[1:]):
                bridge(a, b, finish)
            add_face(reversed(sections[0]), finish)
            add_face(sections[-1], finish)

        def stud(angle, row):
            center = point(angle, row)
            normal = Vector((math.sin(angle), -math.cos(angle), .30)).normalized()
            tangent = Vector((-normal.y, normal.x, 0)).normalized()
            bitangent = normal.cross(tangent)
            circle = [tangent * math.cos(i * math.tau / 8) + bitangent * math.sin(i * math.tau / 8)
                      for i in range(8)]
            a = add_ring([center + radial * .0045 - normal * .0008 for radial in circle])
            b = add_ring([center + radial * .0034 + normal * .0022 for radial in circle])
            bridge(a, b, 2)
            add_face(b, 2)

        chin_sides = []
        for sign in (-1, 1):
            front_anchor = point(sign * .91, rings[0], -.002)
            # Ниже кромки ремни прилегают к щеке и огибают нижнюю челюсть.
            join = against_head(sign * .077, -.109, 1.613)
            strap([front_anchor, against_head(sign * .091, -.126, 1.657), join], .014)
            strap([point(sign * 2.04, rings[0], -.002), against_head(sign * .092, -.029, 1.650),
                   against_head(sign * .083, -.071, 1.630), join], .013)
            chin_sides.append([join, against_head(sign * .055, -.126, 1.582),
                               against_head(sign * .029, -.132, 1.563, .013)])
            strap([join + Vector((sign * .003, -.002, -.006)),
                   join + Vector((sign * .005, -.003, .009))], .018, 2)
            stud(sign * 1.10, rings[3])
        chin = against_head(0, -.132, 1.558, .014)
        strap([*chin_sides[0], chin, *reversed(chin_sides[1])], .021 if style == 'vityaz' else .016)

        def patch(start, end, lower, higher, protrusion, finish, steps):
            angles = [start + (end - start) * index / steps for index in range(steps + 1)]
            a = add_ring([point(angle, lower, protrusion) for angle in angles])
            b = add_ring([point(angle, higher, protrusion) for angle in angles])
            bridge(a, b, finish, closed=False)

        if style == 'vityaz':
            # Небольшие боковые защёлки. Лобная часть остаётся гладкой.
            patch(2.05, 2.27, rings[1], rings[3], .004, 2, 4)
            patch(-2.27, -2.05, rings[1], rings[3], .004, 2, 4)
        elif style == 'pervoprohodets':
            # Боковые планки и нижние липучки.
            for start, end in ((1.02, 2.25), (-2.25, -1.02)):
                patch(start, end, rings[2], rings[4], .0045, 2, 14)
                for tooth in range(8):
                    angle = start + .12 + .13 * tooth
                    patch(angle, angle + .045, rings[3], rings[4], .006, 3, 2)
            lower_velcro_top = (.416, .913, .929)
            for start, end in ((1.28, 2.05), (-2.05, -1.28)):
                patch(start, end, rings[4], lower_velcro_top, .0032, 4, 10)

        obj = helmet.object()
        if style == 'pervoprohodets':
            obj.shape_key_add(name='Basis')
            fitted = obj.shape_key_add(name='over_gas_mask')
            for vertex in fitted.data:
                _, y, z = vertex.co
                brow = ease(-y, .105, .179) * (1 - ease(z, 1.747, 1.842))
                vertex.co.z += .007 * brow
                vertex.co.y += .019 * brow
        mesh = obj.data
        tint = mesh.color_attributes.new(name='Color', type='BYTE_COLOR', domain='CORNER')
        mesh.color_attributes.active_color = tint
        uv = mesh.uv_layers.new(name='Cloth')
        tones = (color('ffffff'), color('53534c'), color('3f423f'),
                 color('bdbeb5'), color('676a54'), color('777b6b'))
        for polygon, finish in zip(mesh.polygons, helmet.finish):
            polygon.material_index = (0, 1, 2, 0, 1, 1)[finish]
            axis = max(range(3), key=lambda axis: abs(polygon.normal[axis]))
            u, v = (axis + 1) % 3, (axis + 2) % 3
            for index in polygon.loop_indices:
                point_co = mesh.vertices[mesh.loops[index].vertex_index].co
                tint.data[index].color = tones[finish]
                uv.data[index].uv = (point_co[u] * 4, point_co[v] * 4)
        obj.parent = rig
        obj.matrix_world = Matrix.Identity(4)
        result.append(obj)
    return result


def respirator_helmets():
    head = {'Head': 1}
    rubber = material('respirator_rubber', '535654', .80)
    lining = material('respirator_lining', '424541', .90)
    steel = material('respirator_steel', '777771', .56)
    dark_metal = material('respirator_dark_metal', '4b504f', .55)
    wear = material('respirator_edge_wear', '8e8a7a', .65)
    for metal in (steel, dark_metal, wear):
        metal.node_tree.nodes.get('Principled BSDF').inputs['Metallic'].default_value = .23
    rng = random.Random(520)
    grain = cloth_images('Фактура противогазо-шлемов', 'Рельеф противогазо-шлемов', 64,
                         [rng.random() for _ in range(64 * 64)], rng, .89, .09)
    angles = [math.tau * i / 40 for i in range(40)]
    dome_profile = ((0, 1), (.055, 1.008), (.21, .987), (.39, .946), (.57, .868),
                    (.73, .741), (.87, .558), (.95, .362), (.99, .156), (1, .027))
    outlines = {
        'blockpost': ((.011, 1.712), (.025, 1.735), (.076, 1.745), (.107, 1.733),
                      (.111, 1.701), (.094, 1.665), (.076, 1.661), (.037, 1.679)),
        'voevoda': ((-.108, 1.716), (-.094, 1.737), (-.052, 1.742), (.052, 1.742),
                    (.094, 1.737), (.108, 1.716), (.097, 1.686), (.064, 1.685),
                    (.027, 1.711), (0, 1.717), (-.027, 1.711), (-.064, 1.685), (-.097, 1.686)),
        'kuznets': ((.009, 1.710), (.031, 1.735), (.086, 1.743), (.110, 1.732),
                    (.111, 1.692), (.099, 1.663), (.081, 1.659), (.045, 1.678)),
    }
    profiles = {
        'blockpost': (.126, .144, 1.858, '55564d', '656359', '7a7361', .54),
        'voevoda': (.130, .146, 1.858, '4e585b', '536064', '48595e', .76),
        'kuznets': (.128, .148, 1.862, '55595b', '565553', '5d5148', .76),
    }
    result = []
    for style, (width, depth, crown, shell_color, plate_color, lens_color, opacity) in profiles.items():
        shell = bake_material('respirator_' + style + '_shell', grain, .10, .67)
        lens = material('respirator_' + style + '_lens', lens_color, .32)
        lens.node_tree.nodes.get('Principled BSDF').inputs['Alpha'].default_value = opacity
        lens.surface_render_method = 'BLENDED'
        helmet = Garment('gear_helmet_' + style, [shell, rubber, lining, steel, dark_metal, wear, lens])

        def ring(points):
            start = len(helmet.vertices)
            helmet.vertices.extend(points)
            helmet.weights.extend([head] * len(points))
            return list(range(start, len(helmet.vertices)))

        def face(indices, finish):
            helmet.faces.append(tuple(indices))
            helmet.finish.append(finish)

        def bridge(a, b, finish, closed=True):
            for i in range(len(a) if closed else len(a) - 1):
                j = (i + 1) % len(a)
                face((a[i], a[j], b[j], b[i]), finish)

        def loft_axis(center, axis, radius, profile, finish, segments=16):
            normal = Vector(axis).normalized()
            across = normal.cross(Vector((0, 0, 1))).normalized()
            up = across.cross(normal)
            sections = []
            for offset, scale in profile:
                sections.append(ring([
                    Vector(center) + normal * offset + scale * (
                        across * radius[0] * math.cos(math.tau * i / segments)
                        + up * radius[1] * math.sin(math.tau * i / segments))
                    for i in range(segments)
                ]))
            face(sections[0], finish)
            for a, b in zip(sections, sections[1:]):
                bridge(b, a, finish)
            face(reversed(sections[-1]), finish)

        def dome_point(angle, phase, scale):
            rim = 1.744 - .106 * ease(abs(math.remainder(angle, math.tau)), .66, 1.91)
            return (width * scale * math.sin(angle), -.035 - depth * scale * math.cos(angle),
                    rim + (crown - rim) * phase)

        dome = [ring([dome_point(angle, phase, scale) for angle in angles]) for phase, scale in dome_profile]
        inside = ring([dome_point(angle, .005, .957) for angle in angles])
        bridge(inside, dome[0], 2)
        for a, b in zip(dome, dome[1:]):
            bridge(a, b, 0)
        face(dome[-1], 0)

        def front_y(x):
            return -.186 + .057 * (x / .125) ** 2

        # Нижняя часть огибает подбородок и стыкуется с куполом за ушами.
        chin_profile = ((1.565, .066, -.137, .022), (1.575, .087, -.158, .049),
                        (1.600, .108, -.182, .077), (1.629, .118, -.199, .098),
                        (1.655, .122, -.192, .104), (1.685, .124, -.184, .105))
        def jaw_point(angle, row, z, rx, front, back):
            forward = math.cos(angle)
            ridge = max(0, forward)**28 * ease(row, 2, 5)
            return (rx * math.sin(angle), (front + back) / 2 - (back - front) / 2 * forward - .018 * ridge,
                    z + (.016 if row < 3 else 0) * (1 - max(0, forward)) + .024 * ridge)

        chin = [ring([jaw_point(a, row, *profile) for a in angles]) for row, profile in enumerate(chin_profile)]
        face(reversed(chin[0]), 2)
        for row, (a, b) in enumerate(zip(chin, chin[1:])):
            bridge(a, b, 2 if row == 0 else 0 if style == 'voevoda' else 1)

        # Прозрачные линзы занимают реальные проёмы. Вокруг них — сплошная рамка.
        def make_window(outline, xmin, xmax):
            outline = list(outline)
            area = sum(x * v - u * z for (x, z), (u, v) in zip(outline, outline[1:] + outline[:1]))
            if area < 0:
                outline.reverse()
            curve = [(x + (u - x) * t, z + (v - z) * t)
                     for (x, z), (u, v) in zip(outline, outline[1:] + outline[:1]) for t in (.22, .78)]
            cx = sum(x for x, _ in curve) / len(curve)
            cz = sum(z for _, z in curve) / len(curve)
            if style == 'voevoda':
                # Центр выше выемки под переносицу, чтобы треугольники линзы не пересекались.
                cx, cz = 0, 1.734
            # Добавляем углы внешней панели на соответствующих лучах от центра.
            zmin, zmax = 1.635, 1.754
            for corner_x, corner_z in ((xmin, zmin), (xmax, zmin), (xmax, zmax), (xmin, zmax)):
                dx, dz = corner_x - cx, corner_z - cz
                for i, (x, z) in enumerate(curve):
                    u, v = curve[(i + 1) % len(curve)]
                    a, b = u - x, v - z
                    divisor = a * dz - b * dx
                    if abs(divisor) < 1e-9:
                        continue
                    fraction = ((cx - x) * dz - (cz - z) * dx) / divisor
                    if .0001 < fraction < .9999 and (x + a * fraction - cx) * dx + (z + b * fraction - cz) * dz > 0:
                        curve.insert(i + 1, (x + a * fraction, z + b * fraction))
                        break

            outside, bevel, rim, inner = [], [], [], []
            for x, z in curve:
                dx, dz = x - cx, z - cz
                extent = min((xmax - cx if dx > 0 else xmin - cx) / dx if dx else math.inf,
                             (zmax - cz if dz > 0 else zmin - cz) / dz if dz else math.inf)
                edge_x, edge_z = cx + dx * extent, cz + dz * extent
                outside.append((edge_x, front_y(edge_x) + .013, edge_z))
                normal = Vector((dx, dz)).normalized()
                bevel.append((x + normal.x * .008, front_y(x) - .003, z + normal.y * .008))
                rim.append((x + normal.x * .004, front_y(x) - .010, z + normal.y * .004))
                inner.append((x, front_y(x) - .008, z))
            outer_ring, bevel_ring, rim_ring, inner_ring = map(ring, (outside, bevel, rim, inner))
            panel = 0 if style == 'voevoda' else 2
            bridge(outer_ring, bevel_ring, panel)
            bridge(bevel_ring, rim_ring, 3 if style == 'blockpost' else 4)
            bridge(rim_ring, inner_ring, 4 if style == 'blockpost' else 2)
            for side in (xmin, xmax):
                if side == 0:
                    continue
                sign = math.copysign(1, side)
                edge = [i for i, (x, _, _) in enumerate(outside) if abs(x - side) < 1e-6]
                edge.sort(key=lambda i: outside[i][2], reverse=sign < 0)
                start = [outer_ring[i] for i in edge]
                for x, y, zlow, zhigh in ((width + .003, -.077, 1.638, 1.732),
                                          (width - .003, -.019, 1.639, 1.699)):
                    row = ring([(sign * x, y, zlow + (zhigh - zlow) * (outside[i][2] - zmin) / (zmax - zmin))
                                for i in edge])
                    bridge(start, row, panel, closed=False)
                    start = row
            glass_edge = ring([(x, y + .001, z) for x, y, z in inner])
            glass_center = ring([(cx, front_y(cx) - .012, cz)])[0]
            for i, start in enumerate(glass_edge):
                face((start, glass_edge[(i + 1) % len(glass_edge)], glass_center), 6)
            if style == 'blockpost':
                for i in range(0, len(rim), 2):
                    j = (i + 1) % len(rim)
                    x, y, z = (Vector(rim[i]) + Vector(rim[j])) * .5
                    loft_axis((x, y + .0005, z), (x * 2, -1, 0), (.0035, .0025),
                              ((-.001, 1), (.0015, .80)), 5, 6)

        outline = outlines[style]
        if style == 'voevoda':
            make_window(outline, -.123, .123)
        else:
            make_window(outline, 0, .123)
            make_window([(-x, z) for x, z in outline], -.123, 0)

        if style == 'blockpost':
            # Потёртая лобная накладка с гранёным верхом и рядами заклёпок.
            rows = []
            for row in range(4):
                points = []
                for step in range(19):
                    angle = (step - 9) * .112
                    phase = (.045, .18, .44, .66 - .30 * abs(angle))[row]
                    scale = (1.015, 1.008, .948, .885 + .095 * abs(angle))[row]
                    x, y, z = dome_point(angle, phase, scale)
                    points.append((x, y - .008, z))
                rows.append(ring(points))
            for a, b in zip(rows, rows[1:]):
                bridge(a, b, 0, closed=False)
            for i in (2, 4, 6, 12, 14, 16):
                point = Vector(helmet.vertices[rows[3][i]]).lerp(helmet.vertices[rows[2][i]], .44)
                loft_axis(point, (point.x * 3, -1, .17), (.0036, .0036), ((-.002, 1), (.002, .75)), 4, 8)
        elif style == 'kuznets':
            for angle in (-.54, .54):
                x, y, z = dome_point(angle, .87, .558)
                loft_axis((x, y, z), (angle, -.42, 1), (.0048, .0048),
                          ((-.002, 1), (.0017, .85)), 4, 8)

        for sign in (-1, 1):
            # Снаружи различаются корпуса на ушах и форма боковых клапанов.
            cup_x = width - .009
            cup_y = -.023 if style == 'kuznets' else -.028
            cup_size = (.047, .064) if style == 'kuznets' else (.033, .060) if style == 'voevoda' else (.035, .055)
            loft_axis((sign * cup_x, cup_y, 1.677), (sign, 0, 0), cup_size,
                      ((-.007, .91), (.005, 1), (.015, .86), (.021, .56)), 0)
            valve = (sign * .078, -.170, 1.626)
            loft_axis(valve, (sign * .44, -1, 0), (.024, .027), ((-.012, .85), (.004, 1), (.013, .83)), 4)
            loft_axis(Vector(valve) + Vector((sign * .005, -.011, 0)), (sign * .44, -1, 0),
                      (.015, .017), ((-.001, 1), (.004, .85)), 2)
            loft_axis(Vector(valve) + Vector((sign * .008, -.016, 0)), (sign * .44, -1, 0),
                      (.012, .0028), ((-.002, 1), (.002, .82)), 4, 8)

        if style == 'voevoda':
            # Высокий кожух по центру задаёт профиль Воеводы.
            loft_axis((0, -.196, 1.626), (0, -1, 0), (.044, .060),
                      ((-.022, 1), (.008, .94), (.029, .82), (.034, .74)), 0)
            center, radius = (0, -.230, 1.607), (.021, .021)
        else:
            center, radius = (0, -.197, 1.616), (.033, .033) if style == 'kuznets' else (.035, .035)
        loft_axis(center, (0, -1, 0), radius, ((-.011, .85), (.002, 1), (.019, .94), (.023, .85)),
                  3 if style == 'blockpost' else 4)
        loft_axis((0, center[1] - .023, center[2]), (0, -1, 0), (radius[0] * .74, radius[1] * .74),
                  ((-.001, 1), (.001, .97)), 2)
        if style == 'voevoda':
            for slot in range(-2, 3):
                z = center[2] + slot * radius[1] * .235
                loft_axis((0, center[1] - .024, z), (0, -1, 0),
                          (radius[0] * (.66 - .06 * abs(slot)), .0015), ((-.001, 1), (.002, .92)), 4, 8)
        else:
            finish = 3 if style == 'blockpost' else 4
            loft_axis((0, center[1] - .024, center[2]), (0, -1, 0), (.011, .011),
                      ((-.001, 1), (.005, .81)), finish, 12)
            for angle in (0, math.tau / 3, 2 * math.tau / 3):
                out = Vector((math.sin(angle), 0, math.cos(angle)))
                across = Vector((out.z, 0, -out.x))
                pivot = Vector(center) + Vector((0, -.026, 0))
                blade = [pivot + out * distance + across * half_width for distance, half_width in
                         ((.009, -.0016), (.024, -.0024), (.024, .0024), (.009, .0016))]
                face(ring(blade), finish)

        obj = helmet.object()
        mesh = obj.data
        tint = mesh.color_attributes.new(name='Color', type='BYTE_COLOR', domain='CORNER')
        mesh.color_attributes.active_color = tint
        uv = mesh.uv_layers.new(name='Helmet')
        plate_start = rows[0][0] if style == 'blockpost' else len(mesh.vertices)
        plate_end = rows[-1][-1] if style == 'blockpost' else -1
        for polygon in mesh.polygons:
            if polygon.material_index == 6:
                polygon.use_smooth = True
            is_plate = plate_start <= polygon.vertices[0] <= plate_end
            tone = color('99968a' if is_plate else plate_color if polygon.center.y < -.115
                         and polygon.center.z < 1.69 else shell_color)
            if polygon.material_index != 0:
                tone = (1, 1, 1, 1)
            axis = max(range(3), key=lambda i: abs(polygon.normal[i]))
            u, v = (axis + 1) % 3, (axis + 2) % 3
            for index in polygon.loop_indices:
                point = mesh.vertices[mesh.loops[index].vertex_index].co
                tint.data[index].color = tone
                uv.data[index].uv = (point[u] * 5, point[v] * 5)
        obj.parent = rig
        obj.matrix_world = Matrix.Identity(4)
        result.append(obj)
    return result


def visor_helmets():
    head = {'Head': 1}
    rng = random.Random(613)
    grain = cloth_images('Краска шлемов с забралом', 'Рельеф краски шлемов с забралом', 64,
                         [rng.random() for _ in range(64 * 64)], rng, .95, .041)
    cloth = bake_material('visor_helmet_cloth', canvas_maps, .18, .86)
    cloth.use_backface_culling = False
    metal = material('visor_helmet_hardware', '505657', .51)
    fastener = material('visor_helmet_fastener', '727573', .49)
    glass = material('visor_clear_glass', 'b9ced5', .28)
    glass_edge = material('visor_glass_edge', 'a6bbbf', .44)
    smoked = material('visor_smoked_glass', '687d80', .30)
    for surface, opacity in ((glass, .21), (glass_edge, .60), (smoked, .64)):
        surface.node_tree.nodes.get('Principled BSDF').inputs['Alpha'].default_value = opacity
        surface.surface_render_method = 'BLENDED'
        surface.use_backface_culling = True

    # Ширина и глубина купола, макушка, кромки спереди / у ушей / сзади.
    shells = {
        'kysa_2': (.130, .147, 1.862, 1.753, 1.614, 1.647),
        'gusar': (.131, .146, 1.865, 1.749, 1.662, 1.668),
        'shz_13m': (.129, .145, 1.862, 1.753, 1.660, 1.665),
        'karbovanets': (.139, .152, 1.874, 1.750, 1.624, 1.638),
        'volk_a': (.131, .147, 1.863, 1.752, 1.649, 1.659),
    }
    # Высоты верхнего и нижнего края, прогиб, ширина, вынос спереди и глубина у шарнира.
    shields = {
        'kysa_2': (1.753, 1.568, .012, .139, -.205, -.089),
        'gusar': (1.744, 1.565, .020, .144, -.213, -.101),
        'shz_13m': (1.751, 1.620, .017, .141, -.211, -.082),
        'karbovanets': (1.760, 1.560, .021, .143, -.213, -.064),
        'volk_a': (1.746, 1.574, .028, .140, -.209, -.092),
    }
    profile = ((0, 1), (.028, 1.005), (.12, 1.006), (.26, .984), (.43, .937), (.59, .866),
               (.73, .750), (.84, .606), (.93, .447), (.98, .258), (1, .035))
    angles = [math.tau * i / 48 for i in range(48)]
    sections = (-1, -.977, -.94, -.88, -.76, -.60, -.45, -.30, -.15, 0,
                .15, .30, .45, .60, .76, .88, .94, .977, 1)
    output = []
    for style, (width, depth, crown, brow, ears, nape) in shells.items():
        textile = style in {'gusar', 'volk_a'}
        shell = bake_material('helmet_' + style + '_shell', canvas_maps if textile else grain,
                              .18 if textile else .07, .86 if textile else .58)
        shell.use_backface_culling = False
        helmet = Garment('gear_helmet_' + style, [shell, cloth, metal, fastener, glass, glass_edge, smoked])

        def ring(points):
            start = len(helmet.vertices)
            helmet.vertices.extend(points)
            helmet.weights.extend([head] * len(points))
            return list(range(start, len(helmet.vertices)))

        def face(indices, finish):
            helmet.faces.append(tuple(indices))
            helmet.finish.append(finish)

        def bridge(a, b, finish, closed=False):
            for i in range(len(a) if closed else len(a) - 1):
                j = (i + 1) % len(a)
                face((a[i], a[j], b[j], b[i]), finish)

        def dome(a, phase, radius, offset=0):
            side = abs(math.remainder(a, math.tau))
            fall = ease(side, .72 if style != 'karbovanets' else .91, 1.52)
            rim = brow + (ears - brow) * fall + (nape - ears) * ease(side, 1.67, 2.94)
            return Vector(((width * radius + offset) * math.sin(a),
                           -.038 - (depth * radius + offset) * math.cos(a),
                           rim + (crown - rim) * phase))

        dome_rows = [ring([dome(a, phase, radius) for a in angles]) for phase, radius in profile]
        inner = ring([dome(a, .014, .954) for a in angles])
        bridge(inner, dome_rows[0], 2, closed=True)
        for row, (a, b) in enumerate(zip(dome_rows, dome_rows[1:])):
            bridge(a, b, 1 if row == 0 else 0, closed=True)
        face(dome_rows[-1], 0)

        if textile:
            for center in (-.74, .74, math.pi):
                a = ring([dome(center - .019, phase, radius, .0011) for phase, radius in profile[1:-1]])
                b = ring([dome(center + .019, phase, radius, .0011) for phase, radius in profile[1:-1]])
                bridge(b, a, 0)

        if style in {'kysa_2', 'volk_a'}:
            # Свисающий затыльник, суженный около ушей и отведённый от шеи.
            rows = []
            for row in range(3):
                points = []
                for i in range(23):
                    a = 1.46 + (math.tau - 2 * 1.46) * i / 22
                    point = dome(a, 0, 1, .004)
                    depth_at_nape = math.sin(math.pi * i / 22)**.64
                    point.z -= (.003, .027, .052 if style == 'kysa_2' else .109)[row] * (.18 + .82 * depth_at_nape)
                    point.y += (.001, .010, .036)[row] * depth_at_nape
                    points.append(point)
                rows.append(ring(points))
            bridge(rows[1], rows[0], 2 if style == 'kysa_2' else 0)
            bridge(rows[2], rows[1], 1 if style == 'kysa_2' else 0)

        def rivet(center, axis, radius, finish=3):
            normal = Vector(axis).normalized()
            u = normal.cross(Vector((0, 0, 1))).normalized()
            v = normal.cross(u)
            perimeter = [radius * (math.cos(i * math.tau / 12) * u + math.sin(i * math.tau / 12) * v) for i in range(12)]
            lower = ring([center + point for point in perimeter])
            cap = ring([center + .81 * point + normal * radius * .36 for point in perimeter])
            bridge(lower, cap, finish, closed=True)
            face(cap, finish)

        for sign in (-1, 1):
            # Плоский ремешок повторяет линию нижней челюсти.
            anchor = dome(sign * 1.62, .032, .94)
            strap = [anchor, (sign * .094, -.056, 1.637), (sign * .087, -.087, 1.611),
                     (sign * .058, -.114, 1.579), (sign * .028, -.123, 1.567),
                     (0, -.126, 1.566)]
            a = ring([(x, y, z - .008) for x, y, z in strap])
            b = ring([(x, y - .001, z + .005) for x, y, z in strap])
            bridge(a, b, 1)

        top, bottom, rise, shield_width, front_y, side_y = shields[style]

        def shield(s, t, offset=0):
            side = abs(s)
            arc = .58 * side**2 + .42 * side**4
            taper = (.068 if style != 'gusar' else .106) * t**2
            lower_z = bottom + rise * side**2
            if style == 'volk_a':
                lower_z += .013 * ease(side, .35, .82)
            corner = .022 * ease(side, .87, 1) * t**3
            return Vector((s * shield_width * (1 - taper),
                           front_y + (side_y - front_y) * arc - (.016 * (1 - arc) - .006 * arc) * t - offset,
                           top + (lower_z - top) * t - .014 * side**3 * (1 - t) + corner))

        def strip(span, a, b, finish, offset=0):
            lower = ring([shield(s, b, offset) for s in span])
            upper = ring([shield(s, a, offset) for s in span])
            bridge(lower, upper, finish)
            return lower, upper

        if style == 'karbovanets':
            # Стальная маска вокруг узкого смотрового окна.
            window = tuple(s * .75 for s in sections)
            panel = (-1, -.89, *window, .89, 1)
            strip(panel, 0, .22, 0, .004)
            strip(panel, .49, 1, 0, .004)
            strip(panel[:3], .22, .49, 0, .004)
            strip(panel[-3:], .22, .49, 0, .004)
            strip(window, .22, .49, 6, .002)
            strip(window, .183, .227, 2, .009)
            strip(window, .483, .524, 2, .009)
            for sign in (-1, 1):
                strip((-.816, -.74) if sign < 0 else (.74, .816), .183, .524, 2, .009)
                for height in (.24, .46):
                    rivet(shield(sign * .90, height, .005), (sign * .53, -1, 0), .0045)
        else:
            rows = [ring([shield(s, t) for s in sections]) for t in (0, .08, .37, .70, .90, 1)]
            for a, b in zip(rows, rows[1:]):
                bridge(b, a, 4)
            # Тонкая шлифованная кромка повторяет стекло по периметру.
            strip(sections, .974, 1, 5, .0014)
            strip(sections, 0, .025, 5, .0014)
            for span in (sections[:2], sections[-2:]):
                strip(span, .025, .974, 5, .0014)
            if style == 'kysa_2':
                strip(sections, 0, .145, 2, .0032)
                bracket = ring([shield(s, 0, .0032) for s in sections])
                seated = ring([dome(s * 1.07, .083, 1.003, -.0015) for s in sections])
                bridge(seated, bracket, 2)
                for s in (-.87, -.44, .44, .87):
                    rivet(shield(s, .063, .004), (s * .75, -1, 0), .0042)

        for sign in (-1, 1):
            # Шарниры находятся на боковинах. Лоб остаётся гладким.
            outer = shield(sign * .98, .08)
            pivot = dome(sign * 1.24, .30, .976, .005)
            panel = [outer + Vector((0, -.007, .010)), pivot + Vector((sign * .002, .013, .015)),
                     pivot + Vector((sign * .004, .016, -.016)), outer + Vector((0, -.004, -.013))]
            if sign < 0:
                panel.reverse()
            face(ring(panel), 2 if style != 'shz_13m' else 0)
            rivet(pivot, (sign, -.14, 0), .013 if style != 'shz_13m' else .010, 2)
            rivet(pivot + Vector((sign * .005, 0, 0)), (sign, -.14, 0), .005)
            if style in {'gusar', 'volk_a'}:
                a = ring([dome(sign * angle, .18, 1.005, .006) for angle in (1.23, 1.42, 1.60, 1.78, 1.94)])
                b = ring([dome(sign * angle, .33, .96, .007) for angle in (1.23, 1.42, 1.60, 1.78, 1.94)])
                if sign < 0:
                    a.reverse()
                    b.reverse()
                bridge(a, b, 2)
            elif style == 'kysa_2':
                vent = dome(sign * 1.59, .21, .991, .002)
                rivet(vent, (sign, 0, 0), .014, 2)
                for u, v in ((0, 0), (-1, 1), (1, 1), (-1, -1), (1, -1)):
                    rivet(vent + Vector((sign * .0055, u * .0055, v * .0055)), (sign, 0, 0), .0022, 3)

        obj = helmet.object()
        mesh = obj.data
        tint = mesh.color_attributes.new(name='Color', type='BYTE_COLOR', domain='CORNER')
        mesh.color_attributes.active_color = tint
        uv = mesh.uv_layers.new(name='Helmet')
        for polygon in mesh.polygons:
            finish = polygon.material_index
            tint_color = color('454541') if finish == 1 else (1, 1, 1, 1)
            axis = max(range(3), key=lambda i: abs(polygon.normal[i]))
            u, v = (axis + 1) % 3, (axis + 2) % 3
            for index in polygon.loop_indices:
                point = mesh.vertices[mesh.loops[index].vertex_index].co
                tint.data[index].color = tint_color
                uv.data[index].uv = (point[u] * 5, point[v] * 5)
        obj.parent = rig
        obj.matrix_world = Matrix.Identity(4)
        output.append(obj)
    return output


def soft_headwear_models():
    # Высота, полуширина, полуглубина, смещение вправо и оттенок ткани.
    crowns = {
        'beret': [(1.752,.124,.135,0,1), (1.762,.126,.137,0,1), (1.768,.128,.139,.004,0),
                  (1.787,.137,.136,.020,0), (1.813,.128,.130,.024,0), (1.833,.102,.111,.022,0),
                  (1.845,.058,.068,.018,0), (1.848,.010,.012,.014,0)],
        'beanie': [(1.708,.122,.134,0,1), (1.713,.128,.138,0,1), (1.744,.127,.138,0,1),
                   (1.749,.121,.132,0,2), (1.763,.118,.130,0,0), (1.798,.103,.112,0,0),
                   (1.830,.080,.084,0,0), (1.852,.051,.052,0,0), (1.863,.009,.010,0,0)],
        'cap': [(1.742,.125,.141,0,1), (1.752,.127,.142,0,0), (1.777,.117,.132,0,0),
                (1.809,.096,.108,0,0), (1.834,.073,.081,0,0), (1.847,.044,.049,0,0),
                (1.853,.007,.008,0,0), (1.856,.004,.005,0,1)],
        'panama': [(1.738,.124,.136,0,1), (1.751,.126,.138,0,1), (1.762,.122,.135,0,1),
                   (1.796,.109,.123,0,0), (1.827,.099,.110,0,0), (1.842,.089,.100,0,0),
                   (1.848,.058,.070,0,0), (1.850,.009,.012,0,0)],
    }
    result = []
    weights = {'Head': 1}
    for style, profile in crowns.items():
        name = 'gear_headwear_' + style
        cloth = bake_material('headwear_' + style + '_fabric', knit_maps if style == 'beanie' else canvas_maps,
                              .28 if style == 'beanie' else .18)
        hat = Garment(name, [cloth])
        segments = 48 if style == 'beanie' else 36

        def loft(rows):
            first = hat.loft([((shift, -.042, height), width, depth, weights)
                              for height, width, depth, shift, _ in rows], segments,
                             lambda ring, angle: rows[ring][4], close=False)
            for ring, (height, width, _, _, _) in enumerate(rows):
                for index in range(segments):
                    angle = math.tau * index / segments
                    x, y, z = hat.vertices[first + ring * segments + index]
                    if style == 'beret':
                        z -= .115 * x * ease(height, 1.761, 1.791)
                    elif style == 'beanie':
                        rib = .0016 * math.cos(angle * 24) * (1 - ease(height, 1.738, 1.752))
                        x += rib * math.cos(angle)
                        y += rib * math.sin(angle)
                    elif style == 'panama':
                        z += .004 * math.sin(3 * angle + .55) * ease(width, .112, .173)
                    else:
                        z += .004 * max(0, -math.sin(angle)) * (1 - ease(height, 1.750, 1.783))
                    hat.vertices[first + ring * segments + index] = x, y, z
            return first

        # Заворачиваем нижнюю кромку внутрь и закрываем только макушку.
        height, width, depth, shift, _ = profile[0]
        loft([(height + .002, width - .005, depth - .005, shift, 2), *profile])
        hat.faces.append(tuple(range(len(hat.vertices) - segments, len(hat.vertices))))
        hat.finish.append(0)
        if style == 'panama':
            loft([(1.713,.177,.182,0,1), (1.721,.168,.176,0,0), (1.732,.145,.150,0,0),
                  (1.746,.122,.134,0,2), (1.741,.122,.134,0,2), (1.727,.145,.150,0,2),
                  (1.716,.168,.176,0,2), (1.710,.177,.182,0,1), (1.713,.177,.182,0,1)])
        elif style == 'cap':
            # Козырёк расширяется к передней кромке и имеет собственную толщину.
            inner, outer = [], []
            for index in range(19):
                u = (index - 9) / 9
                angle = math.pi / 2 + u * .88
                reach = .093 * math.cos(u * math.pi / 2) ** .68
                x, y = .123 * math.cos(angle), -.042 - .139 * math.sin(angle)
                inner.append(Vector((x, y, 1.750)))
                outer.append(Vector((x * (1 + .29 * (1 - u * u)), y - reach, 1.748 - .19 * reach + .005 * u * u)))
            below = Vector((0, 0, -.004))
            for a, b, c, d in zip(inner, inner[1:], outer, outer[1:]):
                hat.quad([a, b, d, c], [weights] * 4, 0)
                hat.quad([c + below, d + below, b + below, a + below], [weights] * 4, 2)
                hat.quad([c, d, d + below, c + below], [weights] * 4, 1)

        obj = hat.object()
        if style == 'beanie':
            obj.shape_key_add(name='Basis')
            fitted = obj.shape_key_add(name='over_gas_mask')
            for vertex in fitted.data:
                _, y, z = vertex.co
                # Отворот лежит на резиновой кромке над окулярами.
                front = ease(-y, .071, .164)
                cuff = front * (1 - ease(z, 1.713, 1.805))
                vertex.co.z += .027 * cuff
                vertex.co.y -= .006 * cuff
        mesh = obj.data
        tint = mesh.color_attributes.new(name='Color', type='BYTE_COLOR', domain='CORNER')
        mesh.color_attributes.active_color = tint
        uv = mesh.uv_layers.new(name='Cloth')
        for polygon, finish in zip(mesh.polygons, hat.finish):
            polygon.material_index = 0
            axis = max(range(3), key=lambda axis: abs(polygon.normal[axis]))
            u, v = (axis + 1) % 3, (axis + 2) % 3
            shade = (1, .76, .61)[finish]
            for index in polygon.loop_indices:
                point = mesh.vertices[mesh.loops[index].vertex_index].co
                tint.data[index].color = (shade, shade, shade, 1)
                uv.data[index].uv = (point[u] * 5, point[v] * 5)
        obj.parent = rig
        obj.matrix_world = Matrix.Identity(4)
        result.append(obj)
    return result


def ushanka_fur_maps():
    size = 128
    rng = random.Random(113)
    height = [0.0] * (size * size)
    # Периодические слои разного масштаба дают короткий неоднородный ворс.
    for cells, strength in ((7, .32), (19, .43), (43, .25)):
        values = [[rng.random() for _ in range(cells)] for _ in range(cells)]
        for y in range(size):
            v = y * cells / size
            row = int(v)
            ty = ease(v - row, 0, 1)
            for x in range(size):
                u = x * cells / size
                col = int(u)
                tx = ease(u - col, 0, 1)
                a, b = values[row], values[(row + 1) % cells]
                right = (col + 1) % cells
                low = a[col] * (1 - tx) + a[right] * tx
                high = b[col] * (1 - tx) + b[right] * tx
                height[y * size + x] += strength * (low * (1 - ty) + high * ty)
    return cloth_images('Короткий ворс ушанки', 'Рельеф ворса ушанки', size, height, rng, .64, .31)


def ushanka_model():
    shell = bake_material('ushanka_canvas', canvas_maps, .18)
    fur = bake_material('ushanka_fur', ushanka_fur_maps(), .46)
    hat = Garment('gear_headwear_ushanka', [shell, fur])
    weights = {'Head': 1}
    center_y = -.046
    segments = 40
    profile = [(1.743, .109, .117), (1.748, .116, .124), (1.769, .121, .126),
               (1.833, .119, .123), (1.861, .108, .112), (1.877, .089, .096),
               (1.884, .054, .060), (1.888, .010, .012)]
    def square_round(value, radius):
        return radius * math.copysign(abs(value / radius) ** .84, value)

    hat.loft([((0, center_y, z), w, d, weights) for z, w, d in profile],
             segments, lambda row, angle: 0, close=False)
    for index, (x, y, z) in enumerate(hat.vertices):
        _, w, d = profile[index // segments]
        hat.vertices[index] = (square_round(x, w), center_y + square_round(y - center_y, d), z)
    hat.faces.append(tuple(range(len(hat.vertices) - segments, len(hat.vertices))))
    hat.finish.append(0)

    def padded_band(start, end, steps, bounds):
        first = len(hat.vertices)
        columns = steps + 1
        # Внутренняя сторона, закруглённые края и наружный слой меха.
        contour = ((.03, -.008), (0, .002), (.075, .011), (.27, .016),
                   (.73, .018), (.94, .014), (1, .005), (.96, -.008))
        for fraction, padding in contour:
            for index in range(columns):
                t = index / steps
                angle = start + (end - start) * t
                low, high, width, depth = bounds(angle, t)
                edge = .67 + .33 * ease(min(t, 1 - t), 0, .12)
                amount = padding * edge
                hat.vertices.append(((width + amount) * math.sin(angle),
                                     center_y - (depth + amount) * math.cos(angle),
                                     low + (high - low) * fraction))
                hat.weights.append(weights)
        for row in range(len(contour)):
            next_row = (row + 1) % len(contour)
            for index in range(steps):
                a, b = first + row * columns + index, first + next_row * columns + index
                hat.faces.append((a, a + 1, b + 1, b))
                hat.finish.append(0 if row == len(contour) - 1 else 1)
        hat.faces.extend((tuple(first + row * columns for row in range(len(contour))),
                          tuple(first + row * columns + steps for row in reversed(range(len(contour))))))
        hat.finish.extend((1, 1))

    def visor_bounds(angle, t):
        corner = 1 - ease(min(t, 1 - t), 0, .14)
        return 1.737 + .013 * corner, 1.862 - .018 * corner, .124, .137

    def earflap_bounds(angle, t):
        side = min(angle, math.tau - angle)
        drop = ease(side, .96, 1.30) * (1 - ease(side, 1.83, 2.28))
        lower = 1.686 - .124 * drop + .049 * (1 - ease(side, .86, 1.07))
        return lower, 1.768 - .012 * ease(side, 2.04, 2.9), .111, .120

    padded_band(-1.01, 1.01, 22, visor_bounds)
    padded_band(.89, math.tau - .89, 48, earflap_bounds)
    obj = hat.object()
    mesh = obj.data
    tint = mesh.color_attributes.new(name='Color', type='BYTE_COLOR', domain='CORNER')
    mesh.color_attributes.active_color = tint
    uv = mesh.uv_layers.new(name='Cloth')
    colors = (color('525552'), color('85837e'))
    for poly in mesh.polygons:
        axis = max(range(3), key=lambda axis: abs(poly.normal[axis]))
        u, v = (axis + 1) % 3, (axis + 2) % 3
        for index in poly.loop_indices:
            point = mesh.vertices[mesh.loops[index].vertex_index].co
            shade = .89 + .11 * ease(point.z, 1.55, 1.86)
            tint.data[index].color = tuple(component * shade for component in colors[poly.material_index][:3]) + (1,)
            uv.data[index].uv = (point[u] * 4, point[v] * 4)
    obj.parent = rig
    obj.matrix_world = Matrix.Identity(4)
    return obj


def ushanka_star_model():
    bronze = material('ushanka_gold_star', 'b99850', .53)
    bronze.node_tree.nodes.get('Principled BSDF').inputs['Metallic'].default_value = .48
    star = Garment('gear_headwear_ushanka_star', [bronze])
    head = {'Head': 1}
    for radius, y in ((1, -.201), (.89, -.205)):
        for index in range(10):
            angle = math.tau * index / 10
            distance = radius * (.024 if index % 2 == 0 else .010)
            star.vertices.append((-distance * math.sin(angle), y, 1.804 + distance * math.cos(angle)))
            star.weights.append(head)
    star.vertices.append((0, -.209, 1.804))
    star.weights.append(head)
    for index in range(10):
        after = (index + 1) % 10
        star.faces.extend(((index, after, after + 10, index + 10), (index + 10, after + 10, 20)))
        star.finish.extend((0, 0))
    obj = star.object()
    obj.parent = rig
    obj.matrix_world = Matrix.Identity(4)
    return obj


def balaclava_models():
    face = parts['face_calm'][0]
    face.data.calc_loop_triangles()
    skin_points = [face.matrix_world @ v.co for v in face.data.vertices]
    skin_faces = [t.vertices[:] for t in face.data.loop_triangles if face.data.materials[t.material_index].name == 'Skin']
    skin = BVHTree.FromPolygons(skin_points, skin_faces, all_triangles=True)
    skin_weights = [{face.vertex_groups[group.group].name: group.weight for group in vertex.groups}
                    for vertex in face.data.vertices]
    barycentric_axes = (Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1)))
    heights = (1.503, 1.512, 1.525, 1.538, 1.552, 1.565, 1.579, 1.593, 1.608, 1.619,
               1.630, 1.641, 1.652, 1.663, 1.673, 1.682, 1.693, 1.705, 1.717, 1.729,
               1.740, 1.756, 1.775, 1.793, 1.809, 1.820, 1.830, 1.836)
    segments = 72
    center_y = -.046
    result = []
    # Трафарет на ткани. Углубления под носом, скулами и между зубами имеют цвет основы.
    def skull_ink(angle, z):
        x = abs(angle)
        def oval(x0, z0, width, height):
            radius = math.hypot((x - x0) / width, (z - z0) / height)
            return 1 - ease(radius, .77, 1.08)
        cheek = oval(.27, 1.643, .39, .037)
        jaw = oval(.13, 1.609, .31, .025)
        nose = oval(0, 1.638, .13, .012)
        hollow = oval(.58, 1.624, .26, .015)
        teeth = 1 - .70 * math.sin(angle * 19)**4 * oval(0, 1.605, .32, .013)
        return max(cheek, jaw) * (1 - max(nose, hollow)) * teeth

    def cloth_point(z, front_angle, under_mp50=False):
        if under_mp50:
            # Под очками МП-50 кромка прорези подтянута к переносице.
            slit_span = ease(z, 1.646, 1.674) * (1 - ease(z, 1.730, 1.767))
            front_angle *= 1 - .56 * slit_span * (1 - ease(abs(front_angle), .72, 1.55))
        angle = front_angle - math.pi / 2
        direction = Vector((math.cos(angle), math.sin(angle), 0))
        center = Vector((0, center_y, z))
        hit, _, triangle, _ = skin.ray_cast(center + direction * .28, -direction, .28)
        weights = {'Head': 1} if z > 1.58 else {'Neck': 1}
        face_span = ease(z, 1.584, 1.623) * (1 - ease(z, 1.727, 1.752))
        tuck = face_span * (1 - ease(abs(front_angle), .38, .86)) if under_mp50 else 0
        clearance = .004 + .005 * ease(z, 1.650, 1.683)
        radius = (hit - center).length + clearance - .013 * tuck if hit is not None else .054
        if triangle is not None:
            radius -= balaclava_face_offset(hit) * max(0, -direction.y)
            indices = skin_faces[triangle]
            weights = {}
            factors = barycentric_transform(hit, *(skin_points[i] for i in indices), *barycentric_axes)
            for index, factor in zip(indices, factors):
                for bone, weight in skin_weights[index].items():
                    weights[bone] = weights.get(bone, 0) + weight * max(0, factor)
        if z >= 1.820:
            cap = {1.820: .055, 1.830: .043, 1.836: .022}
            radius = max(radius if hit is not None else 0, cap[z])
        if not under_mp50:
            nose_span = ease(z, 1.633, 1.655) * (1 - ease(z, 1.676, 1.689))
            nose_center = 1 - ease(abs(front_angle), .20, .48)
            radius = max(radius, .125 * nose_span * nose_center)
        # Ткань натягивается между виском и челюстью, прижимая выступающие уши.
        ear_span = ease(z, 1.641, 1.667) * (1 - ease(z, 1.723, 1.752))
        ear_side = 1 - ease(abs(abs(front_angle) - math.pi / 2), .28, .62)
        cheek_width = .086 + .004 * ease(z, 1.675, 1.725)
        ear_excess = max(0, radius - cheek_width / max(.01, abs(direction.x)))
        radius -= ear_excess * ear_span * ear_side
        return center + direction * radius, weights

    rings = [(z, math.remainder(math.tau * segment / segments, math.tau))
             for z in heights for segment in range(segments)]
    points, weights = zip(*(cloth_point(z, angle) for z, angle in rings))
    tucked = [cloth_point(z, angle, under_mp50=True)[0] for z, angle in rings]
    for variant in ('balaclava', 'ghost'):
        cloth = bake_material('mask_' + variant + '_fabric', knit_maps, .22)
        mask = Garment('gear_mask_' + variant, [cloth])
        mask.vertices.extend(points)
        mask.weights.extend(weights)

        def eye_opening(angle, z):
            return ((z - 1.703) / .027)**4 + (angle / .80)**4 < 1

        for row, (low, high) in enumerate(zip(heights, heights[1:])):
            for segment in range(segments):
                angle = math.remainder(math.tau * (segment + .5) / segments, math.tau)
                z = (low + high) / 2
                if eye_opening(angle, z):
                    continue
                edge = eye_opening(angle * .89, 1.703 + (z - 1.703) * .82)
                a = row * segments + segment
                b = row * segments + (segment + 1) % segments
                mask.faces.append((a, b, b + segments, a + segments))
                mask.finish.append(int(edge or row == 0))
        top = (len(heights) - 1) * segments
        mask.faces.append(tuple(range(top, top + segments)))
        mask.finish.append(0)
        obj = mask.object()
        obj.shape_key_add(name='Basis')
        fitted = obj.shape_key_add(name='under_mp50')
        for vertex, point in zip(fitted.data, tucked):
            vertex.co = point
        mesh = obj.data
        tint = mesh.color_attributes.new(name='Color', type='BYTE_COLOR', domain='CORNER')
        mesh.color_attributes.active_color = tint
        uv = mesh.uv_layers.new(name='Cloth')
        is_ghost = variant == 'ghost'
        pigment = mesh.uv_layers.new(name='ClothTint')
        base, bone = color('515556'), color('c0b8aa')
        for polygon, edge in zip(mesh.polygons, mask.finish):
            polygon.material_index = 0
            segments_in_face = [mesh.loops[i].vertex_index % segments for i in polygon.loop_indices]
            wraps = len(polygon.vertices) == 4 and 0 in segments_in_face and segments - 1 in segments_in_face
            for index, segment in zip(polygon.loop_indices, segments_in_face):
                point = mesh.vertices[mesh.loops[index].vertex_index].co
                angle = math.remainder(math.tau * segment / segments, math.tau)
                ink = skull_ink(angle, point.z) if is_ghost and not edge else 0
                shade = .66 if edge else 1
                cloth_weight = shade * (1 - ink)
                tint.data[index].color = tuple(base[i] * cloth_weight + bone[i] * ink for i in range(3)) + (1,)
                pigment.data[index].uv = (cloth_weight, 0)
                u = 1 if wraps and segment == 0 else segment / segments
                uv.data[index].uv = (u * 3, (point.z - heights[0]) * 5)
        obj.parent = rig
        obj.matrix_world = Matrix.Identity(4)
        result.append(obj)
    return result


def portable_device_models():
    shell = material('device_shell', '494e48', .73)
    rubber = material('device_rubber', '303632', .88)
    metal = material('device_metal', '707873', .48)
    strap = material('device_strap', '525247', .92)
    olive = material('device_olive', '73774d', .78)
    optics = material('device_optics', '3d6563', .27)
    reflector = material('device_reflector', '9a9f98', .42)
    amber = material('device_amber', '9e8250', .67)
    slots = [shell, rubber, metal, strap, olive, optics, reflector, amber]
    output = []

    def make_device(name, bone, build, lit_color=None):
        materials = slots.copy()
        if lit_color:
            materials.append(material('device_' + name + '_emitter', lit_color, .42))
        device = Garment('gear_device_' + name, materials)
        weights = {bone: 1}

        def loft(center, axis, profile, segments=16, capped=True):
            axis = Vector(axis).normalized()
            up = Vector((0, 0, 1)) if abs(axis.z) < .90 else Vector((0, 1, 0))
            across = up.cross(axis).normalized()
            up = axis.cross(across)
            start = len(device.vertices)
            for offset, width, height, _ in profile:
                for i in range(segments):
                    angle = math.tau * i / segments
                    device.vertices.append(Vector(center) + offset * axis
                                           + width * math.cos(angle) * across + height * math.sin(angle) * up)
                    device.weights.append(weights)
            for row, (_, _, _, finish) in enumerate(profile[1:]):
                for i in range(segments):
                    a = start + row * segments + i
                    b = start + row * segments + (i + 1) % segments
                    device.faces.append((a, b, b + segments, a + segments))
                    device.finish.append(finish)
            if capped:
                end = start + len(profile) * segments
                device.faces.extend((tuple(reversed(range(start, start + segments))), tuple(range(end - segments, end))))
                device.finish.extend((profile[0][3], profile[-1][3]))

        def tube(center, axis, profile, segments=16):
            loft(center, axis, [(offset, radius, radius, finish) for offset, radius, finish in profile], segments)

        def casing(center, width, height, depth, finish):
            loft(center, (0, -1, 0),
                 [(-depth / 2, width * .47, height * .47, finish),
                  (depth / 2, width * .47, height * .47, finish)], 8)

        build(tube, casing, loft)
        obj = device.object()
        obj.parent = rig
        obj.matrix_world = Matrix.Identity(4)
        output.append(obj)

    def flashlight(tube, center, axis):
        tube(center, axis, [(-.083, .016, 1), (-.079, .020, 0), (-.066, .020, 0),
                           (-.062, .016, 1), (.025, .016, 1), (.032, .019, 0),
                           (.048, .030, 0), (.069, .030, 0), (.076, .029, 2),
                           (.0765, .024, 1), (.077, .022, 6), (.078, .021, 8)])
        for offset in (-.056, -.043, -.030, -.017, -.004, .009, .022):
            tube(center, axis, [(offset, .017, 1), (offset + .003, .017, 1)], 12)

    def glowstick(tube, center, axis, his):
        length = .105 if his else .077
        radius = .015 if his else .009
        tube(center, axis, [(-length, radius * .74, 4), (-length + .006, radius, 4),
                           (-length + .019, radius, 4), (-length + .020, radius * .82, 4),
                           (length - .026, radius * .82, 8), (length - .025, radius, 4),
                           (length - .005, radius, 4), (length, radius * .77, 7)], 12)
        if his:
            for offset in (-length + .007, length - .022, length - .014):
                tube(center, axis, [(offset, radius * 1.04, 1), (offset + .004, radius * 1.04, 1)], 12)
        else:
            end = Vector(center) + length * Vector(axis)
            for i in range(8):
                # Петля подвеса видна по силуэту палочки.
                a = math.tau * i / 8
                point = end + Vector((.005 * math.cos(a), 0, 0)) + Vector(axis) * (.005 + .005 * math.sin(a))
                tube(point, axis, [(-.002, .002, 4), (.002, .002, 4)], 6)

    hand = rig.matrix_world @ rig.data.bones['Middle2.R'].head_local + Vector((.004, 0, -.035))
    belt = (-.187, -.165, 1.001)

    def belt_clip(casing, height):
        casing((belt[0], belt[1] + .027, belt[2] + height), .032, .038, .056, 3)

    for mount, center, bone in (('hand', hand, 'Wrist.R'), ('belt', belt, 'Body')):
        def portable_torch(tube, casing, _loft):
            if mount == 'belt':
                belt_clip(casing, .051)
            flashlight(tube, center, (0, -1, 0) if mount == 'hand' else (0, 0, -1))

        make_device('flashlight_' + mount, bone, portable_torch, 'a8a89a')
        for name in ('his', 'glowstick'):
            def portable_glow(tube, casing, _loft):
                if mount == 'belt':
                    belt_clip(casing, .091 if name == 'his' else .066)
                glowstick(tube, center, (0, -1, 0) if mount == 'hand' else (0, 0, 1), name == 'his')

            make_device(name + '_' + mount, bone, portable_glow, 'a8b57d')

    def headlamp(tube, casing, loft):
        # Эластичный ремень и шарнирная площадка за корпусом.
        loft((0, -.042, 1.765), (0, 0, 1),
             [(-.012, .114, .129, 3), (-.012, .118, .133, 3), (.012, .118, .133, 3),
              (.012, .114, .129, 3), (-.012, .114, .129, 3)], 32, capped=False)
        casing((0, -.164, 1.768), .091, .050, .032, 3)
        casing((0, -.180, 1.769), .089, .052, .039, 4)
        casing((.018, -.181, 1.796), .021, .009, .017, 7)
        tube((0, -.195, 1.768), (0, -1, 0), [(-.006, .021, 1), (.012, .028, 0),
                                             (.020, .027, 2), (.021, .022, 6), (.022, .019, 8)])

    make_device('headlamp', 'Head', headlamp, 'a8a89a')
    make_device('flashlight_visor', 'Head',
                lambda tube, _casing, _loft: flashlight(tube, (.116, -.133, 1.753), (0, -1, 0)), 'a8a89a')

    def night_vision(tube, casing, style, raised):
        casing((0, -.168, 1.796), .070, .037, .025, 2)
        tube((0, -.186, 1.786), (1, 0, 0), [(-.037, .013, 1), (.037, .013, 0)])
        height = 1.825 if raised else 1.710
        depth = -.206 if raised else -.215
        finish = 4 if style == 'pnv1p' else 0
        casing((0, -.193, (height + 1.786) / 2), .035, abs(height - 1.786) + .017, .028, finish)
        if style != 'sidr':
            casing((0, depth, height + .013), .158 if style == 'supernova' else .105, .032, .033, finish)
        if style == 'supernova':
            for side in (-1, 1):
                # Кронштейны доходят до тыльной части наружных окуляров в обоих положениях.
                casing((side * .073, depth + .010, height + .012), .082, .023, .033, finish)
        offsets = {'pnv1p': (-.039, .039), 'sidr': (-.033,),
                   'supernova': (-.103, -.037, .037, .103)}[style]
        for x in offsets:
            radius = .031 if style == 'sidr' else .024 if style == 'pnv1p' else .026
            axis = (0, -.6, .8) if raised else (0, -1, 0)
            center = (x, depth, height)
            tube(center, axis, [(-.026, radius * .90, 1), (-.015, radius * .90, 1),
                                (-.013, radius, finish), (.023, radius, finish), (.027, radius * 1.10, 2),
                                (.038, radius * 1.10, 1), (.0385, radius * .85, 1),
                                (.039, radius * .81, 5)])
        casing((.027, depth + .004, height + .032), .022, .017, .028, 7)

    for style in ('pnv1p', 'sidr', 'supernova'):
        for raised in (False, True):
            make_device(style + ('_raised' if raised else ''), 'Head',
                        lambda tube, casing, _loft: night_vision(tube, casing, style, raised))
    return output


def body_points():
    graph = bpy.context.evaluated_depsgraph_get()
    return [posed.matrix_world @ vertex.co for sources in parts.values() for obj in sources
            for posed in [obj.evaluated_get(graph)] for vertex in posed.data.vertices]


def sample_pose(joints, support, pose, center=None):
    reset()
    pose(joints, support)
    points = body_points()
    if center is None:
        center = Vector(((min(v.x for v in points) + max(v.x for v in points)) * .5,
                         (min(v.y for v in points) + max(v.y for v in points)) * .5, 0))
    root_pose = bones['Root'].matrix.copy()
    root_pose.translation += world_to_rig @ Vector((-center.x, -center.y, -min(v.z for v in points)))
    change_bone('Root', root_pose)
    return {bone.name: bone.matrix_basis.copy() for bone in bones}, center


def pose_frames(samples, pose):
    center = None
    frames = []
    for joints in samples:
        frame, center = sample_pose(joints, samples[0], pose, center)
        frames.append(frame)
    return frames


def directional_ankles(phase, direction, gait):
    stride, lift = {'walk': (.76, .085), 'run': (1.02, .135), 'sprint': (1.23, .16),
                    'crouch_walk': (.67, .075)}[gait]
    lateral = direction in {'left', 'right'}
    travel = Vector((1 if direction == 'left' else -1, 0, 0)) if lateral else Vector((0, 1, 0))
    if lateral:
        stride *= .65
    result = {}
    for side, sign in sides:
        progress = (phase + (.5 if side == 'R' else 0)) % 1
        support = .57
        swing = max(0, (progress - support) / (1 - support))
        step = stride * (support * .5 - min(progress, support) + support * ease(swing, 0, 1))
        rise = lift * math.sin(math.pi * swing)
        ankle = Vector((sign * (.18 if lateral else .131), .014 + (sign * .075 if lateral else 0),
                        ankles[side].z + rise)) + travel * step
        result[side] = (ankle, Vector((sign * .09, -1, -.19 * math.sin(2 * math.pi * swing))))
    if lateral:
        left, right = result['L'][0], result['R'][0]
        overlap = max(0, .162 - left.x + right.x) * .5
        left.x += overlap
        right.x -= overlap
    return result


def locomotion_frames(samples, gait, direction='forward'):
    crouching = gait == 'crouch_walk'
    running = gait in {'run', 'sprint'}
    lateral = direction in {'left', 'right'}

    def stride(joints, support):
        body = bones['Body'].matrix.copy()
        down = (.37 if crouching else .13 if lateral else .075) if direction != 'forward' else 0
        body.translation += world_to_rig @ Vector((joints['Hips'].x * .55, 0, -down))
        change_bone('Body', body)
        for bone, start, end, child in [('Abdomen', 'Hips', 'Spine1', 'Torso'),
                                       ('Torso', 'Spine1', 'Neck', 'Chest'),
                                       ('Neck', 'Neck', 'Head', 'Head')]:
            axis = joints[end] - joints[start]
            if direction != 'forward' and not crouching:
                axis.y *= .50
                if lateral:
                    axis.x += (.16 if direction == 'left' else -.16) * axis.z
            aim(bone, axis, child)
        aim('Head', (0, -.05 if crouching else -.10, 1))
        for side, sign in sides:
            prefix = 'Left' if side == 'L' else 'Right'
            thigh, shin = 'UpperLeg.' + side, 'LowerLeg.' + side
            knee = joints[prefix + 'Leg'] - joints[prefix + 'UpLeg']
            shank = joints[prefix + 'Foot'] - joints[prefix + 'Leg']
            if direction == 'forward':
                aim(thigh, knee, shin)
                aim(shin, shank)
                toe = joints[prefix + 'ToeBase'] - joints[prefix + 'Foot']
                match_boot(side, rig.matrix_world @ bones[shin].tail, toe, .45)
            else:
                ankle, toe = support[side]
                aim_chain(thigh, shin, None, ankle, Vector((sign * (.29 if lateral else .11), -1, -.05)))
                match_boot(side, rig.matrix_world @ bones[shin].tail, toe)

            arm, forearm, wrist = 'UpperArm.' + side, 'LowerArm.' + side, 'Wrist.' + side
            upper = joints[prefix + 'ForeArm'] - joints[prefix + 'Arm']
            lower = joints[prefix + 'Hand'] - joints[prefix + 'ForeArm']
            upper.x = sign * max(.11, sign * upper.x)
            lower.x = sign * max(-.04, sign * lower.x)
            aim(arm, upper, forearm)
            aim(forearm, lower, wrist)
            hand_direction = joints[prefix + 'HandTip'] - joints[prefix + 'Hand']
            if crouching and direction != 'forward':
                world = rig.matrix_world
                shoulder = world @ bones[arm].head
                swing = world @ bones[wrist].head - shoulder
                if lateral:
                    # При боковом шаге кисти проходят перед расходящимися коленями.
                    offset = (sign * .145 + .20 * swing.x, -.27 + .24 * swing.y,
                              -.275 + .25 * (swing.z + .39))
                    pole = (sign * .63, .42, -.65)
                    relaxed_hand = (sign * .10, 0, -.28)
                else:
                    # При заднем шаге кисти опущены к бёдрам и слегка качаются вдоль корпуса.
                    offset = (sign * .195 + .12 * swing.x, -.075 + .28 * swing.y,
                              -.38 + .15 * (swing.z + .39))
                    pole = (sign * .65, .50, -.28)
                    relaxed_hand = (sign * .08, .06, -.24)
                aim_chain(arm, forearm, wrist, shoulder + Vector(offset), Vector(pole))
                hand_direction = (world @ (bones[wrist].head - bones[forearm].head)).normalized()
                hand_direction += Vector(relaxed_hand)
            aim(wrist, hand_direction, 'Middle1.' + side)
            if running:
                hand = bones[wrist]
                curl_axis = rig.matrix_world.to_3x3() @ (hand.matrix.to_3x3() @ Vector((-1, 0, 0)))
                for finger in ('Index', 'Middle', 'Ring', 'Pinky'):
                    for segment, flex in [(1, .27), (2, .49), (3, .31)]:
                        pivot(f'{finger}{segment}.{side}', curl_axis, flex)

    # Координаты пути задаёт игра, поэтому цикл двигает скелет вокруг центра клетки.
    center = Vector((0, 0, 0))
    frames = []
    for index, joints in enumerate(samples[:-1]):
        support = None if direction == 'forward' else directional_ankles(index / (len(samples) - 1), direction, gait)
        frames.append(sample_pose(joints, support, stride, center)[0])
    return [*frames, frames[0]]


def ease(value, start, end):
    t = max(0, min(1, (value - start) / (end - start)))
    return t * t * (3 - 2 * t)


def pose_anchors(frame):
    for bone in bones:
        bone.matrix_basis = frame[bone.name]
    bpy.context.view_layer.update()
    world = rig.matrix_world
    feet = {}
    for side, _ in sides:
        shin = bones['LowerLeg.' + side]
        ankle = shin.tail.copy()
        boot = bones['Foot.' + side].matrix.copy()
        feet[side] = (world @ ankle, boot, boot.translation - ankle, world @ shin.head)
    hands = {side: (bones['Wrist.' + side].matrix.copy(), world @ bones['LowerArm.' + side].head)
             for side, _ in sides}
    return world @ bones['Body'].head, feet, hands


def place_hip(target):
    root_pose = bones['Root'].matrix.copy()
    root_pose.translation += world_to_rig @ (target - rig.matrix_world @ bones['Body'].head)
    change_bone('Root', root_pose)


def fit_boot_step(side, start, end, step, lift, knee_offset, bend_from_plane=0):
    thigh, shin, foot = ('UpperLeg.' + side, 'LowerLeg.' + side, 'Foot.' + side)
    ankle_start, boot_start, offset_start, knee_start = start
    ankle_end, boot_end, offset_end, knee_end = end
    ankle = ankle_start.lerp(ankle_end, step)
    ankle.z += lift
    boot = boot_start.lerp(boot_end, step)
    offset = offset_start.lerp(offset_end, step)
    knee = knee_start.lerp(knee_end, step) + knee_offset
    hip = rig.matrix_world @ bones[thigh].head
    pole = knee - hip
    if bend_from_plane:
        axis = (ankle - hip).normalized()
        sign = 1 if side == 'L' else -1
        # Направление сгиба поворачивается вместе с ногой, пока стопа проходит под тазом.
        bend = Vector((sign * .11, axis.z, -axis.y))
        bend -= axis * bend.dot(axis)
        pole -= axis * pole.dot(axis)
        pole = pole.normalized().lerp(bend.normalized(), bend_from_plane)
    for _ in range(3):
        aim_chain(thigh, shin, None, ankle, pole)
        boot.translation = bones[shin].tail + offset
        change_bone(foot, boot)
        ankle.z += lift - boot_levels()[side]


def fall_to_back(start, landed, samples, crouching):
    world = rig.matrix_world
    start_hip, start_feet, start_hands = pose_anchors(start)
    end_hip, end_feet, end_hands = pose_anchors(landed)
    # Сокращаем подготовку перед потерей опоры, оставляем отдельные кадры удара и оседания.
    indices = ([5, 9, 11, 12, 13, 14, 15, 16, 17, 18.5, 20, 22, 24, 25] if crouching else
               [2, 5, 8.5, 11, 12.5, 13.3, 14, 14.75, 15.6, 16.5, 17.5, 19, 20.5, 22, 24, 25])
    frames = [start]
    for index in indices:
        key = int(index)
        joints = {name: point.lerp(samples[key + 1][name], index - key) for name, point in samples[key].items()}
        reset()
        fall_back(joints, samples[0])
        enter = ease(index, 8, 14) if crouching else ease(index, 0, 11)
        settle = ease(index, 16, 23)
        for bone in bones:
            captured = start[bone.name].lerp(bone.matrix_basis, enter)
            bone.matrix_basis = captured.lerp(landed[bone.name], settle)
        bpy.context.view_layer.update()
        # Корпус теряет опору вместе с тазом. Одно плечо касается пола чуть раньше другого.
        lean = ease(index, 0, 9) * (1 - ease(index, 13, 20))
        pivot('Root', (1, 0, 0), -(.27 if crouching else .53) * lean)
        roll = ease(index, 8, 17) * (1 - ease(index, 17, 23))
        pivot('Torso', (0, 1, 0), -.14 * roll)

        # Таз проваливается первым. Грудь и плечи догоняют его при ударе, ноги оседают следом.
        travel = max(0, min(1, joints['Hips'].y / samples[-1]['Hips'].y))
        drop = joints['Hips'].z / samples[-1]['Hips'].z
        hip = start_hip.lerp(end_hip, travel)
        hip.z = start_hip.z + (end_hip.z - start_hip.z) * drop
        place_hip(hip)
        for side, sign in sides:
            arm, forearm, wrist = ('UpperArm.' + side, 'LowerArm.' + side, 'Wrist.' + side)
            reach = ease(index, 4, 17 if side == 'L' else 18.5)
            spread = math.sin(math.pi * reach)
            hand_start, elbow_start = start_hands[side]
            hand_end, elbow_end = end_hands[side]
            hand = hand_start.lerp(hand_end, reach)
            target = world @ hand.translation + Vector((sign * .09 * spread, .07 * spread, 0))
            elbow = elbow_start.lerp(elbow_end, reach) + Vector((sign * .15 * spread, 0, -.08 * spread))
            aim_chain(arm, forearm, wrist, target, elbow - world @ bones[arm].head)
            hand.translation = bones[wrist].head
            change_bone(wrist, hand)

            step_start = (5 if side == 'R' else 11) if crouching else (8 if side == 'R' else 9.5)
            step = ease(index, step_start, 22 if side == 'R' else 23)
            arc = math.sin(math.pi * step)
            lift = (.047 if side == 'R' else .034) * arc * ease(index, 12, 16)
            knee_offset = Vector((sign * .07 * arc, -.17 * arc, .24 * arc))
            bend = ease(index, 0, 9 if crouching else 4) * (1 - ease(index, 20, 24))
            fit_boot_step(side, start_feet[side], end_feet[side], step, lift, knee_offset, bend)

        clearance = min(point.z for point in body_points())
        if clearance < 0:
            hip.z -= clearance
            place_hip(hip)
        frames.append({bone.name: bone.matrix_basis.copy() for bone in bones})
    return [*frames, landed]


def prone_breath_frames(ready):
    for bone in bones:
        bone.matrix_basis = ready[bone.name]
    bpy.context.view_layer.update()
    world = rig.matrix_world
    arms = []
    for side, _ in sides:
        arm, forearm, wrist = ('UpperArm.' + side, 'LowerArm.' + side, 'Wrist.' + side)
        target = world @ bones[wrist].head
        pole = world @ bones[forearm].head - world @ bones[arm].head
        arms.append((arm, forearm, wrist, target, pole, bones[wrist].matrix.copy()))

    steps = round(3.6 * clip_fps)
    frames = [ready]
    for index in range(1, steps):
        for bone in bones:
            bone.matrix_basis = ready[bone.name]
        bpy.context.view_layer.update()
        breath = math.sin(math.pi * index / steps) ** 2
        pivot('Torso', (1, 0, 0), -.028 * breath)
        # Вес остаётся на кистях и берцах, пока движется грудная клетка.
        for arm, forearm, wrist, target, pole, hand in arms:
            aim_chain(arm, forearm, wrist, target, pole)
            change_bone(wrist, hand)
        frames.append({bone.name: bone.matrix_basis.copy() for bone in bones})
    return [*frames, ready]


def prone_crawl_frames(ready, samples, direction='forward'):
    world = rig.matrix_world
    hip, feet, hands = pose_anchors(ready)
    ranges = {name: (min(sample[name].y for sample in samples), max(sample[name].y for sample in samples))
              for side in ('Left', 'Right') for name in (side + 'Hand', side + 'Foot')}
    frames = []
    sideways = direction in {'left', 'right'}
    travel = Vector((1 if direction == 'left' else -1, 0, 0)) if sideways else Vector((0, 1 if direction == 'back' else -1, 0))
    for joints in samples[:-1]:
        for bone in bones:
            bone.matrix_basis = ready[bone.name]
        bpy.context.view_layer.update()
        sway = (joints['Chest'] - joints['Hips']).x
        body = hip + Vector((sway * .42, 0, -.025))
        place_hip(body)
        pivot('Torso', (0, 0, 1), sway * .85)
        clearance = mesh_floor(parts['top_gorka'] + parts['top_sweater'])
        if clearance < 0:
            body.z -= clearance
            place_hip(body)
        hand_floor = min(joints['LeftHand'].z, joints['RightHand'].z)
        foot_floor = min(joints['LeftFoot'].z, joints['RightFoot'].z)
        for side, sign in sides:
            prefix = 'Left' if side == 'L' else 'Right'
            arm, forearm, wrist = ('UpperArm.' + side, 'LowerArm.' + side, 'Wrist.' + side)
            hand_name = prefix + 'Hand'
            hand = joints[hand_name]
            min_y, max_y = ranges[hand_name]
            lift = (hand.z - hand_floor) * .36
            palm, elbow = hands[side]
            palm = palm.copy()
            reach = ((min_y + max_y) * .5 - hand.y) * (.55 if sideways else .78)
            target = world @ palm.translation + Vector((sign * (.082 if sideways else .047), -.045, lift)) + travel * reach
            pole = elbow - world @ bones[arm].head + Vector((sign * .09, 0, -.10))
            for _ in range(2):
                aim_chain(arm, forearm, wrist, target, pole)
                palm.translation = bones[wrist].head
                change_bone(wrist, palm)
                target.z += lift - side_levels(parts['base'][0])[side]

            foot_name = prefix + 'Foot'
            foot = joints[foot_name]
            min_y, max_y = ranges[foot_name]
            draw = (max_y - foot.y) / (max_y - min_y)
            ankle, boot, offset, knee = feet[side]
            # При обратном ползании нога всё равно сгибается вперёд под таз.
            if direction == 'back':
                draw = 1 - draw
            advance = Vector((sign * .095, -.19, 0))
            if sideways:
                advance.x += travel.x * .085
                advance.y = -.11
            forward = (ankle + advance, boot, offset, knee)
            fit_boot_step(side, feet[side], forward, draw, (foot.z - foot_floor) * .24,
                          Vector((sign * draw * .20, -draw * .10, 0)))
        frames.append({bone.name: bone.matrix_basis.copy() for bone in bones})
    return [*frames, frames[0]]


def crouch_to_prone_frames(crouch, ready):
    samples = accad_samples('Male2_A8_CrouchToLie.bvh', range(44, 109, 2))
    world = rig.matrix_world
    start_hip, start_feet, start_hands = pose_anchors(crouch)
    end_hip, end_feet, end_hands = pose_anchors(ready)
    frames = [crouch]
    for index, joints in enumerate(samples[1:-1], 1):
        progress = index / (len(samples) - 1)
        # Последний кадр сразу передаёт позу циклу покоя.
        if progress >= .89:
            break
        torso = joints['Neck'] - joints['Spine1']
        pitch = math.atan2(-torso.y, torso.z)
        forward = .10 * ease(pitch, .94, 1.56) + .90 * ease(progress, 0, .57)
        for bone in bones:
            bone.matrix_basis = crouch[bone.name].lerp(ready[bone.name], forward)
        bpy.context.view_layer.update()
        # Наклоняем грудь к опоре на руки, оставляя таз выше коленей, пока ноги уходят назад.
        brace = ease(progress, .07, .39) * (1 - ease(progress, .60, .89))
        pivot('Abdomen', (1, 0, 0), .22 * brace)
        pivot('Torso', (1, 0, 0), .15 * brace)
        shift = ease(progress, .08, .86)
        hip = start_hip.lerp(end_hip, shift)
        hip.x += joints['Hips'].x * .17 * (1 - shift)
        hip.z = start_hip.z + (.581 - start_hip.z) * ease(progress, .02, .30)
        hip.z += (end_hip.z - .581) * ease(progress, .53, .89)
        place_hip(hip)

        for side, sign in sides:
            arm, forearm, wrist = ('UpperArm.' + side, 'LowerArm.' + side, 'Wrist.' + side)
            reach = ease(progress, .02 if side == 'R' else .06, .37 if side == 'R' else .41)
            hand_start, elbow_start = start_hands[side]
            hand_end, elbow_end = end_hands[side]
            hand = hand_start.lerp(hand_end, reach)
            target = world @ hand.translation
            target.y += .16 * reach * (1 - ease(progress, .52, .80))
            target.z += .04 * math.sin(math.pi * reach)
            elbow = elbow_start.lerp(elbow_end, reach)
            elbow.x += sign * .15 * math.sin(math.pi * reach)
            aim_chain(arm, forearm, wrist, target, elbow - world @ bones[arm].head)
            hand.translation = bones[wrist].head
            change_bone(wrist, hand)

            # Передняя нога освобождает место под грудью, затем вытягивается задняя.
            step = ease(progress, .18, .64) if side == 'L' else ease(progress, .37, .75)
            arc = math.sin(math.pi * step)
            bend = ease(progress, .02, .20) * (1 - ease(progress, .72, .88))
            fit_boot_step(side, start_feet[side], end_feet[side], step, (.040 if side == 'L' else .025) * arc,
                          Vector((sign * .035 * arc, 0, 0)), bend)
        frames.append({bone.name: bone.matrix_basis.copy() for bone in bones})
    return [*frames, ready]


def prone_to_crouch_frames(ready, crouch):
    world = rig.matrix_world
    start_hip, start_feet, start_hands = pose_anchors(ready)
    end_hip, end_feet, end_hands = pose_anchors(crouch)
    steps = round(2 * clip_fps)
    frames = [ready]
    for index in range(1, steps):
        progress = index / steps
        upright_weight = ease(progress, .44, .96)
        for bone in bones:
            bone.matrix_basis = ready[bone.name].lerp(crouch[bone.name], upright_weight)
        bpy.context.view_layer.update()
        brace = ease(progress, .05, .35) * (1 - ease(progress, .54, .82))
        pivot('Abdomen', (1, 0, 0), .23 * brace)
        pivot('Torso', (1, 0, 0), .16 * brace)
        hip = start_hip.lerp(end_hip, ease(progress, .13, .82))
        hip.z = start_hip.z + (.576 - start_hip.z) * ease(progress, .01, .42)
        hip.z += (end_hip.z - .576) * ease(progress, .56, .88)
        hip.x -= .035 * math.sin(math.pi * ease(progress, .08, .85))
        place_hip(hip)

        for side, sign in sides:
            # Сначала подтягивается задняя нога. Передняя стопа встаёт под корпус, пока ладони ещё держат вес.
            step = ease(progress, .30, .72) if side == 'L' else ease(progress, .21, .54)
            arc = math.sin(math.pi * step)
            bend = ease(progress, .01, .19) * (1 - ease(progress, .76, .95))
            fit_boot_step(side, start_feet[side], end_feet[side], step,
                          (.045 if side == 'L' else .024) * arc, Vector((sign * .035 * arc, 0, 0)), bend)

            arm, forearm, wrist = ('UpperArm.' + side, 'LowerArm.' + side, 'Wrist.' + side)
            reach = ease(progress, .59, .90) if side == 'L' else ease(progress, .64, .96)
            hand_start, elbow_start = start_hands[side]
            hand_end, elbow_end = end_hands[side]
            hand = hand_start.lerp(hand_end, reach)
            target = world @ hand.translation
            # По очереди переставляем ладони ближе к плечам перед толчком.
            replant = ease(progress, .07, .21) if side == 'L' else ease(progress, .17, .32)
            target.y += .12 * replant * (1 - reach)
            target.z += .025 * math.sin(math.pi * replant)
            elbow = elbow_start.lerp(elbow_end, reach) + Vector((sign * .10 * math.sin(math.pi * reach), 0, 0))
            aim_chain(arm, forearm, wrist, target, elbow - world @ bones[arm].head)
            hand.translation = bones[wrist].head
            change_bone(wrist, hand)
        frames.append({bone.name: bone.matrix_basis.copy() for bone in bones})
    return [*frames, crouch]


def standing_to_crouch_frames(standing, crouch):
    start_hip, start_feet, _ = pose_anchors(standing)
    end_hip, end_feet, _ = pose_anchors(crouch)
    steps = round(1.13 * clip_fps)
    frames = [standing]
    for index in range(1, steps):
        progress = index / steps
        weight = ease(progress, .04, .95)
        for bone in bones:
            bone.matrix_basis = standing[bone.name].lerp(crouch[bone.name], weight)
        bpy.context.view_layer.update()
        hip = start_hip.lerp(end_hip, weight)
        # Вес переходит на переднюю стопу, когда задняя отставляется и поворачивается на носок.
        hip.x -= .031 * math.sin(math.pi * ease(progress, 0, .31))
        hip.x += .059 * math.sin(math.pi * ease(progress, .20, .77))
        place_hip(hip)
        for side, _ in sides:
            step = ease(progress, .04, .36) if side == 'L' else ease(progress, .29, .75)
            arc = math.sin(math.pi * step)
            bend = ease(progress, .01, .21) * (1 - ease(progress, .79, .97))
            fit_boot_step(side, start_feet[side], end_feet[side], step, (.024 if side == 'L' else .047) * arc,
                          Vector((0, 0, 0)), bend)
        frames.append({bone.name: bone.matrix_basis.copy() for bone in bones})
    return [*frames, crouch]


def join_through_crouch(first, second):
    # Срезаем замедление у стыка и продолжаем перенос веса через короткую фазу приседа.
    before, after = first[:-3], second[4:]
    start, end = before[-1], after[0]
    _, start_feet, _ = pose_anchors(start)
    _, end_feet, _ = pose_anchors(end)
    join = []
    for weight in (1 / 3, 2 / 3):
        for bone in bones:
            bone.matrix_basis = start[bone.name].lerp(end[bone.name], weight)
        bpy.context.view_layer.update()
        for side, _ in sides:
            fit_boot_step(side, start_feet[side], end_feet[side], weight, 0, Vector((0, 0, 0)))
        join.append({bone.name: bone.matrix_basis.copy() for bone in bones})
    return [*before, *join, *after]


def stash_clip(name, frames, offset):
    anim = rig.animation_data
    action = bpy.data.actions.new(name)
    anim.action = action
    rotations = {}
    for frame, transforms in enumerate(frames):
        for bone in bones:
            location, rotation, scale = transforms[bone.name].decompose()
            if bone.name in rotations:
                rotation.make_compatible(rotations[bone.name])
            rotations[bone.name] = rotation.copy()
            bone.rotation_mode = 'QUATERNION'
            bone.location = location
            bone.rotation_quaternion = rotation
            bone.scale = scale
            for channel in ('location', 'rotation_quaternion', 'scale'):
                bone.keyframe_insert(data_path=channel, frame=frame, group=bone.name)
    for curve in action.fcurves:
        for key in curve.keyframe_points:
            key.interpolation = 'LINEAR'
    anim.action = None
    track = anim.nla_tracks.new()
    track.name = name
    track.strips.new(name, offset, action)
    return offset + len(frames) + clip_fps


if '--fall-animation' in sys.argv:
    # Отдельный GLB хранит только движения на том же скелете. Детали и материалы не пересобираются.
    fall_samples, stand_samples, crouch_samples = quaternius_samples(('Death01', 'Idle_Loop', 'Crouch_Idle_Loop'), loop=False)
    front, = accad_samples('Male2_A9_Lie.bvh', [8])
    _, end_center = sample_pose(fall_samples[-1], fall_samples[0], grounded_back)
    end_center.y -= .25
    settled, _ = sample_pose(fall_samples[-1], fall_samples[0], grounded_back, end_center)
    back_head_parts = ('face_calm', 'face_wounded', 'hair_short')
    settled, = fit_contact_frames([settled], head_parts=back_head_parts)
    stand, _ = sample_pose(stand_samples[0], stand_samples[0],
                            lambda joints, support: upright(joints, support, False))
    crouch, _ = sample_pose(crouch_samples[0], crouch_samples[0],
                            lambda joints, support: upright(joints, support, True))
    fall = fall_to_back(stand, settled, fall_samples, False)
    crouch_fall = fall_to_back(crouch, settled, fall_samples, True)
    def prone_pose(joints, support):
        ground_move(joints, support, resting=True)
        pivot('Torso', (1, 0, 0), .21)
        pivot('Chest', (1, 0, 0), .08)
        floor = (rig.matrix_world @ bones['Body'].head).z - .21
        plant_prone_hands(floor, rest_on_elbows=True)
        aim('Neck', (0, -.36, .93), 'Head')
        aim('Head', (0, -.08, 1))

    def relaxed_prone_pose(joints, support):
        ground_move(joints, support, resting=True)
        pivot('Torso', (1, 0, 0), .24)
        pivot('Chest', (1, 0, 0), .13)
        aim('Neck', (0, -1, .12), 'Head')
        aim('Head', (0, -1, .12))
        pivot('Head', (0, -1, 0), .46)
        floor = (rig.matrix_world @ bones['Body'].head).z - .21
        plant_prone_hands(floor, relaxed=True, rest_on_elbows=True)
        floor = mesh_floor(parts['base'] + parts['top_sweater'] + parts['bottom_jeans'])
        tilt_head(*head_drop_target(floor, 1))

    ready, front_center = sample_pose(front, front, prone_pose)
    relaxed, _ = sample_pose(front, front, relaxed_prone_pose, front_center)
    ready, = fit_contact_frames([ready], limbs=True, fit_head=False)
    relaxed, = fit_contact_frames([relaxed], limbs=True)
    prone_idle = prone_breath_frames(ready)
    crouch_to_prone = crouch_to_prone_frames(crouch, ready)
    prone_to_crouch = prone_to_crouch_frames(ready, crouch)
    standing_to_crouch = standing_to_crouch_frames(stand, crouch)
    crouch_to_standing = standing_to_crouch[::-1]
    standing_to_prone = join_through_crouch(standing_to_crouch, crouch_to_prone)
    prone_to_standing = join_through_crouch(prone_to_crouch, crouch_to_standing)
    movement_sources = [('walk', 'Walk_Loop'), ('run', 'Jog_Fwd_Loop'),
                        ('sprint', 'Sprint_Loop'), ('crouch_walk', 'Crouch_Fwd_Loop')]
    movement_samples = quaternius_samples([source for _, source in movement_sources], loop=True)
    crawl_samples = kaykit_crawl_samples()
    movement = []
    for direction in ('forward', 'left', 'right', 'back'):
        suffix = '' if direction == 'forward' else '_' + direction
        movement.extend((name + suffix, locomotion_frames(samples, name, direction))
                        for (name, _), samples in zip(movement_sources, movement_samples))
        movement.append(('crawl' + suffix, prone_crawl_frames(ready, crawl_samples, direction)))
    front_drop = []
    for step in range(10):
        progress = step / 9
        blend = progress ** 2 * (3 - 2 * progress)
        front_drop.append({name: matrix.lerp(relaxed[name], blend) for name, matrix in ready.items()})
    back_drop = back_head_drop(settled)
    for name, frame in [('на спине', settled), ('лёжа в сознании', ready), ('на животе', relaxed)]:
        for bone in bones:
            bone.matrix_basis = frame[bone.name]
        bpy.context.view_layer.update()
        hands = side_levels(parts['base'][0])
        boots_at_rest = boot_levels()
        head_at_rest = mesh_floor(parts['face_calm'])
        chest_height = (rig.matrix_world @ bones['Chest'].head).z
        neck_length = (rig.matrix_world.to_3x3() @ (bones['Head'].head - bones['Neck'].head)).length
        print(name, 'руки', hands, 'ноги', boots_at_rest, 'голова', head_at_rest,
              'грудь', chest_height, 'шея', neck_length, flush=True)
    reset()
    rig.name = 'FieldRig'
    for obj in list(scene.objects):
        if obj is not rig:
            bpy.data.objects.remove(obj, do_unlink=True)
    for source_action in list(bpy.data.actions):
        bpy.data.actions.remove(source_action)
    scene.render.fps = clip_fps
    rig.animation_data_create()
    offset = 1
    for name, frames in [('fall_back', fall), ('fall_back_crouch', crouch_fall), ('prone_idle', prone_idle),
                         ('prone_drop', front_drop), ('prone_back_drop', back_drop),
                         ('crouch_to_prone', crouch_to_prone), ('prone_to_crouch', prone_to_crouch),
                         ('standing_to_crouch', standing_to_crouch), ('crouch_to_standing', crouch_to_standing),
                         ('standing_to_prone', standing_to_prone), ('prone_to_standing', prone_to_standing), *movement]:
        offset = stash_clip(name, frames, offset)
    scene.frame_end = offset - clip_fps - 1
    scene.frame_set(1)
    bpy.ops.object.select_all(action='DESELECT')
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    output = pathlib.Path(sys.argv[sys.argv.index('--fall-animation') + 1]).resolve()
    bpy.ops.export_scene.gltf(filepath=str(output), export_format='GLB', use_selection=True,
                              export_animation_mode='ACTIONS')
    sys.exit(0)

# Движения Quaternius (CC0) и Open Motion Project, ACCAD / The Ohio State University (CC BY 3.0).
stand_samples, crouch_samples = quaternius_samples(('Idle_Loop', 'Crouch_Idle_Loop'), loop=True)
prone_samples = accad_samples('Male2_A9_Lie.bvh', range(8, 81, 2))
prone_samples += list(reversed(prone_samples[:-1]))
standing = pose_frames(stand_samples, lambda joints, support: upright(joints, support, False))
sitting = pose_frames(crouch_samples, lambda joints, support: upright(joints, support, True))
prone = pose_frames(prone_samples, lambda joints, support: ground_move(joints, support, resting=True))

reset()
output = [export_part(kind, sources) for kind, sources in parts.items()] + [
    mp50_model(), mp50_ear_seals(), *gas_mask_models(), helmet_68g_model(), *tactical_helmets(), *respirator_helmets(),
    *visor_helmets(), *soft_headwear_models(), *portable_device_models(),
    ushanka_model(), ushanka_star_model(), *balaclava_models(),
]
for sources in parts.values():
    for obj in sources:
        bpy.data.objects.remove(obj, do_unlink=True)

scene.render.fps = clip_fps
scene.frame_start = 1
rig.name = 'FieldRig'
for source_action in list(bpy.data.actions):
    bpy.data.actions.remove(source_action)
rig.animation_data_create()
offset = 1
for name, frames in [('standing', standing), ('sitting', sitting), ('prone', prone)]:
    offset = stash_clip(name, frames, offset)
    print(name, len(frames), 'кадров', flush=True)
scene.frame_end = offset - clip_fps - 1
scene.frame_set(1)

bpy.ops.object.select_all(action='DESELECT')
for obj in [rig, *output]:
    obj.select_set(True)
bpy.context.view_layer.objects.active = rig
bpy.ops.export_scene.gltf(filepath=str(root/'app/static/models/characters/field_male.glb'), export_format='GLB',
    use_selection=True, export_animation_mode='ACTIONS')

notes = bpy.data.texts.new('Детали персонажа')
notes.write('Один скелет, сменные детали и три клипа. Ключевые кадры доступны в редакторе действий.\n'
            'Риг, кисти, голова, волосы, циклы стоя и в приседе: Quaternius (CC0).\n'
            'Положение на животе: ACCAD / The Ohio State University (CC BY 3.0).\n'
            'Котелок 68Г: модель проекта, геометрия построена по фото пользователя.\n')
for obj in output:
    obj.hide_set(obj.name.startswith('gear_') or obj.name in {'top_gorka', 'top_leather_coat', 'top_trench_coat',
                 'bottom_gorka', 'bottom_tactical', 'bottom_hunting', 'face_wounded'})
bpy.context.preferences.filepaths.save_version = 0
work = root / '.private-testing/character-poses'
work.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(work / 'field-male.blend'))
