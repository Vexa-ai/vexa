"""Optional CRM fact receipt pack, enabled through VEXA_FLOWS_DEFS_EXTRA.

Record contents remain in CRM. Downstream routines resolve these references with
an explicitly delegated subject and tenant scope; a fact grants no record access.
"""
from flows import Done, EventType, StepError


def build(reg, db):
    @reg.step
    def record_crm_change(ctx):
        """Record a CRM revision reference in the workflow timeline without copying account fields."""
        required = ('tenant_id', 'record_id', 'revision')
        if any(not ctx.refs.get(key) for key in required):
            raise StepError('CRM event requires tenant_id, record_id and revision')
        return Done({key: ctx.refs[key] for key in required})

    reg.flow(name='crm_change_log', version=1, on=EventType('crm.record.changed'),
             steps=[reg.steps['record_crm_change']])
