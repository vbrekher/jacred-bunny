"""Test-only Hrana subset backed by real SQLite. Never accepts production tokens."""
import sys,json,sqlite3,base64
path,mode=sys.argv[1:]
request=json.load(sys.stdin)
def decode(value):
    kind=value['type']
    return None if kind=='null' else int(value['value']) if kind=='integer' else base64.b64decode(value['base64']) if kind=='blob' else value['value']
def encode(value):
    if value is None:return {'type':'null'}
    if isinstance(value,int):return {'type':'integer','value':str(value)}
    if isinstance(value,bytes):return {'type':'blob','base64':base64.b64encode(value).decode().rstrip('=')}
    return {'type':'text' if isinstance(value,str) else 'float','value':value}
with sqlite3.connect(path,timeout=10,isolation_level=None) as db:
    db.execute('PRAGMA foreign_keys=ON')
    def execute(stmt):
        if mode=='fail_begin' and stmt['sql'].startswith('BEGIN'):raise sqlite3.OperationalError('test-only BEGIN failure')
        cur=db.execute(stmt['sql'],[decode(x) for x in stmt.get('args',[])])
        return {'cols':[{'name':x[0]} for x in cur.description or []],
                'rows':[[encode(x) for x in row] for row in cur.fetchall()],
                'affected_row_count':max(0,cur.rowcount)}
    def condition(cond,results,errors):
        if not cond:return True
        if cond['type']=='ok':return results[cond['step']] is not None
        if cond['type']=='error':return errors[cond['step']] is not None
        if cond['type']=='not':return not condition(cond['cond'],results,errors)
        raise ValueError('unsupported fixture condition')
    output=[]
    for operation in request['requests']:
        try:
            if operation['type']=='close':
                if db.in_transaction:db.rollback()
                response={'type':'close'}
            elif operation['type']=='execute':response={'type':'execute','result':execute(operation['stmt'])}
            elif operation['type']=='batch':
                results=[];errors=[]
                for step in operation['batch']['steps']:
                    if not condition(step.get('condition'),results,errors):results.append(None);errors.append(None);continue
                    try:results.append(execute(step['stmt']));errors.append(None)
                    except sqlite3.Error:results.append(None);errors.append({'message':'private-fixture-value-must-not-leak'})
                response={'type':'batch','result':{'step_results':results,'step_errors':errors}}
            else:raise ValueError('unsupported fixture operation')
            output.append({'type':'ok','response':response})
        except sqlite3.Error:output.append({'type':'error','error':{'message':'private-fixture-value-must-not-leak'}})
    print(json.dumps({'results':output}))
