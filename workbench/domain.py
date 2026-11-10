from decimal import Decimal
def dry(value,moisture):
    m=Decimal(str(moisture))
    if not 0<=m<1:raise ValueError('含水率超出范围')
    return str(Decimal(str(value))/(1-m))
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
TOOLS={'dry':dry,'censored':censored,'custody':custody}
