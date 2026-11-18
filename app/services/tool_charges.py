def remaining_tool_charges(item, default):
    attributes = item.get('attributes') or {}
    remaining = item.get('uses', attributes.get('uses_remaining', item.get('maxUses', default)))
    if type(remaining) not in (int, float) or not 1 <= remaining < float('inf'):
        return 0
    return int(remaining)


def spend_tool_charge(item, remaining):
    item['uses'] = remaining - 1
    item.setdefault('attributes', {})['uses_remaining'] = item['uses']
    return item['uses']
