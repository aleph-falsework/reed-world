"""Dry-run import_reed.py twice against the stub `unreal` module (python3 world/unreal/dev/run_stub.py)."""
import collections
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import unreal_stub  # noqa: E402
sys.modules['unreal'] = unreal_stub

for run in (1, 2):
    print('================ run', run)
    runpy.run_path(os.path.join(KIT, 'import_reed.py'), run_name='__main__')
    counts = collections.Counter(type(a).__name__ for a in unreal_stub.LEVEL)
    print('level actors after run %d:' % run, dict(counts))
    tagged = [a for a in unreal_stub.LEVEL if 'ReedWorld' in a._p['tags']]
    assert len(tagged) == len(unreal_stub.LEVEL), 'untagged actor spawned'
    if run == 1:
        first = dict(counts)
    else:
        assert dict(counts) == first, 're-run did not replace the previous actors'

errors = [m for k, m in unreal_stub.LOG if k == 'E']
warns = [m for k, m in unreal_stub.LOG if k == 'W']
print('stats', unreal_stub.STATS)
print('imports', unreal_stub.IMPORTS[:7])
print('errors', len(errors), errors[:5])
print('warnings', len(warns), warns[:10])
signs = [a for a in unreal_stub.LEVEL if type(a).__name__ == 'TextRenderActor']
print('sample sign tags', [str(t)[:80] for t in signs[0]._p['tags']])
print('sample sign text', repr(signs[0]._components[0]._p.get('text')))
assert not errors, errors
print('OK')
