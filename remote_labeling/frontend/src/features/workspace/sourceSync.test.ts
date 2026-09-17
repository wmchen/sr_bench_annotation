import { describe, expect, it } from "vitest";
import type { Group, Sample } from "../../api/client";
import { needsWriteback, sameContent } from "./sourceSync";

const group=():Group=>({HR:[{region_id:"one",label:"text",description:"hello",shape_type:"rectangle",points:[[1,2],[4,6]],recoverable:0}],LR2:[],LR3:[],LR4:[]});
const sample=(source=group()):Sample=>({source_group:source,source_ready:true,draft:source} as Sample);

describe("source synchronization",()=>{
  it("ignores object key ordering but preserves region and vertex ordering",()=>{
    expect(sameContent({a:1,b:{x:2,y:3}},{b:{y:3,x:2},a:1})).toBe(true);
    expect(sameContent([[1,2],[3,4]],[[3,4],[1,2]])).toBe(false);
    expect(sameContent({x:null},{})).toBe(false);
  });
  it("reflects unsent edits immediately and clears after undo to the disk version",()=>{
    const current=sample(), edited=structuredClone(current.draft);
    expect(needsWriteback(current,edited)).toBe(false);
    edited.HR[0].description="new";
    expect(needsWriteback(current,edited)).toBe(true);
    edited.HR[0].description="hello";
    expect(needsWriteback(current,edited)).toBe(false);
  });
  it("requires publication for missing files, metadata repairs and stale desktop drafts",()=>{
    const current={...sample(),source_ready:false};
    expect(needsWriteback(current,current.draft)).toBe(true);
    expect(needsWriteback({...current,source_group:null},current.draft)).toBe(true);
  });
  it("detects evidence and extension field changes, including unfinished values",()=>{
    const current=sample(), edited=structuredClone(current.draft);
    edited.HR[0].recoverable=null;
    expect(needsWriteback(current,edited)).toBe(true);
    edited.HR[0].recoverable=0;
    edited.HR[0].custom={review:"changed"};
    expect(needsWriteback(current,edited)).toBe(true);
  });
});
