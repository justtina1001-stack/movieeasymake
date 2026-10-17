"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path");
const effects = require("../static/editor_transition_effects.js");
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures/transition_shader_reference.json"), "utf8"));
function sources() {
  const { width, height } = fixture;
  const left = new Uint8ClampedArray(width * height * 4), right = new Uint8ClampedArray(left.length);
  for (let y=0;y<height;y++) for(let x=0;x<width;x++) {
    const offset=(y*width+x)*4;
    left.set(x>=18&&x<36&&y>=8&&y<25 ? [20,210,90,255] : [x*3+12,y*5+15,(x*2+y*2+5)%256,255],offset);
    right.set(x>=30&&x<47&&y>=12&&y<30 ? [225,30,175,255] : [240-x*3,210-y*4,(x+y*3+70)%256,255],offset);
  }
  return { left, right };
}
for(const reference of fixture.cases) test(`${reference.type} matches independent original GLSL capture at ${reference.progress}`,()=>{
  const { left, right }=sources(), { width, height }=fixture;
  const actual=effects.applyPixels(left,right,width,height,reference.type,reference.progress);
  let maximum=0,total=0,count=0;
  fixture.points.forEach(([x,y],point)=>{
    const offset=(y*width+x)*4;
    for(let channel=0;channel<4;channel++) {
      const error=Math.abs(actual[offset+channel]-reference.rgba[point][channel]);
      maximum=Math.max(maximum,error);total+=error;count++;
    }
    assert.equal(actual[offset+3],255);
  });
  assert.ok(maximum<=3,`GPU jitter maximum error ${maximum}`);
  assert.ok(total/count<.35,`Mean byte error ${total/count}`);
});
