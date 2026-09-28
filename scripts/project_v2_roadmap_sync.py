#!/usr/bin/env python3
from __future__ import annotations
import json, os, re, urllib.request, urllib.error
from datetime import date

TOKEN=os.environ["PASI_PROJECTS_TOKEN"].strip()
GRAPHQL="https://api.github.com/graphql"
PROJECT_ID="PVT_kwHOEykLWc4BkVgY"

SCHEDULES={
0:("2026-09-22","2026-10-04"),
1:("2026-10-04","2026-10-12"),
2:("2026-10-13","2026-10-24"),
3:("2026-10-25","2026-11-07"),
4:("2026-11-08","2026-11-21"),
5:("2026-11-22","2026-12-05"),
6:("2026-12-06","2026-12-19"),
7:("2026-12-20","2027-01-09"),
8:("2027-01-10","2027-01-30"),
9:("2027-01-31","2027-02-20"),
10:("2027-02-21","2027-03-20"),
11:("2027-03-21","2027-04-10"),
12:("2027-04-11","2027-05-08"),
13:("2027-05-09","2027-05-29"),
14:("2027-05-30","2027-06-19"),
15:("2027-06-20","2027-07-10"),
16:("2027-07-11","2027-08-07"),
17:("2027-08-08","2027-08-28"),
18:("2027-08-29","2027-09-25"),
19:("2027-09-26","2027-10-16"),
20:("2027-10-17","2027-11-13"),
21:("2027-11-14","2027-12-11"),
22:("2027-12-12","2028-01-15"),
}

QUERY="""
query($id:ID!){
 node(id:$id){
  ... on ProjectV2{
   id
   fields(first:100){
    nodes{
     ... on ProjectV2Field{id name dataType}
     ... on ProjectV2SingleSelectField{id name dataType options{id name}}
     ... on ProjectV2IterationField{
      id name dataType
      configuration{
       iterations{id title startDate}
       completedIterations{id title startDate}
      }
     }
    }
   }
   items(first:100){
    nodes{
     id
     content{
      ... on Issue{id number title state repository{nameWithOwner} body}
     }
    }
   }
  }
 }
}
"""

MUTATION="""
mutation($projectId:ID!,$itemId:ID!,$fieldId:ID!,$value:ProjectV2FieldValue!){
 updateProjectV2ItemFieldValue(
  input:{projectId:$projectId,itemId:$itemId,fieldId:$fieldId,value:$value}
 ){projectV2Item{id}}
}
"""

READBACK="""
query($id:ID!){
 node(id:$id){
  ... on ProjectV2Item{
   startDate: fieldValueByName(name:"Start date"){
    ... on ProjectV2ItemFieldDateValue{date}
   }
   targetDate: fieldValueByName(name:"Target date"){
    ... on ProjectV2ItemFieldDateValue{date}
   }
   iteration: fieldValueByName(name:"Iteration"){
    ... on ProjectV2ItemFieldIterationValue{title}
   }
   quarter: fieldValueByName(name:"Quarter"){
    ... on ProjectV2ItemFieldIterationValue{title}
   }
   team: fieldValueByName(name:"Team"){
    ... on ProjectV2ItemFieldSingleSelectValue{name}
   }
   status: fieldValueByName(name:"Status"){
    ... on ProjectV2ItemFieldSingleSelectValue{name}
   }
  }
 }
}
"""


def gql(query, variables):
    req=urllib.request.Request(
        GRAPHQL,
        data=json.dumps({"query":query,"variables":variables}).encode(),
        headers={
            "Authorization":f"Bearer {TOKEN}",
            "Content-Type":"application/json",
            "Accept":"application/json",
            "User-Agent":"PASI-project-v2-roadmap-sync",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req,timeout=30) as response:
            body=json.load(response)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"GitHub GraphQL HTTP {exc.code}: {exc.read().decode('utf-8','replace')[:1000]}")
    if body.get("errors"):
        raise SystemExit("GraphQL: "+"; ".join(str(e.get("message",e)) for e in body["errors"]))
    return body["data"]

def resolve_single_option(field, wanted):
    for option in field.get("options") or []:
        if option.get("name","").casefold()==wanted.casefold():
            return option["id"]
    raise SystemExit(f"Missing single-select option {wanted!r} for {field['name']}")

def resolve_iteration_by_title(field, wanted):
    cfg=field.get("configuration") or {}
    all_items=list(cfg.get("iterations") or [])+list(cfg.get("completedIterations") or [])
    matches=[x for x in all_items if x.get("title","").casefold()==wanted.casefold()]
    if len(matches)!=1:
        raise SystemExit(f"Expected one iteration {wanted!r}; found {len(matches)}")
    return matches[0]["id"]

def resolve_quarter_by_date(field, start_date):
    cfg=field.get("configuration") or {}
    all_items=[]
    for bucket in ("iterations","completedIterations"):
        all_items += list(cfg.get(bucket) or [])
    usable=[x for x in all_items if x.get("startDate")]
    usable.sort(key=lambda x:x["startDate"])
    candidates=[x for x in usable if x["startDate"]<=start_date]
    if not candidates:
        raise SystemExit(f"No Project Quarter contains start date {start_date}")
    return candidates[-1]["id"], candidates[-1]["title"]

def derived_status(issue_state, body):
    if issue_state=="CLOSED":
        return "Done"
    checks=re.findall(r"(?m)^\s*[-*]\s*\[([ xX])\]", body or "")
    if checks and all(x.strip().lower()=="x" for x in checks):
        return "Done"
    if any(x.strip().lower()=="x" for x in checks):
        return "In progress"
    return "Todo"

def set_field(project_id,item_id,field,value):
    result=gql(MUTATION,{
        "projectId":project_id,
        "itemId":item_id,
        "fieldId":field["id"],
        "value":value,
    })
    returned=((result.get("updateProjectV2ItemFieldValue") or {}).get("projectV2Item") or {}).get("id")
    if returned!=item_id:
        raise SystemExit(f"Mutation returned wrong item for {field['name']}")

data=gql(QUERY,{"id":PROJECT_ID})
project=data["node"]
fields={f["name"]:f for f in project["fields"]["nodes"] if f and f.get("name")}
required=("Status","Team","Iteration","Quarter","Start date","Target date")
for name in required:
    if name not in fields:
        raise SystemExit(f"Project field missing: {name}")

phase_items={}
for item in project["items"]["nodes"]:
    content=item.get("content") or {}
    title=content.get("title","")
    match=re.match(r"^(FE-)?P(\d+)\s+—\s+",title)
    if not match:
        continue
    prefix="FE" if match.group(1) else "P"
    phase=int(match.group(2))
    key=(f"FE-P{phase}" if prefix=="FE" else f"P{phase}")
    if phase not in SCHEDULES:
        continue
    if key in phase_items:
        raise SystemExit(f"Duplicate Project item for {key}")
    phase_items[key]=(item,content)

expected=set([f"P{i}" for i in range(23)]+[f"FE-P{i}" for i in range(23)])
missing=sorted(expected-set(phase_items))
if missing:
    raise SystemExit("Missing Project roadmap items: "+", ".join(missing))

status_ids={x["name"]:x["id"] for x in fields["Status"].get("options") or []}
team_ids={x["name"]:x["id"] for x in fields["Team"].get("options") or []}
for team in ("Backend","Frontend"):
    if team not in team_ids: raise SystemExit(f"Missing Team option {team}")

verified=[]
for key in sorted(expected,key=lambda x:(int(x.split("P")[-1]),0 if x.startswith("P-") else 1)):
    item,content=phase_items[key]
    phase=int(key.split("P")[-1])
    start,target=SCHEDULES[phase]
    iteration=f"Iteration {phase+1}"
    team="Frontend" if key.startswith("FE-") else "Backend"
    status=derived_status(content.get("state"),content.get("body"))
    quarter_id,quarter_title=resolve_quarter_by_date(fields["Quarter"],start)

    set_field(PROJECT_ID,item["id"],fields["Start date"],{"date":start})
    set_field(PROJECT_ID,item["id"],fields["Target date"],{"date":target})
    set_field(PROJECT_ID,item["id"],fields["Iteration"],{"iterationId":resolve_iteration_by_title(fields["Iteration"],iteration)})
    set_field(PROJECT_ID,item["id"],fields["Quarter"],{"iterationId":quarter_id})
    set_field(PROJECT_ID,item["id"],fields["Team"],{"singleSelectOptionId":team_ids[team]})
    set_field(PROJECT_ID,item["id"],fields["Status"],{"singleSelectOptionId":status_ids[status]})

    read=gql(READBACK,{"id":item["id"]})["node"]["fieldValues"]["nodes"]
    observed={}
    for value in read:
        name=(value.get("field") or {}).get("name")
        if name=="Start date": observed["Start date"]=value.get("date")
        elif name=="Target date": observed["Target date"]=value.get("date")
        elif name=="Iteration": observed["Iteration"]=value.get("title")
        elif name=="Quarter": observed["Quarter"]=value.get("title")
        elif name=="Team": observed["Team"]=value.get("name")
        elif name=="Status": observed["Status"]=value.get("name")
    expected_values={
        "Start date":start,
        "Target date":target,
        "Iteration":iteration,
        "Quarter":quarter_title,
        "Team":team,
        "Status":status,
    }
    if observed!=expected_values:
        raise SystemExit(f"{key} readback mismatch: expected={expected_values} observed={observed}")
    verified.append({"phase":key,"issue":content.get("number"),"repo":(content.get("repository") or {}).get("nameWithOwner"),**expected_values})
    print(json.dumps(verified[-1],sort_keys=True))

print(f"ROADMAP PROJECT V2 SYNC COMPLETE: {len(verified)} items verified")
print("No issue state was changed by this sync.")
