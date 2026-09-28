#!/usr/bin/env python3
from __future__ import annotations
import json, os, urllib.error, urllib.request

TOKEN = os.environ.get("PASI_PROJECTS_TOKEN", "").strip()
if not TOKEN:
    raise SystemExit("PASI_PROJECTS_TOKEN missing")

GRAPHQL = "https://api.github.com/graphql"
PROJECT_ID = "PVT_kwHOEykLWc4BkVgY"
REPO = "th3-st0v3/PASI-Engineering-Workspace"

# phase, issue, start, target, iteration, quarter, team
ITEMS = [
("FE-P0",30,"2026-09-22","2026-10-04","Iteration 1","Quarter 1","Frontend"),
("FE-P1",29,"2026-10-04","2026-10-12","Iteration 2","Quarter 1","Frontend"),
("FE-P2",28,"2026-10-13","2026-10-24","Iteration 3","Quarter 1","Frontend"),
("FE-P3",27,"2026-10-25","2026-11-07","Iteration 4","Quarter 1","Frontend"),
("FE-P4",26,"2026-11-08","2026-11-21","Iteration 5","Quarter 1","Frontend"),
("FE-P5",24,"2026-11-22","2026-12-05","Iteration 6","Quarter 1","Frontend"),
("FE-P6",23,"2026-12-06","2026-12-19","Iteration 7","Quarter 1","Frontend"),
("FE-P7",22,"2026-12-20","2027-01-09","Iteration 8","Quarter 1","Frontend"),
("FE-P8",21,"2027-01-10","2027-01-30","Iteration 9","Quarter 2","Frontend"),
("FE-P9",20,"2027-01-31","2027-02-20","Iteration 10","Quarter 2","Frontend"),
("FE-P10",19,"2027-02-21","2027-03-20","Iteration 11","Quarter 2","Frontend"),
("FE-P11",18,"2027-03-21","2027-04-10","Iteration 12","Quarter 2","Frontend"),
("FE-P12",17,"2027-04-11","2027-05-08","Iteration 13","Quarter 3","Frontend"),
("FE-P13",16,"2027-05-09","2027-05-29","Iteration 14","Quarter 3","Frontend"),
("FE-P14",15,"2027-05-30","2027-06-19","Iteration 15","Quarter 3","Frontend"),
("FE-P15",14,"2027-06-20","2027-07-10","Iteration 16","Quarter 3","Frontend"),
("FE-P16",13,"2027-07-11","2027-08-07","Iteration 17","Quarter 4","Frontend"),
("FE-P17",12,"2027-08-08","2027-08-28","Iteration 18","Quarter 4","Frontend"),
("FE-P18",11,"2027-08-29","2027-09-25","Iteration 19","Quarter 4","Frontend"),
("FE-P19",10,"2027-09-26","2027-10-16","Iteration 20","future Quarter 1","Frontend"),
("FE-P20",9,"2027-10-17","2027-11-13","Iteration 21","future Quarter 1","Frontend"),
("FE-P21",8,"2027-11-14","2027-12-11","Iteration 22","future Quarter 1","Frontend"),
("FE-P22",7,"2027-12-12","2028-01-15","Iteration 23","future Quarter 1","Frontend"),
("P14",42,"2027-05-30","2027-06-19","Iteration 15","Quarter 3","Backend"),
("P15",41,"2027-06-20","2027-07-10","Iteration 16","Quarter 3","Backend"),
("P16",40,"2027-07-11","2027-08-07","Iteration 17","Quarter 4","Backend"),
("P17",39,"2027-08-08","2027-08-28","Iteration 18","Quarter 4","Backend"),
("P18",38,"2027-08-29","2027-09-25","Iteration 19","Quarter 4","Backend"),
("P19",37,"2027-09-26","2027-10-16","Iteration 20","future Quarter 1","Backend"),
("P20",36,"2027-10-17","2027-11-13","Iteration 21","future Quarter 1","Backend"),
("P21",33,"2027-11-14","2027-12-11","Iteration 22","future Quarter 1","Backend"),
("P22",32,"2027-12-12","2028-01-15","Iteration 23","future Quarter 1","Backend"),
]

Q = """query($id:ID!){
  node(id:$id){
    ... on ProjectV2{
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
            ... on Issue{id number repository{nameWithOwner}}
          }
        }
      }
    }
  }
}"""

M = """mutation($projectId:ID!,$itemId:ID!,$fieldId:ID!,$value:ProjectV2FieldValue!){
 updateProjectV2ItemFieldValue(input:{projectId:$projectId,itemId:$itemId,fieldId:$fieldId,value:$value}){
   projectV2Item{id}
 }
}"""

R = """query($id:ID!,$names:[String!]!){
 node(id:$id){
  ... on ProjectV2Item{
   fieldValues(first:100){
    nodes{
     ... on ProjectV2ItemFieldDateValue{field{name} date}
     ... on ProjectV2ItemFieldIterationValue{field{name} title}
     ... on ProjectV2ItemFieldSingleSelectValue{field{name} name}
    }
   }
  }
 }
}"""

def gql(q, vars):
    req=urllib.request.Request(
        GRAPHQL,
        data=json.dumps({"query":q,"variables":vars}).encode(),
        headers={"Accept":"application/json","Content-Type":"application/json",
                 "Authorization":f"Bearer {TOKEN}","User-Agent":"PASI-project-sync"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            out=json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"GitHub GraphQL HTTP {e.code}: {e.read().decode('utf-8','replace')[:500]}")
    if out.get("errors"):
        raise SystemExit("GraphQL: "+"; ".join(str(e.get("message")) for e in out["errors"]))
    return out["data"]

d=gql(Q,{"id":PROJECT_ID})
node=d["node"]
fields={}
for f in node["fields"]["nodes"]:
    if f and f.get("name"):
        fields[f["name"]]=f

def opt_id(name, value):
    f=fields[name]
    if f["__typename"]=="ProjectV2SingleSelectField":
        for o in f.get("options",[]):
            if o["name"].casefold()==value.casefold(): return o["id"]
    else:
        cfg=f.get("configuration") or {}
        for o in list(cfg.get("iterations") or [])+list(cfg.get("completedIterations") or []):
            if o["title"].casefold()==value.casefold(): return o["id"]
    raise SystemExit(f"{name} option not found: {value}")

items={}
for item in node["items"]["nodes"]:
    c=item.get("content") or {}
    repo=(c.get("repository") or {}).get("nameWithOwner")
    if repo==REPO:
        items[(repo,c.get("number"))]=item["id"]

for phase,number,start,target,iteration,quarter,team in ITEMS:
    iid=items.get((REPO,number))
    if not iid: raise SystemExit(f"{phase} issue #{number} is not in the project")
    values={
      "Start date":{"date":start},
      "Target date":{"date":target},
      "Iteration":{"iterationId":opt_id("Iteration",iteration)},
      "Quarter":{"iterationId":opt_id("Quarter",quarter)},
      "Team":{"singleSelectOptionId":opt_id("Team",team)},
    }
    for fname,val in values.items():
        fid=fields[fname]["id"]
        result=gql(M,{"projectId":PROJECT_ID,"itemId":iid,"fieldId":fid,"value":val})
        returned=((result.get("updateProjectV2ItemFieldValue") or {}).get("projectV2Item") or {}).get("id")
        if returned != iid: raise SystemExit(f"{phase}: mutation returned wrong item for {fname}")
    read=gql(R,{"id":iid,"names":[]})["node"]["fieldValues"]["nodes"]
    observed={}
    for v in read:
        field=(v.get("field") or {}).get("name")
        if field=="Start date": observed["Start date"]=v.get("date")
        elif field=="Target date": observed["Target date"]=v.get("date")
        elif field=="Iteration": observed["Iteration"]=v.get("title")
        elif field=="Quarter": observed["Quarter"]=v.get("title")
        elif field=="Team": observed["Team"]=v.get("name")
    expected={"Start date":start,"Target date":target,"Iteration":iteration,"Quarter":quarter,"Team":team}
    if observed != expected:
        raise SystemExit(f"{phase}: readback mismatch expected={expected} observed={observed}")
    print(json.dumps({"phase":phase,"issue":number,"verified":True,**observed},sort_keys=True))

print("ROADMAP PROJECT V2 SYNC COMPLETE")
