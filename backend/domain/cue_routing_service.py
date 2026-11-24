from core.records import get, insert, need, scan
from domain.learning import dtw
from domain.venues_model import venues
from domain.cue_tracks_model import cue_tracks
from domain.cue_clusters_model import cue_clusters
from domain.cue_routing_model import cue_routing

PENDING = '待重试'
SHARED = '已合用'


def _route_label(venue_code, index):
    suffix = venue_code.rsplit('-', 1)[-1]
    return f'{suffix}-{index + 1}'


def _used_routes(conn, venue_code):
    return {x['route'] for x in scan(conn, cue_routing)
            if x['destination'] == venue_code and x['status'] == SHARED}


def _candidate_rows(conn, group):
    """按拍点相似度聚合同簇场地，返回排序后的候选（含跳过原因）。"""
    source_track = get(conn, cue_tracks, group['track'])
    origin_code = source_track['venue']
    member_codes = [c for c in group['members'] if c != source_track['code']]
    members = {x['code']: x for x in scan(conn, cue_tracks) if x['code'] in member_codes}
    scores = {}
    for code in member_codes:
        track = members.get(code)
        if track is None:
            continue
        distance = round(dtw(source_track['timings'], track['timings']), 4)
        slot = scores.setdefault(track['venue'], {'distances': [], 'tracks': []})
        slot['distances'].append(distance)
        slot['tracks'].append(code)
    candidates = []
    for venue_code, slot in scores.items():
        if venue_code == origin_code:
            continue
        candidates.append({
            'venue': venue_code,
            'similarity': round(sum(slot['distances']) / len(slot['distances']), 4),
            'tracks': sorted(slot['tracks']),
            'distances': slot['distances'],
        })
    candidates.sort(key=lambda x: (x['similarity'], x['venue']))
    return origin_code, source_track, candidates


def _preview(conn, group):
    origin_code, source_track, candidates = _candidate_rows(conn, group)
    origin = get(conn, venues, origin_code)
    rows = []
    for item in candidates:
        venue = get(conn, venues, item['venue'])
        reasons = []
        if not venue['ready']:
            reasons.append('场地尚未签验收单')
        if venue['protocol'] != origin['protocol']:
            reasons.append('两场计量协议不同，须登记独立分路提示')
        occupied = _used_routes(conn, venue['code'])
        free = [_route_label(venue['code'], i) for i in range(venue['channels'])
                if _route_label(venue['code'], i) not in occupied]
        if not free:
            reasons.append('目标场地分路已全部占满')
        rows.append({
            'venue': venue['code'],
            'similarity': item['similarity'],
            'evidence_tracks': item['tracks'],
            'distances': item['distances'],
            'ready': venue['ready'],
            'protocol': venue['protocol'],
            'free_routes': free,
            'skipped': reasons,
        })
    return {
        'cluster': group['code'],
        'token': group['token'],
        'origin': origin_code,
        'source_track': source_track['code'],
        'candidates': rows,
    }


def routing_candidates(conn, user, data):
    group = get(conn, cue_clusters, data.cluster)
    return _preview(conn, group)


def apply_routing(conn, user, data):
    group = get(conn, cue_clusters, data.cluster)
    # 已签的同一申请重试：直接回原回单，不重复占线，原拍点依据仍可查
    for row in scan(conn, cue_routing):
        if row['code'] == data.code:
            return {**row, 'replayed': True}
    origin_code, source_track, candidates = _candidate_rows(conn, group)
    # 固定加锁顺序，避免两张申请并发抢同一路时死锁
    locked = {code: get(conn, venues, code, lock=True)
              for code in sorted({origin_code, *(x['venue'] for x in candidates)})}
    origin = locked[origin_code]
    preview = []
    chosen = None
    for item in candidates:
        venue = locked[item['venue']]
        reasons = []
        if not venue['ready']:
            reasons.append('场地尚未签验收单')
        if venue['protocol'] != origin['protocol']:
            reasons.append('两场计量协议不同，须登记独立分路提示')
        occupied = _used_routes(conn, venue['code'])
        free = [_route_label(venue['code'], i) for i in range(venue['channels'])
                if _route_label(venue['code'], i) not in occupied]
        if not free:
            reasons.append('目标场地分路已全部占满')
        preview.append({
            'venue': venue['code'],
            'similarity': item['similarity'],
            'evidence_tracks': item['tracks'],
            'distances': item['distances'],
            'free_routes': free,
            'skipped': reasons,
        })
        # 顺着相似度往下找：首个通过全部校验的场地取最小空闲分路
        if chosen is None and not reasons:
            chosen = {'venue': venue['code'], 'route': free[0],
                      'similarity': item['similarity']}
    basis = {
        'cluster_token': group['token'],
        'source_track': source_track['code'],
        'origin': origin_code,
        'chosen': chosen,
        'candidates': preview,
    }
    if chosen is None:
        # 无路可用：留待重试，不占线
        return insert(conn, cue_routing, code=data.code, cluster=group['code'],
                      destination=origin_code, route=None, status=PENDING, basis=basis)
    return insert(conn, cue_routing, code=data.code, cluster=group['code'],
                  destination=chosen['venue'], route=chosen['route'],
                  status=SHARED, basis=basis)


def routing_evidence(conn, user, data):
    row = get(conn, cue_routing, data.code)
    return {'code': row['code'], 'cluster': row['cluster'], 'destination': row['destination'],
            'route': row['route'], 'status': row['status'], 'basis': row['basis']}


def share_cue(conn, user, data):
    group=get(conn,cue_clusters,data.cluster)
    origin=get(conn,cue_tracks,group['track'])['venue']
    venues_by_key={x:get(conn,venues,x,lock=True) for x in sorted({origin,data.destination})}
    target=venues_by_key[data.destination]
    need(target['ready'],'目标场地尚未签验收单')
    need(target['protocol']==venues_by_key[origin]['protocol'],'两场计量协议不同，须登记独立分路提示')
    need(len(data.route)<=7,'分路编号超界')
    used=[x for x in scan(conn,cue_routing) if x['destination']==target['code'] and x['status']=='已合用']
    need(len(used)<target['channels'] and not any(x['route']==data.route for x in used),'目标分路已被占')
    return insert(conn,cue_routing,**data.model_dump(),status='已合用')


def record_separate(conn, user, data):
    group=get(conn,cue_clusters,data.cluster)
    source=get(conn,venues,get(conn,cue_tracks,group['track'])['venue'])
    target=get(conn,venues,data.destination)
    need(source['protocol']!=target['protocol'],'同协议可直接用合用签回')
    return insert(conn,cue_routing,**data.model_dump(),status='异协议分路提示')
