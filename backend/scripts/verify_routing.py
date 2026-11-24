"""交场试投分配的离库验证脚本，使用 SQLite，不依赖 Redis 连接。

运行：python3 backend/scripts/verify_routing.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for extra in os.environ.get('PYDEPS', '').split(':'):
    if extra:
        sys.path.insert(0, extra)

os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/routing_verify.db')
os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/0')
os.environ.setdefault('JWT_SECRET', 'verify-secret')

from sqlalchemy import create_engine, text

import core.db
from core import db as db_module
from core.db import metadata
from domain.demo import FIXTURES
from domain import cue_routing_service as service
from domain.cue_routing_model import cue_routing
from domain.venues_model import venues
from domain.cue_tracks_model import cue_tracks
from domain.cue_clusters_model import cue_clusters

import core.db
core.db.engine = create_engine(os.environ['DATABASE_URL'])
metadata.bind = core.db.engine

USER = {'sub': 'operator@demo.test', 'role': 'operator'}


def setup():
    if os.path.exists('/tmp/routing_verify.db'):
        os.remove('/tmp/routing_verify.db')
    metadata.create_all(core.db.engine)
    with core.db.transaction() as conn:
        for table, rows in FIXTURES:
            for row in rows:
                conn.execute(table.insert().values(**row))


class ClusterIn:
    def __init__(self, cluster, code=None):
        self.cluster = cluster
        self.code = code or 'AUTO-' + cluster


def show(title, payload):
    print('\n==', title)
    for c in payload['candidates']:
        print(' ', c['venue'], 'sim=', c['similarity'], 'free=', c['free_routes'],
              'skip=', c['skipped'] or '-', 'tracks=', c['evidence_tracks'])


def main():
    setup()
    with core.db.transaction() as conn:
        preview = service.routing_candidates(conn, USER, ClusterIn('BEAT-24'))
        show('候选预览 BEAT-24（预期 D 之外按相似度排序，B/C/E 各带跳过原因）', preview)
        order = [c['venue'] for c in preview['candidates']]
        assert order == sorted(order, key=lambda v: next(c['similarity'] for c in preview['candidates'] if c['venue'] == v)), '未按相似度排序'
        reasons = {c['venue']: c['skipped'] for c in preview['candidates']}
        assert any('场地尚未签验收单' in r for r in reasons['HALL-C']), '未验收场地未跳过'
        assert any('计量协议不同' in r for r in reasons['HALL-B']), '异协议场地未跳过'
        assert any('占满' in r for r in reasons['HALL-E']), '占满分路未跳过'

        preview = service.routing_candidates(conn, USER, ClusterIn('BEAT-24'))
        show('完整候选（HALL-F 为最近同协议已签场地）', preview)
        first_venue = preview['candidates'][0]
        assert first_venue['venue'] == 'HALL-F' and first_venue['skipped'] == [], '最近同协议场地应首选'

        first = service.apply_routing(conn, USER, ClusterIn('BEAT-24', 'AUTO-1'))
        print('\n首次申请 ->', first['destination'], first['route'], first['status'])
        assert first['status'] == '已合用' and first['destination'] == 'HALL-F' and first['route'] == 'F-1'
        assert first['basis']['chosen'] == {'venue': 'HALL-F', 'route': 'F-1',
                                            'similarity': first['basis']['chosen']['similarity']}

        replay = service.apply_routing(conn, USER, ClusterIn('BEAT-24', 'AUTO-1'))
        print('同号重试 ->', replay['destination'], replay['route'], 'replayed=', replay.get('replayed'))
        assert replay.get('replayed') is True and replay['route'] == 'F-1'
        count = conn.execute(text("select count(*) from cue_routing where code='AUTO-1'")).scalar()
        assert count == 1, '重复重试产生了新占线'

        second = service.apply_routing(conn, USER, ClusterIn('BEAT-24', 'AUTO-2'))
        print('第二张申请 ->', second['destination'], second['route'], second['status'])
        assert second['destination'] == 'HALL-F' and second['route'] == 'F-2', '应顺延到同场地下一空闲分路'

        third = service.apply_routing(conn, USER, ClusterIn('BEAT-24', 'AUTO-3'))
        print('第三张申请 ->', third['destination'], third['route'], third['status'])
        assert third['status'] == '待重试' and third['route'] is None, '无路可用时应待重试且不占线'
        pending_occupies = conn.execute(
            text("select count(*) from cue_routing where destination='HALL-F' and status='已合用'")).scalar()
        assert pending_occupies == 2, '待重试记录不应占线'
        assert third['basis']['chosen'] is None and third['basis']['candidates']

        evidence = service.routing_evidence(conn, USER, type('E', (), {'code': 'AUTO-3'})())
        print('拍点依据 -> token=', evidence['basis']['cluster_token'],
              '候选数=', len(evidence['basis']['candidates']))
        assert evidence['basis']['cluster_token'] == 'beat-24-token'

        rows = [dict(r) for r in conn.execute(cue_routing.select()).mappings()]
        print('\n台账行数:', len(rows))

    # 模拟两张申请争同一路：MySQL/InnoDB 上按场地码 FOR UPDATE 加行锁，
    # 先到者提交释放锁后，后到者重新读到已占分路并顺延。SQLite 无行锁，
    # 这里用两个独立事务串行复现锁等待后的可见状态。
    if os.path.exists('/tmp/routing_concurrent.db'):
        os.remove('/tmp/routing_concurrent.db')
    eng2 = create_engine('sqlite:////tmp/routing_concurrent.db',
                         connect_args={'timeout': 30, 'check_same_thread': False})
    metadata.bind = eng2
    with eng2.begin() as c:
        c.execute(text('PRAGMA journal_mode=WAL'))
    metadata.create_all(eng2)
    core.db.engine = eng2
    with eng2.begin() as conn:
        for table, rows2 in FIXTURES:
            for row in rows2:
                conn.execute(table.insert().values(**row))

    # 模拟两张申请争同一路：MySQL/InnoDB 上按场地码 FOR UPDATE 加行锁，
    # 先到者提交释放锁后，后到者重新读到已占分路并顺延。SQLite 无行锁，
    # 这里用两个独立事务串行复现锁等待后的可见状态。
    outcomes = []
    with core.db.transaction() as conn:
        outcomes.append(service.apply_routing(conn, USER, ClusterIn('BEAT-24', 'RACE-1')))
    with core.db.transaction() as conn:
        outcomes.append(service.apply_routing(conn, USER, ClusterIn('BEAT-24', 'RACE-2')))
    routes = sorted((o['destination'], o['route'], o['status']) for o in outcomes)
    print('\n争路结果（后到者应顺延）:', routes)
    assert len(outcomes) == 2
    taken = [(o['destination'], o['route']) for o in outcomes if o['status'] == '已合用']
    assert len(set(taken)) == len(taken), '两张申请抢到了同一条分路'
    assert routes[0] == ('HALL-F', 'F-1', '已合用') and routes[1] == ('HALL-F', 'F-2', '已合用')
    with eng2.begin() as conn:
        dup = conn.execute(text(
            "SELECT route, COUNT(*) FROM cue_routing WHERE status='已合用' "
            "AND code IN ('RACE-1','RACE-2') GROUP BY route HAVING COUNT(*) > 1")).fetchall()
        assert not dup, '数据库层面出现重复占线'
    print('争路通过：先到 F-1，后到顺延 F-2，无重复占线')

    # 边界：簇内只有源场地自身的轨迹，没有任何外部候选
    with eng2.begin() as conn:
        conn.execute(cue_clusters.insert().values(
            code='BEAT-SOLO', track='CUE-20', representative='CUE-21', distance=0.01,
            members=['CUE-20', 'CUE-21'], token='solo-token'))
    with core.db.transaction() as conn:
        solo = service.apply_routing(conn, USER, ClusterIn('BEAT-SOLO', 'SOLO-1'))
        print('无外部候选 ->', solo['destination'], solo['route'], solo['status'])
        assert solo['status'] == '待重试' and solo['route'] is None
        assert solo['basis']['candidates'] == [] and solo['basis']['chosen'] is None
    print('边界通过：无候选时待重试不报错、不占线')
    print('\n全部断言通过')


if __name__ == '__main__':
    main()
