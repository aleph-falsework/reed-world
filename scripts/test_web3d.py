#!/usr/bin/env python3
"""Headless check of world/3d/web: loads the page in Chromium (SwiftShader WebGL), serves three.js r128 from a
local copy (npm i three@0.128.0 in /tmp), takes screenshots into world/3d/screens/, and runs two gameplay tests:
walking into buildings never penetrates a footprint, and approaching a sign shows the prompt and E opens it.

Usage: PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers python3 scripts/test_web3d.py [--shots-only|--tests-only]
"""
import functools, http.server, json, os, sys, threading, time
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, 'world', '3d', 'web')
SHOTS = os.path.join(ROOT, 'world', '3d', 'screens')
THREE = os.environ.get('THREE_JS', '/tmp/node_modules/three/build/three.min.js')
CDN = 'https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js'


def serve():
    h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=WEB)
    h.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ''
    os.makedirs(SHOTS, exist_ok=True)
    srv = serve()
    url = f'http://127.0.0.1:{srv.server_address[1]}/index.html?fps'
    results, errors = {}, []
    with sync_playwright() as pw:
        br = pw.chromium.launch(args=['--use-gl=swiftshader', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
                                      '--ignore-gpu-blocklist'])
        pg = br.new_page(viewport={'width': 1280, 'height': 760})
        pg.on('console', lambda m: errors.append(f'{m.type}: {m.text}') if m.type in ('error',) else None)
        pg.on('pageerror', lambda e: errors.append(f'pageerror: {e}'))
        pg.route(CDN, lambda r: r.fulfill(path=THREE, content_type='application/javascript'))
        t0 = time.time()
        pg.goto(url)
        pg.wait_for_function('window.__game && (window.__game.ready || window.__game.errors.length)', timeout=180000)
        results['load_s'] = round(time.time() - t0, 1)
        g_err = pg.evaluate('window.__game.errors')
        if g_err:
            print('PAGE ERRORS', g_err); sys.exit(1)

        def frames(n=2):
            f0 = pg.evaluate('window.__game.frames||0')
            pg.wait_for_function(f'(window.__game.frames||0) >= {f0 + n}', timeout=120000)

        def place(x, y, heading_to=None, yaw=None, pitch=None, dist=None, mapmode=False):
            pg.evaluate("""([x, y, hx, hy, yaw, pitch, dist, mm]) => {
                const G = window.__game, S = G.S;
                S.x = x; S.y = y; S.speed = 0;
                if (hx !== null) { S.heading = Math.atan2(hy - y, hx - x); S.yaw = S.heading - Math.PI/2; }
                if (yaw !== null) S.yaw = yaw;
                if (pitch !== null) S.pitch = pitch;
                if (dist !== null) S.dist = dist;
                if (S.mapMode !== mm) G.toggleMap();
                G.update(0); G.updateHUD(1);
            }""", [x, y, heading_to[0] if heading_to else None, heading_to[1] if heading_to else None,
                   yaw, pitch, dist, mapmode])
            frames(3)

        world = json.load(open(os.path.join(WEB, 'world.json')))
        B = {b.get('n'): b for b in world['buildings'] if b.get('n')}
        cen = lambda b: (sum(b['r'][0::2]) / (len(b['r']) // 2), sum(b['r'][1::2]) / (len(b['r']) // 2))
        sign = {s['n']: s for s in world['signs']}

        if mode != '--tests-only':
            frames(3)
            pg.screenshot(path=os.path.join(SHOTS, '01_start.png'))
            results['render_info_start'] = pg.evaluate('(() => { const i = window.__game.renderer.info; return { calls: i.render.calls, triangles: i.render.triangles, geometries: i.memory.geometries, textures: i.memory.textures }; })()')
            # fps estimate (SwiftShader, CPU rendering: a floor, not a laptop number)
            f0, t1 = pg.evaluate('window.__game.frames'), time.time(); time.sleep(4)
            results['fps_swiftshader'] = round((pg.evaluate('window.__game.frames') - f0) / (time.time() - t1), 2)
            lib = B['Eric V. Hauser Memorial Library']; lx, ly = cen(lib)
            place(lx - 12, ly - 70, heading_to=(lx, ly), pitch=.28, dist=13)
            pg.screenshot(path=os.path.join(SHOTS, '02_library.png'))
            bb = next(b for b in world['bridges'] if b['n'] == 'Blue Bridge')['l']
            k = (len(bb) // 3) // 2 * 3
            place(bb[k], bb[k + 1], heading_to=(820, 505), pitch=.34, dist=26)   # Blue Bridge over Reed Lake
            pg.screenshot(path=os.path.join(SHOTS, '03_canyon_lake.png'))
            bb = next(b for b in world['bridges'] if b['n'] == 'Bouncy Bridge')['l']; k = (len(bb) // 3) // 2 * 3
            place(bb[k], bb[k + 1], heading_to=(300, 560), pitch=.3, dist=14)    # Bouncy Bridge, in the ravine
            pg.screenshot(path=os.path.join(SHOTS, '03b_canyon_ravine.png'))
            place(world['start']['x'], world['start']['y'] + 40, mapmode=True)
            pg.evaluate('window.__game.S.mapHalf = 260'); frames(3)
            pg.screenshot(path=os.path.join(SHOTS, '04_map.png'))
            place(world['start']['x'], world['start']['y'], mapmode=False)

        if mode != '--shots-only':
            # ---- test 1: walk into buildings (fixed-step simulation), never inside a footprint
            def walk_into(name, approach_deg, secs=6.0, keysdown=('KeyW',), extra_yaw=0.0):
                b = B[name]; cx, cy = cen(b)
                ring0 = b['r'][:2]
                return pg.evaluate("""([cx, cy, ang, secs, keys, extra, ring0]) => {
                    const G = window.__game, S = G.S;
                    // start 25 m out along the approach direction, on open ground
                    let sx = cx + Math.cos(ang)*25, sy = cy + Math.sin(ang)*25, k = 0;
                    while ((G.insideBuilding(sx, sy) >= 0 || G.walkBlocked(sx, sy)) && k++ < 60) { sx += Math.cos(ang); sy += Math.sin(ang); }
                    S.x = sx; S.y = sy; S.speed = 0;
                    S.heading = Math.atan2(cy - sy, cx - sx); S.yaw = S.heading - Math.PI/2 + extra;
                    G.keys.clear(); keys.forEach(k => G.keys.add(k)); G.keys.add('ShiftLeft');
                    let inside = 0, minEdge = 1e9, steps = Math.round(secs*60), path = [];
                    for (let i = 0; i < steps; i++) {
                        G.update(1/60);
                        if (G.insideBuilding(S.x, S.y) >= 0) inside++;
                        path.push([S.x, S.y]);
                    }
                    G.keys.clear();
                    const s0 = path[0], s1 = path[path.length-1];
                    const moved = Math.hypot(s1[0]-s0[0], s1[1]-s0[1]);
                    // sliding: after contact, does the player keep moving along the wall?
                    const late = path.slice(-60), slide = Math.hypot(late[59][0]-late[0][0], late[59][1]-late[0][1]);
                    // how close did the player get to this building's walls? (should be ~ the 0.38 m body radius)
                    const B = G.BLD.find(b => Math.abs(b.ring[0][0] - ring0[0]) < .2 && Math.abs(b.ring[0][1] - ring0[1]) < .2);
                    let wall = 1e9;
                    for (const r of [B.ring, ...B.holes]) for (let i = 0; i < r.length; i++) {
                        const a = r[i], c = r[(i+1) % r.length], dx = c[0]-a[0], dy = c[1]-a[1], L2 = dx*dx+dy*dy || 1;
                        const t = Math.max(0, Math.min(1, ((s1[0]-a[0])*dx + (s1[1]-a[1])*dy)/L2));
                        wall = Math.min(wall, Math.hypot(s1[0]-a[0]-dx*t, s1[1]-a[1]-dy*t));
                    }
                    return { start: s0, end: s1, inside, moved: +moved.toFixed(2), last_second_travel: +slide.toFixed(2), end_wall_dist: +wall.toFixed(3) };
                }""", [cx, cy, __import__('math').radians(approach_deg), secs, list(keysdown), extra_yaw, ring0])
            t1 = {}
            for name, ang in [('Eliot Hall', -90), ('Eric V. Hauser Memorial Library', 180), ('Eliot Hall', -60),
                              ('Vollum College Center', -100), ('Trillium', 0)]:
                if name in B:
                    t1[f'{name} from {ang}deg'] = walk_into(name, ang)
            # diagonal approach (W + D) to exercise sliding
            t1['Eliot Hall diagonal W+D'] = walk_into('Eliot Hall', -90, keysdown=('KeyW', 'KeyD'), extra_yaw=0.0)
            results['collision'] = t1
            results['collision_pass'] = all(v['inside'] == 0 and v['end_wall_dist'] >= 0.3 for v in t1.values())
            results['reached_wall'] = {k: v['end_wall_dist'] < 1.0 for k, v in t1.items()}
            # real-time key hold (browser input path), into the Library
            lib = B['Eric V. Hauser Memorial Library']; lx, ly = cen(lib)
            place(lx - 40, ly, heading_to=(lx, ly), pitch=.3, dist=10)
            pg.focus('#stage'); pg.keyboard.down('KeyW')
            ins = 0
            for _ in range(20):
                time.sleep(.25)
                ins += pg.evaluate('(() => { const S = window.__game.S; return window.__game.insideBuilding(S.x, S.y) >= 0 ? 1 : 0; })()')
            pg.keyboard.up('KeyW')
            st = pg.evaluate('window.__game.state')
            results['realtime_walk_library'] = dict(inside_samples=ins, end=[round(st['x'], 1), round(st['y'], 1)],
                                                    start=[round(lx - 40, 1), round(ly, 1)])
            # ---- test 2: approach a sign -> prompt; E -> dialogue
            s = sign['Eliot Hall']
            fx, fy = s['x'] + 9 * __import__('math').cos(__import__('math').radians(s['f'])), \
                     s['y'] + 9 * __import__('math').sin(__import__('math').radians(s['f']))
            place(fx, fy, heading_to=(s['x'], s['y']), pitch=.22, dist=7)
            before = pg.evaluate('window.__game.prompt')
            approach = pg.evaluate("""([tx, ty]) => {
                const G = window.__game, S = G.S; G.keys.clear(); G.keys.add('KeyW');
                let i = 0;
                for (; i < 600; i++) {
                    S.heading = Math.atan2(ty - S.y, tx - S.x); S.yaw = S.heading - Math.PI/2;
                    G.update(1/60); G.updateHUD(1/60);
                    if (G.prompt) break;
                }
                G.keys.clear(); S.speed = 0;
                return { steps: i, prompt: G.prompt, dist: Math.hypot(tx - S.x, ty - S.y) };
            }""", [s['x'], s['y']])
            frames(2)
            prompt_visible = pg.is_visible('#prompt')
            pg.keyboard.press('KeyE'); frames(2)
            dlg = dict(visible=pg.is_visible('#dlg'), title=pg.inner_text('#dlgT'), body=pg.inner_text('#dlgB'),
                       meta=pg.inner_text('#dlgM'))
            results['sign'] = dict(prompt_before=before, approach=approach, prompt_visible=prompt_visible, dialog=dlg)
            results['sign_pass'] = bool(before is None and approach['prompt'] and prompt_visible and dlg['visible']
                                        and dlg['title'] == 'Eliot Hall' and len(dlg['body']) > 40)
            # pose the camera for the dialogue screenshot
            pg.evaluate("""() => { const S = window.__game.S; S.yaw += .35; S.pitch = .2; S.dist = 6.5; }"""); frames(2)
            pg.screenshot(path=os.path.join(SHOTS, '05_sign_dialogue.png'))
            pg.keyboard.press('Escape')
            # an estimated-door sign shows the note
            s2 = next(x for x in world['signs'] if x['est'])
            place(s2['x'] + .5, s2['y'] + .5)
            pg.keyboard.press('KeyE'); frames(1)
            results['estimated_note'] = 'estimated' in pg.inner_text('#dlgM')
            pg.keyboard.press('Escape')
        results['console_errors'] = errors
        results['page_errors'] = pg.evaluate('window.__game.errors')
        br.close()
    srv.shutdown()
    print(json.dumps(results, indent=1))
    ok = (mode == '--shots-only' or (results.get('collision_pass') and results.get('sign_pass'))) and not errors
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
