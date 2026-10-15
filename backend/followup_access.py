"""Followup writes remain tied to ownership even when batch lists are shared."""


def can_followup(user, owner_id, in_scope=True):
    if not in_scope:
        return False
    if user.get('rolePermission') == 'manager':
        return bool(owner_id) and owner_id == user.get('id')
    return True


def disabled_reason(user, owner_id, in_scope=True):
    if can_followup(user, owner_id, in_scope):
        return ''
    if not owner_id or owner_id == 'unassigned':
        return '客户尚未分配负责人，请联系主管分配。'
    return '仅客户当前负责人可写跟进。'


def batch_read_user(user):
    # Full batch lists are a narrow read exception, not a customer scope change.
    return dict(user, customerScope='all') if user.get('rolePermission') == 'manager' else user
