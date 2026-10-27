#!/usr/bin/env python3
"""Render reed_buildings_detailed.glb with three.js r128 in headless Chromium (SwiftShader) -> out/preview/*.png

    python3 world/buildings/kit/render_preview.py [--modules]

Needs: playwright (python) + chromium, and three@0.128.0 from npm (`cd /tmp && npm i three@0.128.0`, or set
THREE_DIR). The page loads three.js from cdnjs and GLTFLoader from jsdelivr; both requests are routed to the
local npm copy, so no network is needed.
"""
import functools
import http.server
import json
import math
import os
import sys
import threading

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
OUT = os.path.join(ROOT, 'world', 'buildings', 'out')
PREV = os.path.join(OUT, 'preview')
THREE = os.environ.get('THREE_DIR', '/tmp/node_modules/three')
W, H = 1280, 800


def gl(x, y, z):
    return [x, z, -y]


def building_views():
    blds = {b['osm_id']: b for b in json.load(open(os.path.join(ROOT, 'world', '3d', 'buildings.json')))['buildings']}

    def view(osm, az, elev=14, dist=1.15, lift=0.45, fov=40, name=None):
        b = blds[osm]
        xs = [p[0] for p in b['ring']]
        ys = [p[1] for p in b['ring']]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        diag = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        h = b['top_z'] - b['ground_mean_z']
        tz = b['ground_mean_z'] + h * lift
        d = max(diag, 25) * dist + 10
        a, e = math.radians(az), math.radians(elev)
        px, py = cx + d * math.cos(e) * math.sin(a), cy + d * math.cos(e) * math.cos(a)
        pz = tz + d * math.sin(e)
        return dict(name=name, pos=gl(px, py, pz), target=gl(cx, cy, tz), fov=fov, shadow=max(60, diag))
    V = [
        view('way/39516532', 160, name='eliot_hall'),                    # from the south-south-east
        view('way/39516532', 200, elev=4, dist=0.55, lift=0.5, fov=55, name='eliot_hall_ground'),
        view('way/40877451', 150, name='old_dorm_block'),
        view('way/119843553', 135, name='library'),
        view('way/142983685', 210, name='chemistry'),                    # a science building
        view('way/302430449', 160, elev=24, dist=2.2, name='physics'),
        view('way/785269171', 140, elev=20, name='trillium'),
        view('way/371026021', 200, elev=16, dist=1.6, name='french_house'),
    ]
    # oblique campus overview from the south-east over Eliot Hall toward the library
    e = blds['way/39516532']
    cx = sum(p[0] for p in e['ring']) / len(e['ring'])
    cy = sum(p[1] for p in e['ring']) / len(e['ring'])
    V.append(dict(name='campus_overview', pos=gl(cx + 230, cy - 300, e['ground_mean_z'] + 190),
                  target=gl(cx - 40, cy + 40, e['ground_mean_z']), fov=50, shadow=420))
    return V


def module_views():
    meta = json.load(open(os.path.join(PREV, 'modules.json')))
    V = []
    for m in meta:
        x, y, z = m['center']
        V.append(dict(name='module_' + m['name'], pos=gl(x + 0.0, y - m['dist'], z + m['dist'] * 0.12),
                      target=gl(x, y, z), fov=40, shadow=12))
    return V


def serve():
    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(H, directory=ROOT))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


def main():
    modules = '--modules' in sys.argv
    os.makedirs(PREV, exist_ok=True)
    port = serve()
    base = f'http://127.0.0.1:{port}/'
    with sync_playwright() as p:
        br = p.chromium.launch(args=['--use-gl=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'])
        pg = br.new_page(viewport=dict(width=W, height=H))
        pg.on('console', lambda m: print('  [page]', m.text) if m.type in ('error', 'warning') else None)
        pg.route('**/three.min.js', lambda r: r.fulfill(path=os.path.join(THREE, 'build', 'three.min.js')))
        pg.route('**/GLTFLoader.js', lambda r: r.fulfill(path=os.path.join(THREE, 'examples', 'js', 'loaders', 'GLTFLoader.js')))
        pg.goto(base + 'world/buildings/kit/preview.html')
        if modules:
            n = pg.evaluate('o => setup(o)', dict(buildings=base + 'world/buildings/out/preview/modules.glb',
                                                    terrain=None, w=W, h=H))
            views = module_views()
        else:
            n = pg.evaluate('o => setup(o)', dict(buildings=base + 'world/buildings/out/reed_buildings_detailed.glb',
                                                    terrain=base + 'world/unreal/reed_terrain.glb', w=W, h=H))
            views = building_views()
        print('loaded', n, 'nodes')
        for v in views:
            pg.evaluate('c => shot(c)', v)
            out = os.path.join(PREV, v['name'] + '.png')
            pg.locator('canvas').screenshot(path=out)
            print('wrote', out)
        br.close()


if __name__ == '__main__':
    main()
