#!/usr/bin/env python3
from __future__ import annotations
import json, os, urllib.request
TOKEN=os.environ["PASI_PROJECTS_TOKEN"].strip()
Q="""query($id:ID!){node(id:$id){... on ProjectV2{id number title fields(first:100){nodes{... on ProjectV2Field{id name dataType} ... on ProjectV2SingleSelectField{id name dataType options{id name}} ... on ProjectV2IterationField{id name dataType configuration{iterations{id title startDate} completedIterations{id title startDate}}}}}items(first:100){nodes{id content{... on Issue{id number title state repository{nameWithOwner} body}}}}}}}}"""
req=urllib.request.Request("https://api.github.com/graphql",data=json.dumps({"query":Q,"variables":{"id":"PVT_kwHOEykLWc4BkVgY"}}).encode(),headers={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json","User-Agent":"PASI-project-discovery"})
with urllib.request.urlopen(req,timeout=30) as r: data=json.load(r)
if data.get("errors"): raise SystemExit(json.dumps(data["errors"]))
p=data["data"]["node"]
print("FIELDS")
for f in p["fields"]["nodes"]:
    if f and f.get("name"):
        print(json.dumps({"name":f["name"],"type":f.get("dataType"),"options":f.get("options"),"iterations":(f.get("configuration") or {}).get("iterations")},sort_keys=True))
print("PHASE ITEMS")
for i in p["items"]["nodes"]:
    c=i.get("content") or {}
    t=c.get("title","")
    if t.startswith("P") or t.startswith("FE-P"):
        print(json.dumps({"item_id":i["id"],"number":c.get("number"),"title":t,"repo":(c.get("repository") or {}).get("nameWithOwner"),"state":c.get("state")},sort_keys=True))
