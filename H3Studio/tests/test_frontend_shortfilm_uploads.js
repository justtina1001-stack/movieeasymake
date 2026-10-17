"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const copy = value => JSON.parse(JSON.stringify(value));
function project(id) {
  return { id, revision: 1, title: id, assets: [{ id: "actor", alias: "Alice", image_asset_ids: ["existing"], audio_asset_id: null }],
    scenes: [{ id: "scene", shots: [
      { id: "selected", asset_ids: ["actor"], action: "The subject waves.", continue_previous: false },
      { id: "connected", asset_ids: [], action: "Continue the motion.", continue_previous: true },
      { id: "independent", asset_ids: [], action: "An empty room.", continue_previous: false },
    ].map(shot => ({ ...shot, job_id: `job-${id}-${shot.id}`, status: "completed", continuation_asset_id: "old-tail", continuation_job_id: "old-parent" })) }] };
}
function fixture(uploadFile) {
  const first = project("first"), second = project("second");
  const saved = [], rendered = [], notices = [];
  const context = vm.createContext({
    shortFilmProjects: [first, second], activeShortFilmId: first.id, uploadFile,
    scheduleShortFilmSave: target => saved.push({ target, snapshot: copy(target) }),
    renderShortFilmAssets: () => rendered.push("assets"), renderShortFilmScenes: () => rendered.push("scenes"),
    renderShortFilmSummary: () => rendered.push("summary"), toast: (...args) => notices.push(args),
  });
  for (const name of ["shortFilmFlatten", "shortFilmAliasMentioned", "shortFilmShotAssetSelection", "shortFilmAssetShotIds", "invalidateShortFilmRenders", "uploadShortFilmAssetFiles"]) {
    const start = source.search(new RegExp(`(?:async )?function ${name}\\(`));
    assert.notEqual(start, -1, `${name} exists in the application`);
    vm.runInContext(source.slice(start, source.indexOf("\n}", start) + 2), context);
  }
  return { context, first, second, saved, rendered, notices };
}
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

test("when the second image fails the accepted first image invalidates affected shots and is scheduled for saving", async () => {
  const calls = [];
  const f = fixture(async (file, kind) => {
    calls.push([file, kind]);
    if (file === "second.png") throw new Error("network interrupted");
    return { id: "uploaded-first" };
  });
  await assert.rejects(f.context.uploadShortFilmAssetFiles(f.first, "actor", ["first.png", "second.png", "third.png"], "images"), /已加入 1 個素材.*network interrupted/);
  assert.deepEqual(calls, [["first.png", "shortfilm-reference-image"], ["second.png", "shortfilm-reference-image"]]);
  assert.deepEqual(copy(f.first.assets[0].image_asset_ids), ["existing", "uploaded-first"]);
  const [selected, connected, independent] = f.first.scenes[0].shots;
  for (const shot of [selected, connected]) {
    assert.equal(shot.status, "draft");
    assert.equal(shot.job_id, null);
    assert.equal(shot.continuation_asset_id, null);
    assert.equal(shot.continuation_job_id, null);
  }
  assert.equal(independent.status, "completed");
  assert.equal(f.saved.length, 1);
  assert.strictEqual(f.saved[0].target, f.first);
  assert.equal(f.saved[0].snapshot.scenes[0].shots[0].status, "draft");
  assert.deepEqual(f.rendered, ["assets", "scenes", "summary"]);
});

test("switching projects while upload is pending updates only the captured project and does not redraw the new project", async () => {
  const wait = deferred();
  const f = fixture(() => wait.promise);
  const originalSecond = copy(f.second);
  const operation = f.context.uploadShortFilmAssetFiles(f.first, "actor", ["first.png"], "images");
  f.context.activeShortFilmId = f.second.id;
  // A status refresh may replace the original project object during the upload.
  const refreshedFirst = copy(f.first);
  f.context.shortFilmProjects[0] = refreshedFirst;
  wait.resolve({ id: "uploaded-first" });
  await operation;
  assert.deepEqual(copy(refreshedFirst.assets[0].image_asset_ids), ["existing", "uploaded-first"]);
  assert.deepEqual(copy(f.first.assets[0].image_asset_ids), ["existing"]);
  assert.deepEqual(copy(f.second), originalSecond);
  assert.strictEqual(f.saved[0].target, refreshedFirst);
  assert.deepEqual(f.rendered, []);
});

test("an asset deleted while uploading is not recreated and does not cause an unrelated save", async () => {
  const wait = deferred();
  const f = fixture(() => wait.promise);
  const operation = f.context.uploadShortFilmAssetFiles(f.first, "actor", ["first.png"], "images");
  f.first.assets = [];
  wait.resolve({ id: "uploaded-first" });
  await operation;
  assert.deepEqual(f.first.assets, []);
  assert.equal(f.saved.length, 0);
  assert.equal(f.first.scenes[0].shots[0].status, "completed");
  assert.equal(f.notices.length, 1);
  assert.match(f.notices[0][0], /原素材已移除/);
  assert.deepEqual(copy(f.second.assets[0].image_asset_ids), ["existing"]);
});

test("a deleted project cannot redirect a late upload into the active project's matching asset id", async () => {
  const wait = deferred();
  const f = fixture(() => wait.promise);
  const operation = f.context.uploadShortFilmAssetFiles(f.first, "actor", ["first.png"], "images");
  f.context.shortFilmProjects = [f.second];
  f.context.activeShortFilmId = f.second.id;
  wait.resolve({ id: "uploaded-first" });
  await operation;
  assert.deepEqual(copy(f.second.assets[0].image_asset_ids), ["existing"]);
  assert.equal(f.saved.length, 0);
  assert.deepEqual(f.rendered, []);
});

test("repeated image ids returned by uploads are kept only once alongside existing images", async () => {
  const resultIds = ["existing", "new-image", "new-image"];
  const f = fixture(async () => ({ id: resultIds.shift() }));
  await f.context.uploadShortFilmAssetFiles(f.first, "actor", ["one.png", "two.png", "three.png"], "images");
  assert.deepEqual(copy(f.first.assets[0].image_asset_ids), ["existing", "new-image"]);
  assert.equal(f.saved.length, 1);
  assert.deepEqual(f.saved[0].snapshot.assets[0].image_asset_ids, ["existing", "new-image"]);
});

test("a failed first upload does not invalidate shots or schedule unchanged content", async () => {
  const f = fixture(async () => { throw new Error("offline"); });
  await assert.rejects(f.context.uploadShortFilmAssetFiles(f.first, "actor", ["one.png"], "images"), /offline/);
  assert.deepEqual(copy(f.first.assets[0].image_asset_ids), ["existing"]);
  assert.equal(f.first.scenes[0].shots[0].status, "completed");
  assert.equal(f.saved.length, 0);
  assert.deepEqual(f.rendered, []);
});
