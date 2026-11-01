"""Управляемый d20 для настоящего прыжка, только в собственной браузерной тестовой БД."""
import random
import sys
from threading import Lock
from uuid import UUID


def install(app, db, check_database, run_id):
    assert str(UUID(run_id)) == run_id
    assert app.testing and not app.debug
    check_database()
    import app.services.combat as combat
    from flask import request
    mutex = Lock()
    pending,draws = [],[]

    class ControlledRandom:
        def __getattr__(self,name):
            return getattr(random,name)

        def randint(self,low,high):
            caller=sys._getframe(1)
            if caller.f_code.co_name=='<listcomp>':
                caller=caller.f_back
            if (caller.f_globals.get('__name__'),caller.f_code.co_name)==('app.services.combat','_narrative_skill_check') and (low,high)==(1,20):
                check_database()
                with mutex:
                    if pending:
                        roll=pending.pop(0)
                        draws.append(roll)
                        return roll
            return random.randint(low,high)

    combat.random=ControlledRandom()

    @app.route('/__testing_jump_rolls/'+run_id,methods=['GET','POST'])
    def control_rolls():
        check_database()
        with mutex:
            if request.method=='POST':
                values=(request.get_json() or {}).get('rolls')
                if pending or not isinstance(values,list) or not 1<=len(values)<=4 or any(type(v) is not int or not 1<=v<=20 for v in values):
                    return {'error':'Некорректная очередь тестовых d20'},400
                pending.extend(values)
            return {'pending':list(pending),'draws':list(draws),'run_id':run_id}
