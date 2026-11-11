from decimal import Decimal
def dry(value,moisture):
    m=Decimal(str(moisture))
    if not 0<=m<1:raise ValueError('含水率超出范围')
    return str(Decimal(str(value))/(1-m))
def convert_unit(value,unit,target_unit):
    factors={('g/kg','mg/kg'):Decimal('1000'),('mg/kg','g/kg'):Decimal('0.001'),
             ('mg/kg','mg/kg'):Decimal('1'),('g/kg','g/kg'):Decimal('1')}
    pair=(unit,target_unit)
    if pair not in factors:raise ValueError('不支持的单位换算')
    result=Decimal(str(value))*factors[pair]
    # 固定小数形式（如 40 而非 4E+1），保留有效精度且去掉多余尾零
    return format(result,'f').rstrip('0').rstrip('.') if '.' in format(result,'f') else format(result,'f')
def censored(value,lod=None):
    if value=='ND':
        if lod is None or float(lod)<=0:raise ValueError('未检出必须保留正检出限')
        return {'low':0,'high':float(lod),'high_inclusive':False}
    return {'low':float(value),'high':float(value),'high_inclusive':True}
def custody(events,initial):
    holder=initial;issues=[]
    for event in sorted(events,key=lambda x:x['order']):
        if event['sender']!=holder or not event['signed']:issues.append(event['order'])
        holder=event['receiver']
    return issues
TOOLS={'dry':dry,'convert_unit':convert_unit,'censored':censored,'custody':custody}
