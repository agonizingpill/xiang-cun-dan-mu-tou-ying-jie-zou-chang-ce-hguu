from sqlalchemy import inspect, text

from core.db import engine


def upgrade():
    """补交场回单的拍点依据列，并允许待重试记录暂不占分路。幂等。"""
    inspector = inspect(engine)
    if 'cue_routing' not in inspector.get_table_names():
        return
    columns = {x['name'] for x in inspector.get_columns('cue_routing')}
    with engine.begin() as conn:
        if 'basis' not in columns:
            conn.execute(text('ALTER TABLE cue_routing ADD COLUMN basis JSON NULL'))
        if engine.dialect.name == 'mysql':  # 待重试记录 route 留空，不占线
            conn.execute(text('ALTER TABLE cue_routing MODIFY route VARCHAR(160) NULL'))
