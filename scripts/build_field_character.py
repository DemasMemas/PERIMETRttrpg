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
    return [calm, lips(False)], [wounded, lips(True)], [hair]


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
    'top_sweater': bake_material('sweater_fabric', knit_textures(), .31),
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
coat_buttons = {
    'top_leather_coat': material('coat_buttons', '797570', .58),
    'top_trench_coat': material('trench_buttons', '897451', .66),
}


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
        if kind in coat_buttons:
            mesh.materials.append(coat_buttons[kind])
        for polygon, finish in zip(mesh.polygons, finishes):
            for index in polygon.loop_indices:
                attr.data[index].color = palette[finish]
            polygon.material_index = int(kind in coat_buttons and finish == 3)
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
        copies.append(copy)
    bpy.ops.object.select_all(action='DESELECT')
    for copy in copies:
        copy.select_set(True)
    bpy.context.view_layer.objects.active = copies[0]
    if len(copies) > 1:
        bpy.ops.object.join()
    result = bpy.context.object
    result.name = kind
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
    mask.parent = rig
    mask.matrix_world = Matrix.Identity(4)
    mask.name = 'gear_mp50'
    mask.vertex_groups.new(name='Head').add(list(range(len(mask.data.vertices))), 1, 'REPLACE')
    mask.modifiers.new('Общий скелет', 'ARMATURE').object = rig
    albedos = {link.from_node.image for material in mask.data.materials
               for node in material.node_tree.nodes if node.type == 'BSDF_PRINCIPLED'
               for link in node.inputs['Base Color'].links if link.from_node.type == 'TEX_IMAGE'}
    for obj in imported - {mask}:
        bpy.data.objects.remove(obj, do_unlink=True)
    for image in set(bpy.data.images) - images:
        width, height = image.size
        if max(width, height) > 256:
            scale = 256 / max(width, height)
            image.scale(round(width * scale), round(height * scale))
        if image in albedos:
            # Полутона исходной карты слишком тёмные для рассеянного игрового света.
            pixels = array('f', image.pixels[:])
            for index, value in enumerate(pixels):
                if index % 4 != 3:
                    pixels[index] = value + .23 * min(1, value / .14) * (1 - value) ** 2
            image.pixels.foreach_set(pixels)
        image.pack()
    return mask


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
            aim(wrist, joints[prefix + 'HandTip'] - joints[prefix + 'Hand'], 'Middle1.' + side)
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
output = [export_part(kind, sources) for kind, sources in parts.items()] + [mp50_model()]
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
            'Положение на животе: ACCAD / The Ohio State University (CC BY 3.0).\n')
for obj in output:
    obj.hide_set(obj.name in {'top_gorka', 'top_leather_coat', 'top_trench_coat', 'bottom_gorka', 'bottom_tactical', 'bottom_hunting',
                             'face_wounded', 'gear_mp50'})
bpy.context.preferences.filepaths.save_version = 0
work = root / '.private-testing/character-poses'
work.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(work / 'field-male.blend'))
