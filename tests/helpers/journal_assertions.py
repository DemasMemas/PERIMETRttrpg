"""Сравнение игрового состояния с отдельной проверкой новых записей журнала."""
from copy import deepcopy


def assert_same_game_state(actual, expected, *, journal_status='rejected'):
    histories = [{}, {}]

    def clean(value, side, path=()):
        if isinstance(value, dict):
            value = deepcopy(value)
            if 'actionJournal' in value and path and path[-1] == 'combatMeta':
                histories[side][path] = value.pop('actionJournal')
            result = {key: clean(item, side, path + (key,)) for key, item in value.items()}
            if result.get('combatMeta') == {}:
                result.pop('combatMeta')
            return result
        if isinstance(value, (list, tuple)):
            return [clean(item, side, path + (index,)) for index, item in enumerate(value)]
        return value

    def differences(a,b,path=()):
        if isinstance(a,dict) and isinstance(b,dict):
            for key in a.keys() | b.keys():
                if key not in a or key not in b:yield path+(key,)
                else:yield from differences(a[key],b[key],path+(key,))
        elif isinstance(a,list) and isinstance(b,list) and len(a)==len(b):
            for index,(left,right) in enumerate(zip(a,b)):yield from differences(left,right,path+(index,))
        elif a!=b:yield path
    left,right=clean(actual,0),clean(expected,1)
    def allow_journal_revision(a,b):
        if isinstance(a,dict) and isinstance(b,dict):
            if {'data','revision','updated_at'}<=a.keys() and a.get('data')==b.get('data') and a['revision']!=b['revision']:
                assert a['revision']==b['revision']+1
                assert a['updated_at']>=b['updated_at']
                a['revision'],a['updated_at']=b['revision'],b['updated_at']
            for key in a.keys() & b.keys():allow_journal_revision(a[key],b[key])
        elif isinstance(a,list) and isinstance(b,list):
            for one,two in zip(a,b):allow_journal_revision(one,two)
    allow_journal_revision(left,right)
    assert left==right, list(differences(left,right))
    for path, previous in histories[1].items():
        current = histories[0].get(path, [])
        assert {entry['id'] for entry in previous} <= {entry['id'] for entry in current}
    for path, current in histories[0].items():
        previous = {entry['id']: entry for entry in histories[1].get(path, [])}
        for entry in current:
            if entry == previous.get(entry['id']):
                continue
            assert entry['status'] == journal_status
            assert entry['label'] and entry['action']
            if journal_status == 'rejected':
                assert entry['reason']
                assert entry['action_points'] == entry['movement_points'] == entry['free_actions'] == 0
