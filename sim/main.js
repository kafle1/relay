// R.E.L.A.Y. junction sim — Three.js, realistic pass.
// Real GLB vehicles + image-based lighting + filmic post-processing ("CCTV footage" look).
// v1 topology = 4-way straight-through; config-driven multi-topology comes next.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { FilmPass } from 'three/addons/postprocessing/FilmPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

// ─── config ───
const ROAD_HALF = 5, LANE = 2.5, STOP = 6, HALF_JCT = 5;
const GAP = 1.6, SPEED = 14, START = 70, EXIT = 70, SPAWN_EVERY = 0.9;
const MODELS = 'assets/models/';

// vehicle types → GLB files, target length, spawn weight (Kathmandu = motorcycle-heavy), PCU, yaw fix
// Kathmandu-style mix: motorcycle-dominant, taxis, cars, some heavy vehicles, rare ambulance.
// spd = base pace multiplier (each vehicle also gets its own random jitter). cls = detector class if it differs.
const TYPES = {
  motorcycle: { files: ['polypizza_motorcycle.glb', 'polypizza_scooter.glb'], len: 2.1, w: 0.9, weight: 0.50, pcu: 0.3, yaw: Math.PI, spd: 1.15 },
  car:        { files: ['kenney_sedan.glb', 'kenney_sedan-sports.glb', 'kenney_hatchback-sports.glb', 'kenney_suv.glb', 'kenney_suv-luxury.glb', 'kenney_van.glb'], len: 4.2, w: 1.9, weight: 0.20, pcu: 1, yaw: Math.PI, spd: 1.0 },
  taxi:       { files: ['kenney_taxi.glb'], len: 4.2, w: 1.9, weight: 0.12, pcu: 1, yaw: Math.PI, spd: 1.0, cls: 'car' },
  truck:      { files: ['kenney_truck.glb', 'kenney_delivery.glb', 'kenney_garbage-truck.glb', 'kenney_truck-flat.glb'], len: 6, w: 2.2, weight: 0.06, pcu: 2.5, yaw: Math.PI, spd: 0.82 },
  bus:        { files: ['polypizza_bus.glb'], len: 10.5, w: 2.5, weight: 0.06, pcu: 2.5, yaw: Math.PI, spd: 0.80 },
  ambulance:  { files: ['kenney_ambulance.glb'], len: 5, w: 2.1, weight: 0.02, pcu: 2, yaw: Math.PI, emergency: true, spd: 1.05 },
};

// approaches: dir = side the car comes FROM. progress u: -START → -STOP (line) → 0 → +EXIT.
const APPROACH = {
  N: { axis: 'z', sign: -1, off: -LANE, group: 'NS', rotY: 0 },
  S: { axis: 'z', sign: +1, off: +LANE, group: 'NS', rotY: Math.PI },
  E: { axis: 'x', sign: -1, off: +LANE, group: 'EW', rotY: Math.PI / 2 },
  W: { axis: 'x', sign: +1, off: -LANE, group: 'EW', rotY: -Math.PI / 2 },
};
const DIRS = Object.keys(APPROACH);

// ─── renderer ───
const canvas = document.getElementById('app');
const CAP = +(new URLSearchParams(location.search).get('capture') || 0);   // ?capture=N → dump N labeled frames
const LIVE = new URLSearchParams(location.search).has('live');             // ?live → closed loop (YOLO server drives signals)
let liveSignals = null, liveCounts = null, livePhase = '—', liveBoxes = [], liveMetrics = null;
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: CAP > 0 || LIVE });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.ACESFilmicToneMapping;   // filmic → realistic contrast
renderer.toneMappingExposure = 0.82;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x8ba6bf);
scene.fog = new THREE.Fog(0x8ba6bf, 180, 400);

// image-based lighting for realistic material response + reflections
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

const camera = new THREE.PerspectiveCamera(42, innerWidth / innerHeight, 0.1, 800);
camera.position.set(24, 26, 34);   // realistic elevated pole-cam height (not a drone)
camera.lookAt(0, -1, 2);

// drag to orbit / scroll to zoom / right-drag to pan. Disabled in capture mode so training
// frames stay on the canonical pose the detector was fine-tuned for.
let controls = null;
if (!CAP && !LIVE) {
  controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(0, 0, 0);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.maxPolarAngle = Math.PI * 0.49;   // stay above ground level
  controls.update();
}

// ─── lights ───
scene.add(new THREE.HemisphereLight(0xbcd6f0, 0x55564a, 0.3));
const sun = new THREE.DirectionalLight(0xfff2e0, 1.5);
sun.position.set(48, 78, 30);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.camera.near = 10; sun.shadow.camera.far = 240;
sun.shadow.camera.left = -95; sun.shadow.camera.right = 95;
sun.shadow.camera.top = 95; sun.shadow.camera.bottom = -95;
sun.shadow.bias = -0.0003;
scene.add(sun);

// ─── procedural asphalt texture ───
function noiseTex(size, base, amp) {
  const c = document.createElement('canvas'); c.width = c.height = size;
  const g = c.getContext('2d'), img = g.createImageData(size, size);
  for (let i = 0; i < img.data.length; i += 4) {
    const n = (Math.random() * amp) | 0;
    img.data[i] = base[0] + n; img.data[i + 1] = base[1] + n; img.data[i + 2] = base[2] + n; img.data[i + 3] = 255;
  }
  g.putImageData(img, 0, 0);
  const t = new THREE.CanvasTexture(c); t.wrapS = t.wrapT = THREE.RepeatWrapping; return t;
}
const asphalt = noiseTex(256, [34, 36, 40], 16); asphalt.repeat.set(40, 40);
const concrete = noiseTex(256, [120, 122, 120], 22); concrete.repeat.set(60, 60);

const M = {
  ground:   new THREE.MeshStandardMaterial({ map: concrete, roughness: 1 }),
  road:     new THREE.MeshStandardMaterial({ map: asphalt, roughness: 0.92, metalness: 0 }),
  mark:     new THREE.MeshStandardMaterial({ color: 0xf6f2e8, roughness: 0.5 }),
  sidewalk: new THREE.MeshStandardMaterial({ color: 0x8f9398, roughness: 0.95 }),
  pole:     new THREE.MeshStandardMaterial({ color: 0x2b2f33, roughness: 0.7, metalness: 0.5 }),
};

function box(w, h, d, mat, x, y, z) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
  m.position.set(x, y, z); return m;
}

// ─── ground, roads, surroundings ───
const ground = box(600, 0.2, 600, M.ground, 0, -0.1, 0); ground.receiveShadow = true; scene.add(ground);
const roadNS = box(ROAD_HALF * 2, 0.12, 600, M.road, 0, 0, 0); roadNS.receiveShadow = true; scene.add(roadNS);
const roadEW = box(600, 0.12, ROAD_HALF * 2, M.road, 0, 0.001, 0); roadEW.receiveShadow = true; scene.add(roadEW);

for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
  const sw = box(30, 0.26, 30, M.sidewalk, sx * (ROAD_HALF + 15), 0.13, sz * (ROAD_HALF + 15));
  sw.receiveShadow = true; scene.add(sw);
  const h = 7 + Math.random() * 9;
  const b = box(12 + Math.random() * 6, h, 12 + Math.random() * 6,
    new THREE.MeshStandardMaterial({ color: new THREE.Color().setHSL(0.06 + Math.random() * 0.09, 0.14, 0.24 + Math.random() * 0.14), roughness: 0.88 }),
    sx * (ROAD_HALF + 24), h / 2, sz * (ROAD_HALF + 24));
  b.castShadow = true; b.receiveShadow = true; scene.add(b);
}

// markings
for (let d = HALF_JCT + 2; d < 260; d += 8) for (const s of [-1, 1]) {
  scene.add(box(0.3, 0.02, 3, M.mark, 0, 0.14, s * d));
  scene.add(box(3, 0.02, 0.3, M.mark, s * d, 0.141, 0));
}
for (const dir of DIRS) {
  const a = APPROACH[dir], coord = a.sign * (-STOP);
  if (a.axis === 'z') {
    scene.add(box(ROAD_HALF * 2, 0.02, 0.5, M.mark, 0, 0.145, coord));
    for (let i = -4; i <= 4; i += 2) scene.add(box(0.7, 0.02, 2.4, M.mark, i, 0.144, coord + a.sign * 2));
  } else {
    scene.add(box(0.5, 0.02, ROAD_HALF * 2, M.mark, coord, 0.145, 0));
    for (let i = -4; i <= 4; i += 2) scene.add(box(2.4, 0.02, 0.7, M.mark, coord + a.sign * 2, 0.144, i));
  }
}

// ─── signal heads ───
const BULB = { red: 0xff3b30, yellow: 0xffcc00, green: 0x34c759, off: 0x181b20 };
const signalHeads = {};
for (const dir of DIRS) {
  const a = APPROACH[dir], g = new THREE.Group();
  g.add(box(0.3, 6, 0.3, M.pole, 0, 3, 0));
  g.add(box(1, 3, 0.6, new THREE.MeshStandardMaterial({ color: 0x111417, roughness: 0.6 }), 0, 6, 0));
  const bulbs = {};
  ['red', 'yellow', 'green'].forEach((c, i) => {
    const mat = new THREE.MeshStandardMaterial({ color: BULB.off, emissive: BULB.off, emissiveIntensity: 1 });
    const m = new THREE.Mesh(new THREE.SphereGeometry(0.34, 16, 16), mat);
    m.position.set(0, 7 - i, 0.32); g.add(m); bulbs[c] = m;
  });
  const coord = a.sign * (-STOP);
  if (a.axis === 'z') g.position.set(a.off > 0 ? -ROAD_HALF - 1.2 : ROAD_HALF + 1.2, 0, coord + a.sign * 1.5);
  else g.position.set(coord + a.sign * 1.5, 0, a.off > 0 ? ROAD_HALF + 1.2 : -ROAD_HALF - 1.2);
  g.userData.bulbs = bulbs; scene.add(g); signalHeads[dir] = g;
}
function setSignal(dir, state) {
  const b = signalHeads[dir].userData.bulbs;
  for (const c of ['red', 'yellow', 'green']) {
    const on = c === state;
    b[c].material.color.setHex(on ? BULB[c] : BULB.off);
    b[c].material.emissive.setHex(on ? BULB[c] : BULB.off);
    b[c].material.emissiveIntensity = on ? 2.2 : 1;
  }
}

// ─── fixed-cycle controller (BASELINE) ───
const CYCLE = [
  { green: 'NS', d: 7 }, { yellow: 'NS', d: 2 }, { allred: true, d: 1 },
  { green: 'EW', d: 7 }, { yellow: 'EW', d: 2 }, { allred: true, d: 1 },
];
let cycleIdx = 0, cycleT = 0;
const groupState = g => CYCLE[cycleIdx].green === g ? 'green' : CYCLE[cycleIdx].yellow === g ? 'yellow' : 'red';
function updateSignals(dt) {
  cycleT += dt;
  if (cycleT >= CYCLE[cycleIdx].d) { cycleT = 0; cycleIdx = (cycleIdx + 1) % CYCLE.length; }
  for (const dir of DIRS) setSignal(dir, groupState(APPROACH[dir].group));
  const s = CYCLE[cycleIdx];
  return s.allred ? 'ALL-RED' : `${s.green || s.yellow} ${s.green ? 'GREEN' : 'YELLOW'}`;
}
// current signal for an approach: the live server's decision when in live mode, else the internal cycle
function signalOf(dir) {
  if (LIVE && liveSignals) return liveSignals[dir] || 'red';
  return groupState(APPROACH[dir].group);
}

// ─── load vehicle models → pools ───
const loader = new GLTFLoader();
const pools = {};   // type → [template pivots]
let ready = false;

function normalize(root, targetLen, yaw) {
  root.traverse(o => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
  let bb = new THREE.Box3().setFromObject(root), size = new THREE.Vector3(); bb.getSize(size);
  if (size.x > size.z) root.rotation.y = Math.PI / 2;             // length along Z
  bb = new THREE.Box3().setFromObject(root); bb.getSize(size);
  const ctr = new THREE.Vector3(); bb.getCenter(ctr);
  root.position.set(-ctr.x, -bb.min.y, -ctr.z);                   // centre x/z, sit on ground
  const pivot = new THREE.Group(); pivot.add(root);
  pivot.scale.setScalar(targetLen / Math.max(size.x, size.z));
  pivot.rotation.y = yaw;
  return pivot;
}
const loadGLB = url => new Promise((res, rej) => loader.load(url, g => res(g.scene), undefined, rej));

async function loadModels() {
  for (const [type, cfg] of Object.entries(TYPES)) {
    pools[type] = [];
    for (const f of cfg.files) {
      try { pools[type].push(normalize(await loadGLB(MODELS + f), cfg.len, cfg.yaw)); }
      catch (e) { console.warn('load failed', f, e); }
    }
  }
  ready = Object.values(pools).some(p => p.length);
  console.log('models ready', Object.fromEntries(Object.entries(pools).map(([k, v]) => [k, v.length])));
}

const typeKeys = Object.keys(TYPES);
function pickType() {
  let r = Math.random(), sum = 0;
  for (const t of typeKeys) { sum += TYPES[t].weight; if (r <= sum) return t; }
  return 'car';
}

// ─── cars ───
const cars = [];
function placeCar(c) {
  const a = APPROACH[c.dir], coord = a.sign * c.u;
  if (a.axis === 'z') c.mesh.position.set(a.off, 0, coord);
  else c.mesh.position.set(coord, 0, a.off);
}
function pickModel(type) {
  const p = pools[type];
  return p && p.length ? p[(Math.random() * p.length) | 0].clone(true) : null;
}
function addCar(dir, u) {                                 // one vehicle: random type, its own speed/pace
  const type = pickType(), model = pickModel(type);
  if (!model) return false;
  const T = TYPES[type], len = T.len;
  if (cars.some(c => c.dir === dir && Math.abs(c.u - u) < (c.len + len) / 2 + GAP)) return false;  // keep gap
  const mesh = new THREE.Group(); mesh.add(model); mesh.rotation.y = APPROACH[dir].rotY;
  const speed = SPEED * (T.spd || 1) * (0.85 + Math.random() * 0.3);   // different speed / different pace per vehicle
  const c = { dir, u, mesh, len, type, speed };
  placeCar(c); scene.add(mesh); cars.push(c);
  return true;
}
function spawn(dir) { if (ready) addCar(dir, -START); }
function prefill(n) { for (let k = 0; k < n; k++) addCar(DIRS[(Math.random() * DIRS.length) | 0], -START + Math.random() * (START - 4)); }
function forceSpawn(dir, type) {                         // edge-case staging: force a specific vehicle in
  if (!ready) return;
  const model = pickModel(type); if (!model) return;
  const T = TYPES[type];
  const mesh = new THREE.Group(); mesh.add(model); mesh.rotation.y = APPROACH[dir].rotY;
  const speed = SPEED * (T.spd || 1) * (0.85 + Math.random() * 0.3);
  const c = { dir, u: -START, mesh, len: T.len, type, speed };
  placeCar(c); scene.add(mesh); cars.push(c);
}
function surge(dir, n = 9) { for (let i = 0; i < n; i++) setTimeout(() => forceSpawn(dir, Math.random() < 0.65 ? 'motorcycle' : 'car'), i * 130); }
function scenarioPanel() {
  const p = document.createElement('div');
  Object.assign(p.style, { position: 'fixed', top: '12px', right: '12px', zIndex: 11, display: 'flex', gap: '6px', flexWrap: 'wrap', maxWidth: '250px', justifyContent: 'flex-end' });
  const mk = (label, fn, id) => {
    const b = document.createElement('button'); b.textContent = label; if (id) b.id = id;
    Object.assign(b.style, { font: '12px ui-monospace, monospace', color: '#e8eaed', background: 'rgba(20,24,30,.85)', border: '1px solid rgba(255,255,255,.14)', borderRadius: '6px', padding: '6px 9px', cursor: 'pointer' });
    b.onclick = fn; p.appendChild(b);
  };
  for (const d of DIRS) mk('🚑 ' + d, () => forceSpawn(d, 'ambulance'), 'amb-' + d);
  mk('surge N', () => surge('N'));
  mk('surge E', () => surge('E'));
  document.body.appendChild(p);
}
function updateCars(dt) {
  const byDir = {}; DIRS.forEach(d => byDir[d] = []);
  for (const c of cars) byDir[c.dir].push(c);
  for (const dir of DIRS) {
    const list = byDir[dir].sort((p, q) => p.u - q.u);
    const mustStop = signalOf(dir) !== 'green';
    for (let i = 0; i < list.length; i++) {
      const c = list[i];
      let target = c.u + c.speed * dt;
      const stopTarget = -STOP - c.len / 2;
      // <= + epsilon: a car parked exactly AT the line must stay held (strict < released it → red-running)
      if (mustStop && c.u <= stopTarget + 0.01) target = Math.min(target, stopTarget);
      const leader = list[i + 1];
      if (leader) target = Math.min(target, leader.u - (c.len + leader.len) / 2 - GAP);
      c.u = Math.max(c.u, target);
      placeCar(c);
    }
  }
  for (let i = cars.length - 1; i >= 0; i--) {
    if (cars[i].u > EXIT) { scene.remove(cars[i].mesh); cars.splice(i, 1); }
  }
}
function counts() {
  const c = { N: 0, S: 0, E: 0, W: 0 };
  for (const car of cars) if (car.u < HALF_JCT) c[car.dir]++;
  return c;
}

// ─── post-processing (filmic camera look), with safe fallback ───
let composer = null;
try {
  composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(innerWidth, innerHeight), 0.12, 0.5, 0.9);
  composer.addPass(bloom);
  composer.addPass(new FilmPass(0.15));                            // subtle grain → "footage"
  composer.addPass(new OutputPass());
} catch (e) { console.warn('post-processing off', e); composer = null; }

// ─── HUD ───
const hud = {
  phase: document.getElementById('h-phase'),
  N: document.getElementById('h-N'), S: document.getElementById('h-S'),
  E: document.getElementById('h-E'), W: document.getElementById('h-W'),
  total: document.getElementById('h-total'),
};

// ─── capture mode: auto-labeled training frames (3D box → 2D YOLO label) ───
const CLASS = { car: 0, motorcycle: 1, bus: 2, truck: 3, ambulance: 4, autorickshaw: 5 };
const _v = new THREE.Vector3(), _bb = new THREE.Box3();
let capN = 0, capAcc = 0;
function labelFor(car) {
  _bb.setFromObject(car.mesh);
  _bb.getCenter(_v).project(camera);
  if (_v.z < -1 || _v.z > 1) return null;                         // behind camera / outside frustum
  let x1 = 2, y1 = 2, x2 = -2, y2 = -2;
  for (const X of [_bb.min.x, _bb.max.x]) for (const Y of [_bb.min.y, _bb.max.y]) for (const Z of [_bb.min.z, _bb.max.z]) {
    _v.set(X, Y, Z).project(camera);
    const sx = _v.x * 0.5 + 0.5, sy = -_v.y * 0.5 + 0.5;
    x1 = Math.min(x1, sx); y1 = Math.min(y1, sy); x2 = Math.max(x2, sx); y2 = Math.max(y2, sy);
  }
  x1 = Math.max(0, x1); y1 = Math.max(0, y1); x2 = Math.min(1, x2); y2 = Math.min(1, y2);
  const w = x2 - x1, h = y2 - y1;
  if (w <= 0.006 || h <= 0.006) return null;                      // off-frame / too small
  const cls = CLASS[TYPES[car.type].cls || car.type];
  return `${cls} ${((x1 + x2) / 2).toFixed(6)} ${((y1 + y2) / 2).toFixed(6)} ${w.toFixed(6)} ${h.toFixed(6)}`;
}
function captureTick(dt) {
  capAcc += dt;
  if (capAcc < 0.2 || capN >= CAP) return;
  capAcc = 0;
  const label = cars.map(labelFor).filter(Boolean).join('\n');
  const image = renderer.domElement.toDataURL('image/jpeg', 0.9);
  const name = 'frame_' + String(capN).padStart(5, '0');
  fetch('/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, image, label }) }).catch(() => {});
  if (++capN % 25 === 0) console.log('captured', capN, '/', CAP);
  if (capN >= CAP) console.log('CAPTURE DONE', CAP);
}

// ─── live closed loop: stream frames to the YOLO server, apply its signals + draw its detections ───
let overlay = null, octx = null, mstat = null, spark = null, sctx = null, banner = null;
const waHist = [], wfHist = [];
const PR = Math.min(devicePixelRatio, 2);
if (LIVE) {
  overlay = document.createElement('canvas');
  Object.assign(overlay.style, { position: 'fixed', inset: '0', width: '100vw', height: '100vh', pointerEvents: 'none', zIndex: 5 });
  document.body.appendChild(overlay); octx = overlay.getContext('2d');
  const panel = document.createElement('div');
  Object.assign(panel.style, { position: 'fixed', right: '12px', bottom: '12px', zIndex: 10,
    font: '12px ui-monospace, Menlo, monospace', color: '#e8eaed', background: 'rgba(12,14,18,.62)',
    padding: '10px 12px', borderRadius: '8px', border: '1px solid rgba(255,255,255,.08)', minWidth: '224px' });
  panel.innerHTML = '<div style="color:#7dd3fc;letter-spacing:.06em;margin-bottom:6px">R.E.L.A.Y · live control</div>' +
    '<div id="m-red" style="font-size:22px;color:#86efac;font-weight:700">— %</div>' +
    '<div style="color:#9aa0a6;margin-bottom:6px">less waiting vs a fixed timer</div>' +
    '<canvas id="spark" width="224" height="52" style="width:224px;height:52px"></canvas>' +
    '<div style="color:#9aa0a6;margin-top:4px"><span style="color:#ff453a">■</span> fixed &nbsp; <span style="color:#86efac">■</span> R.E.L.A.Y</div>';
  document.body.appendChild(panel);
  mstat = panel.querySelector('#m-red'); spark = panel.querySelector('#spark'); sctx = spark.getContext('2d');
  banner = document.createElement('div');
  Object.assign(banner.style, { position: 'fixed', top: '14px', left: '50%', transform: 'translateX(-50%)', zIndex: 12, display: 'none',
    font: '700 14px ui-monospace, monospace', color: '#fff', background: 'rgba(255,59,48,.92)', padding: '8px 16px', borderRadius: '8px', boxShadow: '0 4px 16px rgba(0,0,0,.45)' });
  document.body.appendChild(banner);
  sizeOverlay();
}
function sizeOverlay() { if (overlay) { overlay.width = innerWidth * PR; overlay.height = innerHeight * PR; } }
function drawOverlay() {
  if (!octx) return;
  const W = overlay.width, H = overlay.height;
  octx.clearRect(0, 0, W, H);
  const COL = { car: '#34c759', motorcycle: '#ff9f0a', bus: '#5ac8fa', truck: '#ff453a', ambulance: '#ffffff', autorickshaw: '#bf5af2' };
  octx.lineWidth = 2 * PR; octx.font = `600 ${12 * PR}px ui-monospace, monospace`;
  for (const b of liveBoxes) {
    const col = COL[b.cls] || '#34c759';
    octx.strokeStyle = col; octx.strokeRect(b.x * W, b.y * H, b.w * W, b.h * H);
    octx.fillStyle = col; octx.fillText(`${b.cls} ${b.conf}`, b.x * W, Math.max(12 * PR, b.y * H - 3 * PR));
  }
}
function drawSpark() {
  if (!sctx) return;
  const W = spark.width, H = spark.height;
  sctx.clearRect(0, 0, W, H);
  const mx = Math.max(1, ...waHist, ...wfHist);
  const line = (hist, col) => {
    sctx.strokeStyle = col; sctx.lineWidth = 2; sctx.beginPath();
    hist.forEach((v, i) => { const x = i / Math.max(1, hist.length - 1) * W, y = H - (v / mx) * (H - 4) - 2; i ? sctx.lineTo(x, y) : sctx.moveTo(x, y); });
    sctx.stroke();
  };
  line(wfHist, '#ff453a'); line(waHist, '#86efac');
}
const _pv = new THREE.Vector3();
function projPt(x, y, z) { _pv.set(x, y, z).project(camera); return [_pv.x * 0.5 + 0.5, -_pv.y * 0.5 + 0.5]; }
function computeZones() {   // 4 screen-space approach polygons (incoming lane strips) for the detector
  const Z = {}, hw = ROAD_HALF / 2, y = 1.2, back = 45;
  for (const dir of DIRS) {
    const a = APPROACH[dir], near = a.sign * (-STOP), far = a.sign * (-back);
    Z[dir] = (a.axis === 'z'
      ? [[a.off - hw, y, near], [a.off + hw, y, near], [a.off + hw, y, far], [a.off - hw, y, far]]
      : [[near, y, a.off - hw], [near, y, a.off + hw], [far, y, a.off + hw], [far, y, a.off - hw]]
    ).map(c => projPt(c[0], c[1], c[2]));
  }
  return Z;
}
let ws = null, wsOpen = false, sendAcc = 0;
function connectWS() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => { wsOpen = true; ws.send(JSON.stringify({ type: 'zones', zones: computeZones() })); };
  ws.onclose = () => { wsOpen = false; setTimeout(connectWS, 800); };
  ws.onerror = () => {};
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    liveSignals = m.signals; livePhase = `${m.phase} ${m.stage}`; liveBoxes = m.boxes || []; liveCounts = m.counts; liveMetrics = m.metrics;
    for (const dir of DIRS) setSignal(dir, m.signals[dir] || 'red');
    if (banner) {
      const emg = m.emergencies || [];
      banner.style.display = emg.length ? 'block' : 'none';
      if (emg.length) banner.textContent = '🚑 EMERGENCY PREEMPT — clearing ' + emg.join(', ');
    }
    if (m.metrics) {
      mstat.textContent = `↓ ${m.metrics.reduction} %`;
      waHist.push(m.metrics.adaptive); wfHist.push(m.metrics.fixed);
      if (waHist.length > 224) { waHist.shift(); wfHist.shift(); }
      drawSpark();
    }
  };
}

// ─── loop ───
const clock = new THREE.Clock();
let simTime = 0;
const nextSpawn = { N: 0, S: 0, E: 0, W: 0 };
const dirRate = Object.fromEntries(DIRS.map(d => [d, 0.55 + Math.random() * 1.3]));   // some approaches busier than others
function tick() {
  const dt = Math.min(clock.getDelta(), 0.05);
  const phaseLabel = LIVE ? livePhase : updateSignals(dt);
  updateCars(dt);
  simTime += dt;                                          // organic per-approach random flow (varied rates)
  for (const dir of DIRS) if (ready && simTime >= nextSpawn[dir]) {
    spawn(dir);
    nextSpawn[dir] = simTime + (0.5 + Math.random() * 1.8) / dirRate[dir];
  }
  const c = (LIVE && liveCounts) ? liveCounts : counts();
  hud.phase.textContent = phaseLabel;
  hud.N.textContent = c.N; hud.S.textContent = c.S; hud.E.textContent = c.E; hud.W.textContent = c.W;
  hud.total.textContent = cars.length;
  if (controls) controls.update();
  (composer || renderer).render(scene, camera);
  if (CAP && ready) captureTick(dt);
  if (LIVE) {
    drawOverlay();
    sendAcc += dt;
    if (wsOpen && ready && sendAcc >= 0.18) {
      sendAcc = 0;
      const emg = [...new Set(cars.filter(c => c.type === 'ambulance' && c.u < HALF_JCT).map(c => c.dir))];
      try { ws.send(JSON.stringify({ type: 'frame', image: renderer.domElement.toDataURL('image/jpeg', 0.6), emergencies: emg })); } catch (e) {}
    }
  }
  requestAnimationFrame(tick);
}

loadModels().then(() => { prefill(28); scenarioPanel(); clock.start(); if (LIVE) connectWS(); tick(); });

addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
  if (composer) composer.setSize(innerWidth, innerHeight);
  sizeOverlay();
});
