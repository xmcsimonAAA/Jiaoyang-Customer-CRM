"""Keep current USD funding shared across customer and product participation views."""
from decimal import Decimal


SCHEMA = """CREATE TABLE IF NOT EXISTS customer_funding_state (
 customer_id TEXT PRIMARY KEY REFERENCES customers(id), asset_revision TEXT NOT NULL,
 asset_date TEXT NOT NULL, source_kind TEXT NOT NULL, source_label TEXT NOT NULL,
 base_amount REAL NOT NULL, updated_by TEXT NOT NULL, updated_at TEXT NOT NULL)"""


def _write_amount(conn, customer, amount, user, timestamp, *, write_customer=True):
    value = float(Decimal(str(amount)))
    changed = Decimal(str(customer['funded_amount'] or 0)) != Decimal(str(amount))
    if changed and write_customer:
        conn.execute('UPDATE customers SET funded_amount=?,updated_at=?,version=version+1 WHERE id=?',
                     (value, timestamp, customer['id']))
    conn.execute('''UPDATE batch_participations SET funded_amount=?,updated_by=?,updated_by_name=?,updated_at=?
                    WHERE customer_id=? AND funded_amount<>?''',
                 (value, user['id'], user['name'], timestamp, customer['id'], value))
    return changed


def _save_state(conn, customer_id, revision, source_date, kind, label, base, user, timestamp):
    conn.execute('''INSERT INTO customer_funding_state VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(customer_id) DO UPDATE SET asset_revision=excluded.asset_revision,
        asset_date=excluded.asset_date,source_kind=excluded.source_kind,source_label=excluded.source_label,
        base_amount=excluded.base_amount,updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
        (customer_id, revision, source_date, kind, label, base, user['id'], timestamp))


def manual(conn, customer_id, amount, user, now_iso, audit, label='手工修改', *, write_customer=True):
    customer = conn.execute('SELECT id,funded_amount FROM customers WHERE id=?', (customer_id,)).fetchone()
    state = conn.execute('SELECT * FROM customer_funding_state WHERE customer_id=?', (customer_id,)).fetchone()
    timestamp = now_iso()
    changed = _write_amount(conn, customer, amount, user, timestamp, write_customer=write_customer)
    _save_state(conn, customer_id, state['asset_revision'] if state else '', state['asset_date'] if state else '',
                'manual', label, state['base_amount'] if state else customer['funded_amount'], user, timestamp)
    audit(conn, user, 'customer.funding_updated', 'customer', customer_id,
          dict(before=customer['funded_amount'], amount=str(amount), sourceKind='manual', sourceLabel=label))
    return changed


def sync(conn, datasets, user, now_iso, audit, *, rollback=False):
    # Business date wins over upload order. A repeated identical source revision
    # leaves a later manual correction intact; a revised latest source can replace it.
    latest = {}
    # Use the revision/upload timestamp as a deterministic tie-breaker when
    # two source files carry the same business date.  The last correction for
    # that date then becomes the current source without allowing an older date
    # to overwrite it.
    for dataset in sorted(
        datasets.values(),
        key=lambda d: (d['business_date'], d.get('created_at', ''), d.get('head_id', '')),
    ):
        if dataset['kind'] == 'assets' and dataset.get('head_id'):
            for row in dataset['rows']:
                latest[row['twCode']] = (dataset, row)
    customers = {}
    for customer in conn.execute('''SELECT c.id,c.customer_code,c.funded_amount,i.normalized_value
        FROM customers c LEFT JOIN customer_identifiers i ON i.customer_id=c.id AND i.kind='tw'
        WHERE c.archived_at IS NULL''').fetchall():
        code = customer['normalized_value'] or customer['customer_code']
        customers.setdefault(code, {})[customer['id']] = dict(customer)
    updated = 0
    for code, matches in customers.items():
        if len(matches) != 1:
            continue
        customer = next(iter(matches.values()))
        state = conn.execute('SELECT * FROM customer_funding_state WHERE customer_id=?', (customer['id'],)).fetchone()
        source = latest.get(code)
        if not source:
            if rollback and state and state['source_kind'] == 'asset':
                timestamp = now_iso()
                _write_amount(conn, customer, state['base_amount'], user, timestamp)
                conn.execute('DELETE FROM customer_funding_state WHERE customer_id=?', (customer['id'],))
                audit(conn, user, 'customer.funding_restored', 'customer', customer['id'],
                      dict(before=customer['funded_amount'], amount=state['base_amount']))
            continue
        dataset, row = source
        if state and state['asset_revision'] == dataset['head_id']:
            continue
        if state and dataset['business_date'] < state['asset_date'] and not (rollback and state['source_kind'] == 'asset'):
            continue
        if rollback and state and state['source_kind'] == 'manual':
            continue
        timestamp = now_iso()
        updated += _write_amount(conn, customer, row['assetUsd'], user, timestamp)
        _save_state(conn, customer['id'], dataset['head_id'], dataset['business_date'], 'asset', dataset['filename'],
                    state['base_amount'] if state else customer['funded_amount'], user, timestamp)
        audit(conn, user, 'customer.funding_updated', 'customer', customer['id'],
              dict(before=customer['funded_amount'], amount=row['assetUsd'], sourceKind='asset',
                   sourceDate=dataset['business_date'], sourceLabel=dataset['filename'], revision=dataset['head_id']))
    return updated


def attach(conn, customers):
    if not customers:
        return customers
    ids = {customer['id'] for customer in customers}
    states = {row['customer_id']: dict(row) for row in conn.execute(
        'SELECT * FROM customer_funding_state WHERE customer_id IN (' + ','.join('?' for _ in ids) + ')', tuple(ids)).fetchall()}
    for customer in customers:
        state = states.get(customer['id'], {})
        customer['funding_source_kind'] = state.get('source_kind', '')
        customer['funding_source_date'] = state.get('asset_date', '')
        customer['funding_source_label'] = state.get('source_label', '')
        customer['funding_updated_at'] = state.get('updated_at', '')
    return customers
