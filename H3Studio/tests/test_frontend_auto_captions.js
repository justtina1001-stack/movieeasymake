"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const auto = require("../static/editor_auto_captions.js"), editor = require("../static/editor.js");
const project = () => ({id:"a".repeat(32),name:"字幕",fps:24,width:1280,height:720,
  clips:[{id:"v",media_id:"media",in:0,out:4,volume:1,speed:1}],audio_clips:[],overlays:[]});
const complete = p => ({id:"b".repeat(32),project_id:p.id,status:"completed",source_audio_snapshot:structuredClone(p),cues:[{start:1,end:2,text:"測試字幕"}]});
test("caption matching ignores title, visual layers and video transforms, but protects the audible timeline",()=>{
  const p=project(), job=complete(p); p.name="新的名字"; p.width=1920;
  p.overlays.push({kind:"text",text:"標題",start:0,end:1}); assert.equal(auto.resultMatches(job,p),true);
  for(const mutate of [p=>p.fps=30,p=>p.clips[0].in=.1,p=>p.clips[0].volume=0,p=>p.clips[0].fade_in=.2,p=>p.clips[0].speed=2,p=>p.audio_clips.push({media_id:"music",in:0,out:2,volume:1,start:1,track:0}),p=>p.overlays.push({kind:"video",media_id:"speech",start:2,end:4,in:0,out:2,volume:1})]){
    const changed=project();mutate(changed);assert.equal(auto.resultMatches(job,changed),false);
  }
  assert.equal(auto.resultMatches(job,{...p,id:"c".repeat(32)}),false);
});
test("curve changes and audible video-layer fades invalidate stale captions; position edits do not",()=>{
  const p=project();p.overlays.push({id:"layer",kind:"video",media_id:"voice",start:0,end:2,in:0,out:2,volume:.5,x:.5,y:.5});
  const job=complete(p);p.overlays[0].x=.8;assert.equal(auto.resultMatches(job,p),true);
  p.overlays[0].fade_out=.4;assert.equal(auto.resultMatches(job,p),false);
  const curve=project(), original=complete(curve);curve.clips[0].speed_curve=[{time:0,speed:1},{time:4,speed:2}];assert.equal(auto.resultMatches(original,curve),false);
});
test("completed result creates editable captions with one undo step and preserves the original project",()=>{
  const p=project(),job=complete(p),session=new editor.ProjectSession(p);let id=0;
  const result=auto.captionLayers(job,session.project,{totalDuration:4,makeId:()=>`cue_${++id}`});
  session.change(value=>value.overlays.push(...result.layers));assert.equal(session.project.overlays.length,1);assert.equal(p.overlays.length,0);
  assert.equal(session.project.overlays[0].text,"測試字幕");assert.equal(auto.jobAlreadyApplied(job,session.project),true);
  assert.throws(()=>auto.captionLayers(job,session.project,{totalDuration:4,makeId:()=>`cue_${++id}`}),/已加入/);
  session.travel(true);assert.equal(session.project.overlays.length,0);assert.equal(auto.jobAlreadyApplied(job,session.project),false);
  assert.doesNotThrow(()=>auto.captionLayers(job,session.project,{totalDuration:4,makeId:()=>`cue_${++id}`}));session.travel(false);assert.equal(session.project.overlays.length,1);
});
test("stale, empty or over-capacity results do not leave partial subtitles",()=>{
  const p=project(),job=complete(p);p.clips[0].out=3;assert.throws(()=>auto.captionLayers(job,p,{totalDuration:3,makeId:()=>"id"}),/時間軸/);
  const fresh=project();fresh.overlays=Array.from({length:5000},(_,i)=>({id:`title_${i}`,kind:"text",track_id:"subtitle_full"}));
  assert.throws(()=>auto.captionLayers(complete(fresh),fresh,{totalDuration:4,makeId:()=>"id"}),/5,000/);
  assert.throws(()=>auto.captionLayers({...complete(project()),cues:[]},project(),{totalDuration:4,makeId:()=>"id"}),/字幕/);
});
test("job validation and status labels explain setup, progress, cancellation and failure",()=>{
  assert.equal(auto.validateJob({job:complete(project())}).status,"completed");
  for(const value of [null,{}, {job:{id:"../secret",project_id:"a".repeat(32),status:"completed"}}])assert.throws(()=>auto.validateJob(value),/格式/);
  assert.match(auto.jobLabel({status:"running",stage:"model"}),/下載/);
  assert.match(auto.jobLabel({status:"running",stage:"transcribing",progress:50}),/50%/);
  assert.match(auto.jobLabel({status:"cancelled"}),/取消/);
  assert.equal(auto.jobLabel({status:"failed",error:"無語音"}),"無語音");
});
