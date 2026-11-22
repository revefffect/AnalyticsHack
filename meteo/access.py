"""Dataset permissions for authenticated workspace accounts."""
from fastapi import HTTPException


# Snapshots inherit current read access from their live feed. Only the source is editable.
READABLE_DATASETS = """
    SELECT d.*,
           CASE WHEN d.source!='stream_snapshot' AND (base.owner=:username OR m.role='editor')
                THEN 1 ELSE 0 END AS can_edit,
           CASE WHEN d.source!='stream_snapshot' AND base.owner=:username
                THEN 1 ELSE 0 END AS can_manage_access
    FROM datasets d
    JOIN datasets base ON base.id=CASE WHEN d.source='stream_snapshot'
                                      THEN json_extract(d.metadata,'$.source_dataset') ELSE d.id END
    LEFT JOIN dataset_members m ON m.dataset=base.id AND m.username=:username
    WHERE base.source!='stream_snapshot'
      AND (base.owner=:username OR base.visibility='shared' OR m.role IN ('reader','editor'))
"""


def pick_dataset(db, version, user, *, permission='read'):
    dataset = db.execute(f'SELECT * FROM ({READABLE_DATASETS}) WHERE id=:id',
                         {'username': user['name'], 'id': version}).fetchone()
    if dataset is None:
        raise HTTPException(404, 'Набор недоступен')
    if permission != 'read':
        field, detail = {'edit': ('can_edit', 'Нужны права редактора набора'),
                         'manage': ('can_manage_access', 'Доступом управляет владелец набора')}[permission]
        if not dataset[field]:
            raise HTTPException(403, detail)
    return dataset
