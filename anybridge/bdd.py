"""Small runtime Gherkin/BDD layer independent of a particular browser provider."""
from __future__ import annotations
import re
from dataclasses import dataclass,field
from pathlib import Path

@dataclass
class Step:
    keyword:str; text:str; table:list[list[str]]=field(default_factory=list); docstring:str|None=None
@dataclass
class Scenario:
    name:str; steps:list[Step]; tags:list[str]=field(default_factory=list)
@dataclass
class Feature:
    name:str; background:list[Step]; scenarios:list[Scenario]; tags:list[str]=field(default_factory=list)

def parse_feature(source:str)->Feature:
    lines=source.splitlines(); feature=None; background=[]; scenarios=[]; current=None; tags=[]
    pending_tags=[]
    i=0
    while i<len(lines):
        raw=lines[i]; line=raw.strip(); i+=1
        if not line or line.startswith("#"):continue
        if line.startswith("@"): pending_tags += line.split(); continue
        if line.lower().startswith("feature:"):
            feature=line.split(":",1)[1].strip(); continue
        if line.lower()=="background:":
            current=background; continue
        m=re.match(r"(scenario outline|scenario):\s*(.+)",line,re.I)
        if m:
            current=[]; scenarios.append(Scenario(m.group(2).strip(),current,tags+pending_tags)); pending_tags=[]
            continue
        m=re.match(r"(given|when|then|and|but|\*):\s*(.*)",line,re.I)
        if m and current is not None:
            current.append(Step(m.group(1).title(),m.group(2).strip()));continue
        if line.lower()=="examples:" and scenarios:
            # Example tables are retained on the scenario for executor expansion.
            scenarios[-1].steps.append(Step("Examples",""))
            while i<len(lines) and lines[i].strip().startswith("|"):
                cells=[c.strip() for c in lines[i].strip().strip("|").split("|")];scenarios[-1].steps[-1].table.append(cells);i+=1
    if not feature:raise ValueError("Feature file is missing 'Feature:'.")
    return Feature(feature,background,scenarios,pending_tags)

def substitute(text,values):return re.sub(r"<([^>]+)>",lambda m:str(values.get(m.group(1),m.group(0))),text)

class BDDRunner:
    def __init__(self,bridge):
        self.bridge=bridge
        self.steps={}
        self._register_defaults()
    def register(self,pattern,handler):self.steps[re.compile(pattern,re.I)]=handler
    def _register_defaults(self):
        self.register(r"open (?:the )?(?:page|url) (.+)", lambda u: self.bridge.navigate(u))
        self.register(r"navigate to (.+)", lambda u: self.bridge.navigate(u))
        self.register(r"I click (?:on )?(?:the )?(.+)", lambda target: self.bridge.click_text(target))
        self.register(r"I fill (?:in )?(?:the )?(.+) with (.+)", lambda label,value: self.bridge.fill_label(label,value))
        self.register(r"I should see (.+)", self._assert_text)
        self.register(r"the url should contain (.+)", self._assert_url)
        self.register(r"I wait for (?:the )?text (.+)", lambda t: self.bridge.wait_for(text=t.strip('"')))

    async def _assert_text(self, text):
        body = await self.bridge.snapshot(interactive_only=False, max_chars=100000)
        expected = text.strip('"')
        if expected not in body:
            raise AssertionError(f"Expected text not found: {expected}")

    async def _assert_url(self, text):
        expected = text.strip('"')
        if expected not in self.bridge.current_url:
            raise AssertionError(f"Expected URL fragment not found: {expected}")

    async def run(self,feature:Feature,variables=None):
        variables=variables or {};results=[]
        for scenario in feature.scenarios:
            example_rows=[{}]
            for st in scenario.steps:
                if st.keyword=="Examples":
                    headers=st.table[0] if st.table else [];example_rows=[dict(zip(headers,row)) for row in st.table[1:]] or [{}]
            for row in example_rows:\n                values={**variables,**row};executed=[];step_evidence=[]\n                for step_index, st in enumerate(feature.background+scenario.steps, start=1):\n                    if st.keyword=="Examples":continue\n                    text=substitute(st.text,values)\n                    matched=next(((p,h) for p,h in self.steps.items() if p.fullmatch(text)),None)\n                    if not matched:\n                        error=f"Undefined BDD step: {st.keyword} {text}"\n                        step_evidence.append(await self._step_evidence(scenario=scenario, step=st, step_index=step_index, text=text, status="failed", error=error))\n                        raise ValueError(error)\n                    try:\n                        result=matched[1](*matched[0].match(text).groups())\n                        if hasattr(result,"__await__"):await result\n                    except Exception as exc:\n                        step_evidence.append(await self._step_evidence(scenario=scenario, step=st, step_index=step_index, text=text, status="failed", error=str(exc)))\n                        raise\n                    executed.append(text)\n                    step_evidence.append(await self._step_evidence(scenario=scenario, step=st, step_index=step_index, text=text, status="passed", observed="completed"))\n                evidence = None\n                collector = getattr(self.bridge, "collect_evidence", None)\n                if collector is not None:\n                    evidence = await collector(\n                        action="bdd_scenario",\n                        expected={"scenario": scenario.name},\n                        assertion="Scenario completed",\n                        status="passed",\n                        confidence=1.0,\n                    )\n                results.append({\n                    "scenario": scenario.name,\n                    "steps": executed,\n                    "status": "passed",\n                    "evidence": evidence,\n                    "step_evidence": [item for item in step_evidence if item is not None],\n                })\n        return {"feature":feature.name,"scenarios":results}
