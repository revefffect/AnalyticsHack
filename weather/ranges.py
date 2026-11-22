"""Calendar aggregates for a selected interval, with explicit partial-period coverage."""
import calendar
from datetime import date
from collections import Counter
from . import reports, store


def view(con,actor,ds,station,parameter,start,end,step,version=None,revision=None):
    lo,hi=date.fromisoformat(start),date.fromisoformat(end)
    if lo>hi or (hi-lo).days>366*40 or step not in {"day","decade","month"}:
        raise ValueError("Выберите упорядоченный интервал до 40 лет и допустимый шаг.")
    d,v,_,_=reports.visible(con,actor,ds,version)
    rev=d["revision"] if revision is None else revision
    year,month=lo.year,lo.month
    result=[]
    full_count=0
    while (year,month)<=(hi.year,hi.month):
        key=f"{year:04}-{month:02}"
        daily=reports.report(con,actor,ds,station,parameter,key,"day",v,rev)
        chosen=[r for r in daily["rows"] if start<=r["period"]<=end]
        full_count+=sum(r["full"] for r in chosen)
        if step=="day":
            result.extend(chosen)
        else:
            ranges=[(1,len(daily["rows"]))] if step=="month" else [(1,10),(11,20),(21,len(daily["rows"]))]
            for a,b in ranges:
                rows=[r for r in chosen if a<=int(r["period"][-2:])<=b]
                if not rows:
                    continue
                complete=len(rows)==b-a+1
                flags=Counter()
                for row in rows:
                    flags.update(row["flags"])
                vals=[r["value"] for r in rows if r["value"] is not None]
                value=(daily["summary"]["value"] if step=="month" else reports.average(vals,parameter)) if complete else None
                result.append({"period":f"{rows[0]['period']}…{rows[-1]['period']}","value":value,"valid":sum(r["full"] for r in rows),
                               "expected":b-a+1,"flags":dict(flags),"partial":not complete})
        year,month=(year+1,1) if month==12 else (year,month+1)
    return {"id":ds,"version":v,"revision":rev,"title":daily["title"],"station":station,"parameter":parameter,"start":start,"end":end,
            "step":step,"rows":result,"unit":daily["unit"],"method":daily["method"],"source":daily["source"],
            "dashboard_url":reports.graph_url(actor,ds,v,station,parameter,start[:7],step,rev)+f"&var-start={start}&var-end={end}",
            "summary":{"valid_days":full_count,"calendar_days":(hi-lo).days+1,"periods":len(result)}}
