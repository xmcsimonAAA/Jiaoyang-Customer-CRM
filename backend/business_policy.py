"""Historical placement records stay readable; shared customer data stays live."""
from fastapi import HTTPException

PLACEMENT_ARCHIVED = True


def require_active_placement():
    if PLACEMENT_ARCHIVED:
        raise HTTPException(409, '定增业务已归档，仅可查阅历史记录。请在大宗交易或购买计划中办理新业务。')


def guard_placement_changes(values, current=None):
    if not PLACEMENT_ARCHIVED:
        return
    defaults = dict(placement_status='未进入', target_batch_id=None, intent_amount=0,
                    actual_amount=0, intent_status='未确认', capital_destination='', lost_reason='', closed_at=None)
    current = dict(current) if current is not None else defaults
    for key in defaults:
        if key in values and (values[key] or defaults[key]) != (current.get(key) or defaults[key]):
            require_active_placement()
