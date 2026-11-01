"""Пересечение луча с теми же верхними треугольниками, что locationTerrain.js.
Числа полос/склонов перенесены из существующей геометрии renderer, не из правил боя.
"""
import math

EPS = 1e-6


class TerrainMesh:
    def __init__(self, tiles):
        self.tiles = tiles or []
        self.cache = {}

    def land(self, x, z):
        return (0 <= z < len(self.tiles) and 0 <= x < len(self.tiles[z])
                and self.tiles[z][x].get("terrain") != "water")

    def height(self, x, z):
        try:
            value = float(self.tiles[z][x].get("height", 1))
            return value if math.isfinite(value) else 1
        except (TypeError, ValueError):
            return 1

    def blend(self, ax, az, bx, bz):
        return abs(ax - bx) + abs(az - bz) == 1 and self.land(ax, az) and self.land(bx, bz)

    def ring(self, x, z):
        height = self.height(x, z)
        smooth = any(self.land(x+dx, z+dz) and abs(height-self.height(x+dx, z+dz)) > EPS
                     for dz in (-1, 0, 1) for dx in (-1, 0, 1))
        def corner(dx, dz):
            if not smooth:
                return height
            candidates = [(cx, cz) for cz in (z+dz-1, z+dz) for cx in (x+dx-1, x+dx)
                          if self.land(cx, cz)]
            connected, queue = {(x, z)}, [(x, z)]
            for cx, cz in queue:
                for nx, nz in candidates:
                    if (nx, nz) not in connected and self.blend(cx, cz, nx, nz):
                        connected.add((nx, nz)); queue.append((nx, nz))
            return sum(self.height(cx, cz) for cx, cz in sorted(connected)) / len(connected)
        def edge(nx, nz):
            return (height+self.height(nx, nz))/2 if smooth and self.blend(x, z, nx, nz) else height
        return [(x,corner(0,0),z),(x+.5,edge(x,z-1),z),(x+1,corner(1,0),z),
                (x+1,edge(x+1,z),z+.5),(x+1,corner(1,1),z+1),
                (x+.5,edge(x,z+1),z+1),(x,corner(0,1),z+1),(x,edge(x-1,z),z+.5)]

    def triangles(self, x, z):
        if (x, z) in self.cache:
            return self.cache[x, z]
        if not self.land(x, z):
            return [], -math.inf
        height, ring = self.height(x, z), self.ring(x, z)
        center = (x+.5, height, z+.5)
        steep = any(self.land(x+dx,z+dz) and abs(height-self.height(x+dx,z+dz)) > 1
                    for dz in (-1,0,1) for dx in (-1,0,1))
        sand = self.tiles[z][x].get("terrain") == "sand"
        bands = [(.28,.04),(.48,.19),(.68,.47),(.85,.76),(1,1)] if sand else [(.42,.09),(.76,.66),(1,1)]
        outline = []
        for i, point in enumerate(ring):
            outline.append(point)
            if steep and sand:
                nxt = ring[(i+1) % len(ring)]
                outline.append(tuple((a+b)/2 for a,b in zip(point,nxt)))
        inner = [[(center[0]+(px-center[0])*radius, height+(py-height)*falloff,
                   center[2]+(pz-center[2])*radius) for px,py,pz in outline]
                 for radius,falloff in bands] if steep else None
        triangles = []
        for side in range(len(outline)):
            nxt = (side+1) % len(outline)
            if inner:
                triangles.append((center,inner[0][nxt],inner[0][side]))
                for near, far in zip(inner,inner[1:]):
                    triangles.extend([(near[side],near[nxt],far[nxt]),(near[side],far[nxt],far[side])])
            else:
                triangles.append((center,ring[nxt],ring[side]))
        result = triangles, max(p[1] for triangle in triangles for p in triangle)
        self.cache[x,z] = result
        return result

    def first_hit(self, start, end, accept=lambda hit: True):
        direction = tuple(b-a for a,b in zip(start,end))
        dx, dz = direction[0], direction[2]
        x,z = math.floor(start[0]),math.floor(start[2])
        sx,sz = (1 if dx>0 else -1),(1 if dz>0 else -1)
        tx = ((x+1 if dx>0 else x)-start[0])/dx if dx else math.inf
        tz = ((z+1 if dz>0 else z)-start[2])/dz if dz else math.inf
        dtx,dtz = (abs(1/dx) if dx else math.inf),(abs(1/dz) if dz else math.inf)
        entered = 0
        limit = len(self.tiles) + max((len(row) for row in self.tiles), default=0) + 4
        for _ in range(limit):
            leaving = min(tx,tz,1)
            triangles, maximum = self.triangles(x,z)
            if maximum >= min(start[1]+direction[1]*entered,start[1]+direction[1]*leaving)-EPS:
                best = None
                for triangle in triangles:
                    t = triangle_hit(start,direction,*triangle)
                    if t is not None:
                        hit = {"t":t,"x":start[0]+dx*t,"z":start[2]+dz*t,"height":start[1]+direction[1]*t}
                        if accept(hit) and (best is None or t<best["t"]):
                            best = hit
                if best:
                    return best
            if leaving >= 1:
                break
            entered = leaving
            if tx <= leaving:
                x += sx; tx += dtx
            if tz <= leaving:
                z += sz; tz += dtz
        return None


def triangle_hit(origin, direction, a, b, c):
    def sub(u,v): return tuple(x-y for x,y in zip(u,v))
    def cross(u,v): return (u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0])
    def dot(u,v): return sum(x*y for x,y in zip(u,v))
    edge1,edge2 = sub(b,a),sub(c,a)
    p = cross(direction,edge2);det = dot(edge1,p)
    if abs(det)<1e-9:
        return None
    offset = sub(origin,a);u = dot(offset,p)/det
    if u < -EPS or u>1+EPS:
        return None
    q = cross(offset,edge1);v = dot(direction,q)/det
    if v < -EPS or u+v>1+EPS:
        return None
    t = dot(edge2,q)/det
    return t if EPS<t<1-EPS else None
