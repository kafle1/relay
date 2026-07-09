// R.E.L.A.Y. junction sim — an endless, live synthetic CCTV feed of a signalized junction.
//
// Modes (URL params, combinable):
//   ?live      closed loop: frames stream to the server, YOLO + controller drive the signals
//   ?capture=N dump N auto-labeled training frames (3D boxes projected to YOLO labels)
//   ?topo=4|T|2  junction shape (4-way, 3-arm T, plain 2-arm crossing)
//   ?lanes=1|2|3 lanes per direction (2/4/6-lane roads)
//
// Built for 60 fps: no shadow maps (cheap blob shadows instead), all static geometry merged
// into a handful of draw calls, frames streamed downscaled + async.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

// ─────────────────────────── config ───────────────────────────
const P = new URLSearchParams(location.search);
const LIVE = P.has('live');
const CAP = +(P.get('capture') || 0);
const TOPO = (P.get('topo') || '4').toUpperCase();
const LANES = Math.min(3, Math.max(1, +(P.get('lanes') || 1)));

const LANE_W = 3;
const ROAD_HALF = LANES * LANE_W + 0.4;          // half road width = junction box half-size
const ZEBRA = { from: ROAD_HALF + 0.7, to: ROAD_HALF + 3.3 };
const STOP = ZEBRA.to + 0.9;                     // stop line sits BEHIND the crossing
const START = 78 + ROAD_HALF;                    // spawn / despawn distance from the centre
const GAP = 1.6, SPEED = 14, MAX_CARS = 70;

// vehicle mix — Kathmandu-style: motorcycle-dominant, taxis, few heavy vehicles, rare ambulance.
// spd = pace multiplier (each vehicle adds its own jitter); cls = detector class when it differs.
const TYPES = {
  motorcycle: { files: ['polypizza_motorcycle.glb', 'polypizza_scooter.glb'], len: 2.1, weight: 0.50, spd: 1.15 },
  car:        { files: ['kenney_sedan.glb', 'kenney_sedan-sports.glb', 'kenney_hatchback-sports.glb', 'kenney_suv.glb', 'kenney_suv-luxury.glb', 'kenney_van.glb'], len: 4.2, weight: 0.20, spd: 1.0 },
  taxi:       { files: ['kenney_taxi.glb'], len: 4.2, weight: 0.12, spd: 1.0, cls: 'car' },
  truck:      { files: ['kenney_truck.glb', 'kenney_delivery.glb', 'kenney_garbage-truck.glb', 'kenney_truck-flat.glb'], len: 6, weight: 0.06, spd: 0.82 },
  bus:        { files: ['polypizza_bus.glb'], len: 10.5, weight: 0.06, spd: 0.80 },
  ambulance:  { files: ['kenney_ambulance.glb'], len: 5, weight: 0.02, spd: 1.05 },
};

// approaches: dir = the side traffic comes FROM. Progress u runs -START → -STOP (line) → 0 → +START.
// side = which half of the road this approach occupies; lanes fan out from the centreline.
const APPROACH = {
  N: { axis: 'z', sign: -1, side: -1, group: 'NS', rotY: 0 },
  S: { axis: 'z', sign: +1, side: +1, group: 'NS', rotY: Math.PI },
  E: { axis: 'x', sign: -1, side: +1, group: 'EW', rotY: Math.PI / 2 },
  W: { axis: 'x', sign: +1, side: -1, group: 'EW', rotY: -Math.PI / 2 },
};
if (TOPO === 'T') { delete APPROACH.W; APPROACH.E.group = 'E'; }
if (TOPO === '2') { delete APPROACH.E; delete APPROACH.W; }
const DIRS = Object.keys(APPROACH);
const GROUPS = [...new Set(DIRS.map(d => APPROACH[d].group))];
const laneOff = (dir, lane) => APPROACH[dir].side * ((lane + 0.5) * LANE_W);
const hasRoad = (axis, s) =>                     // does a road arm exist on this side of the centre?
  axis === 'z' ? true : TOPO === '4' || (TOPO === 'T' && s === 1);

// ─────────────────────────── renderer / scene ───────────────────────────
const canvas = document.getElementById('app');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
renderer.setSize(innerWidth, innerHeight);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 0.95;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x9ab4cc);
scene.fog = new THREE.Fog(0x9ab4cc, 200, 420);
scene.environment = new THREE.PMREMGenerator(renderer).fromScene(new RoomEnvironment(), 0.04).texture;

const camera = new THREE.PerspectiveCamera(42, innerWidth / innerHeight, 0.1, 800);
camera.position.set(26, 28, 38);                 // elevated pole-cam, CCTV-like
camera.lookAt(0, -1, 2);

let controls = null;
if (!CAP && !LIVE) {                             // free-roam only; live/capture keep the canonical pose
  controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(0, 0, 0);
  controls.enableDamping = true;
  controls.maxPolarAngle = Math.PI * 0.49;
}

scene.add(new THREE.HemisphereLight(0xcfe0f0, 0x5a604e, 0.5));
const sun = new THREE.DirectionalLight(0xfff2df, 1.4);
sun.position.set(50, 80, 30);
scene.add(sun);

// ─────────────────────────── world ───────────────────────────
function noiseTexture(base, amp, repeat) {
  const size = 128, c = document.createElement('canvas');
  c.width = c.height = size;
  const g = c.getContext('2d'), img = g.createImageData(size, size);
  for (let i = 0; i < img.data.length; i += 4) {
    const n = (Math.random() * amp) | 0;
    img.data.set([base[0] + n, base[1] + n, base[2] + n, 255], i);
  }
  g.putImageData(img, 0, 0);
  const t = new THREE.CanvasTexture(c);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(repeat, repeat);
  return t;
}

function windowsTexture(tint) {
  const c = document.createElement('canvas');
  c.width = 128; c.height = 256;
  const g = c.getContext('2d');
  g.fillStyle = tint;
  g.fillRect(0, 0, 128, 256);
  for (let y = 12; y < 244; y += 26) for (let x = 10; x < 118; x += 24) {
    g.fillStyle = Math.random() < 0.35 ? '#cfd8b8' : '#20262e';
    g.fillRect(x, y, 14, 16);
  }
  return new THREE.CanvasTexture(c);
}

const MAT = {
  grass:    new THREE.MeshStandardMaterial({ map: noiseTexture([96, 112, 74], 20, 50), roughness: 1 }),
  asphalt:  new THREE.MeshStandardMaterial({ map: noiseTexture([46, 48, 52], 14, 30), roughness: 0.95 }),
  paint:    new THREE.MeshStandardMaterial({ color: 0xf2eee2, roughness: 0.6 }),
  yellow:   new THREE.MeshStandardMaterial({ color: 0xd9b23a, roughness: 0.6 }),
  sidewalk: new THREE.MeshStandardMaterial({ map: noiseTexture([172, 166, 152], 18, 20), roughness: 1 }),
  curb:     new THREE.MeshStandardMaterial({ color: 0x9b968a, roughness: 0.9 }),
  pole:     new THREE.MeshStandardMaterial({ color: 0x2b2f33, roughness: 0.7, metalness: 0.4 }),
  housing:  new THREE.MeshStandardMaterial({ color: 0x14171b, roughness: 0.6 }),
  trunk:    new THREE.MeshStandardMaterial({ color: 0x6b503a, roughness: 1 }),
  leaves:   new THREE.MeshStandardMaterial({ color: 0x4f7a3d, roughness: 1 }),
};

// merge many boxes into one mesh (one draw call) — the core of the 60fps rebuild
function mergedBoxes(parts, material) {
  const geos = parts.map(([w, h, d, x, y, z]) => new THREE.BoxGeometry(w, h, d).translate(x, y, z));
  const mesh = new THREE.Mesh(mergeGeometries(geos), material);
  geos.forEach(g => g.dispose());
  scene.add(mesh);
  return mesh;
}

function buildWorld() {
  const R = ROAD_HALF, L = 320;                  // arm length

  mergedBoxes([[640, 0.2, 640, 0, -0.1, 0]], MAT.grass);
  const roads = [[R * 2, 0.12, L * 2, 0, 0, 0]];                       // NS
  if (TOPO === '4') roads.push([L * 2, 0.12, R * 2, 0, 0.001, 0]);     // EW
  if (TOPO === 'T') roads.push([L, 0.12, R * 2, L / 2, 0.001, 0]);     // E stem
  mergedBoxes(roads, MAT.asphalt);

  // sidewalks: raised corner slabs with a curb lip along every road edge
  const slabs = [], curbs = [];
  for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
    const cx = sx * (R + 16), cz = sz * (R + 16);
    slabs.push([30, 0.3, 30, cx, 0.15, cz]);
    curbs.push([0.5, 0.34, 30, sx * (R + 0.8), 0.17, cz]);             // along the NS road
    if (hasRoad('x', sx)) curbs.push([30, 0.34, 0.5, cx, 0.17, sz * (R + 0.8)]);
  }
  mergedBoxes(slabs, MAT.sidewalk);
  mergedBoxes(curbs, MAT.curb);

  // road markings, one merged mesh: lane dashes + centrelines, zebra bands, stop lines
  const white = [], t = 0.025, y = 0.14;
  for (const s of [-1, 1]) {
    for (let d = STOP + 3; d < L - 10; d += 8) {
      white.push([0.3, t, 3, 0, y, s * d]);                            // NS centreline
      if (hasRoad('x', s)) white.push([3, t, 0.3, s * d, y, 0]);       // EW centreline
      for (let k = 1; k < LANES; k++) {                                // lane dividers
        white.push([0.22, t, 3, k * LANE_W, y, s * d], [0.22, t, 3, -k * LANE_W, y, s * d]);
        if (hasRoad('x', s)) white.push([3, t, 0.22, s * d, y, k * LANE_W], [3, t, 0.22, s * d, y, -k * LANE_W]);
      }
    }
  }
  for (const dir of DIRS) {                                            // zebra + stop line per approach
    const a = APPROACH[dir];
    const zMid = a.sign * -((ZEBRA.from + ZEBRA.to) / 2), zLen = ZEBRA.to - ZEBRA.from;
    for (let i = -R + 0.8; i <= R - 0.8; i += 1.15) {                  // dense proper zebra bars
      if (a.axis === 'z') white.push([0.62, t, zLen, i, y + 0.002, zMid]);
      else white.push([zLen, t, 0.62, zMid, y + 0.002, i]);
    }
    const sMid = a.sign * -STOP, half = a.side * (R / 2);              // stop line on the incoming half only
    if (a.axis === 'z') white.push([R, t, 0.45, half, y + 0.003, sMid]);
    else white.push([0.45, t, R, sMid, y + 0.003, half]);
  }
  mergedBoxes(white, MAT.paint);

  // Kathmandu box-junction: yellow criss-cross hatching + outline inside the box
  const hatch = [], n = Math.max(4, LANES * 3);
  for (let i = 0; i <= n; i++) hatch.push([0.18, t, R * 2 * 1.35, -R + (2 * R * i) / n, y, 0]);
  const hatchMesh = mergedBoxes(hatch, MAT.yellow);
  hatchMesh.rotation.y = Math.PI / 4;
  hatchMesh.scale.set(0.72, 1, 0.72);
  const border = [];
  for (const s of [-1, 1]) border.push([R * 2, t, 0.18, 0, y, s * (R - 0.1)], [0.18, t, R * 2, s * (R - 0.1), y, 0]);
  mergedBoxes(border, MAT.yellow);

  // corner buildings with lit windows + a few trees
  for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
    const h = 9 + Math.random() * 10;
    const b = new THREE.Mesh(
      new THREE.BoxGeometry(13 + Math.random() * 5, h, 13 + Math.random() * 5),
      new THREE.MeshStandardMaterial({ map: windowsTexture(['#8a7f6d', '#7d8489', '#93867b'][(Math.random() * 3) | 0]), roughness: 0.9 }),
    );
    b.position.set(sx * (R + 25), h / 2, sz * (R + 25));
    scene.add(b);
  }
  const trunks = [], crowns = [];
  for (const sx of [-1, 1]) for (const sz of [-1, 1]) for (let k = 0; k < 2; k++) {
    const x = sx * (R + 6 + k * 9), z = sz * (R + 6 + (1 - k) * 9);
    trunks.push([0.4, 2.4, 0.4, x, 1.2, z]);
    crowns.push([2.6, 2.6, 2.6, x, 3.6, z]);
  }
  mergedBoxes(trunks, MAT.trunk);
  mergedBoxes(crowns, MAT.leaves);
}
buildWorld();

// ─────────────────────────── signal heads ───────────────────────────
const BULB = { red: 0xff3b30, yellow: 0xffcc00, green: 0x34c759, off: 0x181b20 };
const signalHeads = {};
for (const dir of DIRS) {
  const a = APPROACH[dir], g = new THREE.Group();
  const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.14, 0.16, 6, 8), MAT.pole);
  pole.position.y = 3;
  g.add(pole);
  const back = new THREE.Mesh(new THREE.BoxGeometry(1.5, 3.5, 0.1), MAT.housing);
  back.position.set(0, 6, -0.26);
  g.add(back);
  const housing = new THREE.Mesh(new THREE.BoxGeometry(1, 3, 0.6), MAT.housing);
  housing.position.y = 6;
  g.add(housing);
  const bulbs = {};
  ['red', 'yellow', 'green'].forEach((c, i) => {
    const m = new THREE.Mesh(new THREE.SphereGeometry(0.34, 14, 14),
      new THREE.MeshStandardMaterial({ color: BULB.off, emissive: BULB.off }));
    m.position.set(0, 7 - i, 0.32);
    g.add(m);
    bulbs[c] = m;
  });
  // on the kerb beside this approach's incoming lanes, just past the stop line
  const along = a.sign * -(STOP + 1.2);
  const aside = (a.side > 0 ? -1 : 1) * (ROAD_HALF + 1.6);
  if (a.axis === 'z') g.position.set(aside, 0, along);
  else g.position.set(along, 0, -aside);
  g.userData.bulbs = bulbs;
  scene.add(g);
  signalHeads[dir] = g;
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

// ─────────────────────────── signal control ───────────────────────────
// The dumb fixed cycle is both the non-live default and the "system OFF" baseline.
const CYCLE = GROUPS.flatMap(g => [{ green: g, d: 7 }, { yellow: g, d: 2 }, { allred: true, d: 1 }]);
let cycleIdx = 0, cycleT = 0;
let systemOn = true;                             // live mode: ON = adaptive server, OFF = fixed timer
let liveSignals = null, liveCounts = null, livePhase = '—', liveBoxes = [];

const groupState = g => CYCLE[cycleIdx].green === g ? 'green' : CYCLE[cycleIdx].yellow === g ? 'yellow' : 'red';
function signalOf(dir) {
  if (LIVE && systemOn && liveSignals) return liveSignals[dir] || 'red';
  return groupState(APPROACH[dir].group);
}
function tickSignals(dt) {
  cycleT += dt;
  if (cycleT >= CYCLE[cycleIdx].d) { cycleT = 0; cycleIdx = (cycleIdx + 1) % CYCLE.length; }
  for (const dir of DIRS) setSignal(dir, signalOf(dir));
  const s = CYCLE[cycleIdx];
  return s.allred ? 'ALL-RED' : `${s.green || s.yellow} ${s.green ? 'GREEN' : 'YELLOW'}`;
}

// ─────────────────────────── vehicles ───────────────────────────
const loader = new GLTFLoader();
const pools = {};
let ready = false;

// shared soft blob shadow (replaces shadow maps — the big 60fps win)
const blobMat = (() => {
  const c = document.createElement('canvas');
  c.width = c.height = 64;
  const g = c.getContext('2d');
  const grad = g.createRadialGradient(32, 32, 4, 32, 32, 30);
  grad.addColorStop(0, 'rgba(0,0,0,0.38)');
  grad.addColorStop(1, 'rgba(0,0,0,0)');
  g.fillStyle = grad;
  g.fillRect(0, 0, 64, 64);
  return new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(c), transparent: true, depthWrite: false });
})();

function normalize(root, targetLen) {
  let bb = new THREE.Box3().setFromObject(root), size = new THREE.Vector3();
  bb.getSize(size);
  if (size.x > size.z) root.rotation.y = Math.PI / 2;    // put the length along Z
  bb = new THREE.Box3().setFromObject(root);
  bb.getSize(size);
  const ctr = new THREE.Vector3();
  bb.getCenter(ctr);
  root.position.set(-ctr.x, -bb.min.y, -ctr.z);          // centre on origin, sit on the ground
  const pivot = new THREE.Group();
  pivot.add(root);
  pivot.scale.setScalar(targetLen / Math.max(size.x, size.z));
  pivot.rotation.y = Math.PI;                            // models face -Z; our forward is +u
  return pivot;
}

async function loadModels() {
  const load = url => new Promise((res, rej) => loader.load(url, g => res(g.scene), undefined, rej));
  for (const [type, cfg] of Object.entries(TYPES)) {
    pools[type] = [];
    for (const f of cfg.files) {
      try { pools[type].push(normalize(await load('assets/models/' + f), cfg.len)); }
      catch { console.warn('model failed to load:', f); }
    }
  }
  ready = Object.values(pools).some(p => p.length);
}

function pickType() {
  let r = Math.random(), sum = 0;
  for (const t in TYPES) { sum += TYPES[t].weight; if (r <= sum) return t; }
  return 'car';
}

const cars = [];
function placeCar(c) {
  const a = APPROACH[c.dir], along = a.sign * c.u, off = laneOff(c.dir, c.lane);
  if (a.axis === 'z') c.mesh.position.set(off, 0, along);
  else c.mesh.position.set(along, 0, off);
}
function addCar(dir, u, forcedType) {
  if (!ready || (cars.length >= MAX_CARS && !forcedType)) return;
  const type = forcedType || pickType();
  const pool = pools[type];
  if (!pool || !pool.length) return;
  const T = TYPES[type], lane = (Math.random() * LANES) | 0;
  if (cars.some(c => c.dir === dir && c.lane === lane && Math.abs(c.u - u) < (c.len + T.len) / 2 + GAP)) return;
  const mesh = new THREE.Group();
  mesh.add(pool[(Math.random() * pool.length) | 0].clone(true));
  const blob = new THREE.Mesh(new THREE.PlaneGeometry(T.len * 0.9, T.len * 1.25), blobMat);
  blob.rotation.x = -Math.PI / 2;
  blob.position.y = 0.02;
  mesh.add(blob);
  mesh.rotation.y = APPROACH[dir].rotY;
  const c = { dir, u, lane, mesh, len: T.len, type, speed: SPEED * (T.spd || 1) * (0.85 + Math.random() * 0.3) };
  placeCar(c);
  scene.add(mesh);
  cars.push(c);
}

function moveCars(dt) {
  const byLane = {};
  for (const c of cars) (byLane[c.dir + c.lane] ||= []).push(c);      // follow the leader in YOUR lane
  for (const dir of DIRS) {
    const held = signalOf(dir) !== 'green';
    for (let lane = 0; lane < LANES; lane++) {
      const list = (byLane[dir + lane] || []).sort((p, q) => p.u - q.u);
      for (let i = 0; i < list.length; i++) {
        const c = list[i], before = c.u;
        let target = c.u + c.speed * dt;
        const stopAt = -STOP - c.len / 2;
        // <= + epsilon: a car parked exactly AT the line stays held (strict < would release it)
        if (held && c.u <= stopAt + 0.01) target = Math.min(target, stopAt);
        const leader = list[i + 1];
        if (leader) target = Math.min(target, leader.u - (c.len + leader.len) / 2 - GAP);
        c.u = Math.max(c.u, target);
        c.blocked = (c.u - before) < 0.25 * c.speed * dt;
        placeCar(c);
      }
    }
  }
  for (let i = cars.length - 1; i >= 0; i--) {
    if (cars[i].u > START) { scene.remove(cars[i].mesh); cars.splice(i, 1); }
  }
}

const counts = () => {
  const c = Object.fromEntries(DIRS.map(d => [d, 0]));
  for (const car of cars) if (car.u < ROAD_HALF) c[car.dir]++;
  return c;
};
const queuedNow = () => cars.filter(c => c.u < -STOP + 0.5 && c.blocked).length;

// organic arrivals: each approach has its own rate; the live demo uses an imbalanced pattern
// (busy NS, quiet EW — exactly the situation where a fixed timer wastes green)
const nextSpawn = Object.fromEntries(DIRS.map(d => [d, 1 + Math.random() * 2]));
const rate = LIVE
  ? { N: 1.7, S: 1.5, E: 0.4, W: 0.4 }
  : Object.fromEntries(DIRS.map(d => [d, 0.55 + Math.random() * 1.3]));
let simTime = 0;
function spawnTick() {
  for (const dir of DIRS) {
    if (simTime >= nextSpawn[dir]) {
      addCar(dir, -START);
      nextSpawn[dir] = simTime + (0.5 + Math.random() * 1.8) / (rate[dir] || 1);
    }
  }
}

// ─────────────────────────── HUD + scenario controls ───────────────────────────
const hud = Object.fromEntries(['phase', 'N', 'S', 'E', 'W', 'total'].map(k => [k, document.getElementById('h-' + k)]));

function button(label, onClick, id) {
  const b = document.createElement('button');
  b.textContent = label;
  if (id) b.id = id;
  Object.assign(b.style, {
    font: '12px ui-monospace, monospace', color: '#e8eaed', background: 'rgba(20,24,30,.85)',
    border: '1px solid rgba(255,255,255,.14)', borderRadius: '6px', padding: '6px 9px', cursor: 'pointer',
  });
  b.onclick = onClick;
  return b;
}
function scenarioPanel() {
  const p = document.createElement('div');
  Object.assign(p.style, { position: 'fixed', top: '12px', right: '12px', zIndex: 11, display: 'flex', gap: '6px', flexWrap: 'wrap', maxWidth: '260px', justifyContent: 'flex-end' });
  for (const d of DIRS) p.appendChild(button('🚑 ' + d, () => addCar(d, -START, 'ambulance'), 'amb-' + d));
  const surge = d => { for (let i = 0; i < 9; i++) setTimeout(() => addCar(d, -START, Math.random() < 0.65 ? 'motorcycle' : 'car'), i * 130); };
  p.appendChild(button('surge N', () => surge('N')));
  if (DIRS.includes('E')) p.appendChild(button('surge E', () => surge('E')));
  document.body.appendChild(p);
  junctionPanel();
}

// live junction switcher: any shape × any lane count, rebuilt on the spot
function junctionPanel() {
  const p = document.createElement('div');
  Object.assign(p.style, { position: 'fixed', top: '196px', left: '12px', zIndex: 11, display: 'flex', gap: '5px', flexDirection: 'column',
    font: '12px ui-monospace, monospace', color: '#9aa0a6', background: 'rgba(12,14,18,.55)', padding: '8px 10px', borderRadius: '8px' });
  const rebuild = (topo, lanes) => {
    const q = new URLSearchParams(location.search);
    q.set('topo', topo); q.set('lanes', lanes);
    location.search = q.toString();                 // clean rebuild with the new geometry
  };
  const row = (label, items, active, onPick) => {
    const r = document.createElement('div');
    r.style.display = 'flex'; r.style.gap = '5px'; r.style.alignItems = 'center';
    const l = document.createElement('span'); l.textContent = label; l.style.width = '46px'; r.appendChild(l);
    for (const [text, value] of items) {
      const b = button(text, () => onPick(value));
      if (String(value) === String(active)) { b.style.background = '#7dd3fc'; b.style.color = '#0b0d10'; }
      r.appendChild(b);
    }
    return r;
  };
  p.appendChild(row('shape', [['4-way', '4'], ['T', 'T'], ['2-arm', '2']], TOPO, v => rebuild(v, LANES)));
  p.appendChild(row('lanes', [['1', 1], ['2', 2], ['3', 3]], LANES, v => rebuild(TOPO, v)));
  document.body.appendChild(p);
}

// ─────────────────────────── live mode (closed loop) ───────────────────────────
let overlay, octx, statLine, spark, sctx, banner;
let modeWait = 0, modeT = 0;                     // waiting accumulated under the current mode
const waHist = [], wfHist = [];
const PR = Math.min(devicePixelRatio, 1.5);

function buildLiveUI() {
  overlay = document.createElement('canvas');
  Object.assign(overlay.style, { position: 'fixed', inset: '0', width: '100vw', height: '100vh', pointerEvents: 'none', zIndex: 5 });
  document.body.appendChild(overlay);
  octx = overlay.getContext('2d');
  sizeOverlay();

  const panel = document.createElement('div');
  Object.assign(panel.style, { position: 'fixed', right: '12px', bottom: '12px', zIndex: 10,
    font: '12px ui-monospace, Menlo, monospace', color: '#e8eaed', background: 'rgba(12,14,18,.62)',
    padding: '10px 12px', borderRadius: '8px', border: '1px solid rgba(255,255,255,.08)', minWidth: '230px' });
  panel.innerHTML =
    '<div style="color:#7dd3fc;letter-spacing:.06em;margin-bottom:6px">R.E.L.A.Y · live control</div>' +
    '<div id="m-stat" style="font-size:17px;color:#86efac;font-weight:700">warming up…</div>' +
    '<div style="color:#9aa0a6;margin-bottom:6px">current mode load (lower = better)</div>' +
    '<canvas id="spark" width="230" height="52" style="width:230px;height:52px"></canvas>' +
    '<div style="color:#9aa0a6;margin-top:4px"><span style="color:#ff453a">■</span> fixed &nbsp; <span style="color:#86efac">■</span> R.E.L.A.Y</div>';
  document.body.appendChild(panel);
  statLine = panel.querySelector('#m-stat');
  spark = panel.querySelector('#spark');
  sctx = spark.getContext('2d');

  banner = document.createElement('div');
  Object.assign(banner.style, { position: 'fixed', top: '14px', left: '50%', transform: 'translateX(-50%)', zIndex: 12, display: 'none',
    font: '700 14px ui-monospace, monospace', color: '#fff', background: 'rgba(255,59,48,.92)', padding: '8px 16px', borderRadius: '8px' });
  document.body.appendChild(banner);

  const toggle = button('', () => { systemOn = !systemOn; modeWait = 0; modeT = 0; paint(); }, 'sys-toggle');
  Object.assign(toggle.style, { position: 'fixed', bottom: '14px', left: '50%', transform: 'translateX(-50%)', zIndex: 12,
    font: '700 16px ui-monospace, monospace', border: 'none', borderRadius: '10px', padding: '12px 22px' });
  const paint = () => {
    toggle.textContent = systemOn ? 'R.E.L.A.Y.  ON  — click to switch OFF' : 'FIXED TIMER (system OFF) — click to turn ON';
    toggle.style.background = systemOn ? '#86efac' : '#ff6b62';
    toggle.style.color = systemOn ? '#0b0d10' : '#fff';
  };
  paint();
  document.body.appendChild(toggle);
}
function sizeOverlay() {
  if (overlay) { overlay.width = innerWidth * PR; overlay.height = innerHeight * PR; }
}

const BOX_COLORS = { car: '#34c759', motorcycle: '#ff9f0a', bus: '#5ac8fa', truck: '#ff453a', ambulance: '#ffffff', autorickshaw: '#bf5af2' };
function drawOverlay() {
  const W = overlay.width, H = overlay.height;
  octx.clearRect(0, 0, W, H);
  octx.lineWidth = 2 * PR;
  octx.font = `600 ${12 * PR}px ui-monospace, monospace`;
  for (const b of liveBoxes) {
    const col = BOX_COLORS[b.cls] || '#34c759';
    octx.strokeStyle = col;
    octx.strokeRect(b.x * W, b.y * H, b.w * W, b.h * H);
    octx.fillStyle = col;
    octx.fillText(`${b.cls} ${b.conf}`, b.x * W, Math.max(12 * PR, b.y * H - 3 * PR));
  }
}
function drawSpark() {
  const W = spark.width, H = spark.height;
  sctx.clearRect(0, 0, W, H);
  const mx = Math.max(1, ...waHist, ...wfHist);
  const line = (hist, col) => {
    sctx.strokeStyle = col;
    sctx.lineWidth = 2;
    sctx.beginPath();
    hist.forEach((v, i) => {
      const x = i / Math.max(1, hist.length - 1) * W, y = H - (v / mx) * (H - 4) - 2;
      i ? sctx.lineTo(x, y) : sctx.moveTo(x, y);
    });
    sctx.stroke();
  };
  line(wfHist, '#ff453a');
  line(waHist, '#86efac');
}

// screen-space approach polygons for the detector, computed from the fixed camera pose
const _v = new THREE.Vector3();
const project = (x, y, z) => { _v.set(x, y, z).project(camera); return [_v.x * 0.5 + 0.5, -_v.y * 0.5 + 0.5]; };
function computeZones() {
  const zones = {};
  for (const dir of DIRS) {
    const a = APPROACH[dir];
    const near = a.sign * -STOP, far = a.sign * -(STOP + 42), mid = a.side * (ROAD_HALF / 2), hw = ROAD_HALF / 2;
    zones[dir] = (a.axis === 'z'
      ? [[mid - hw, 1.2, near], [mid + hw, 1.2, near], [mid + hw, 1.2, far], [mid - hw, 1.2, far]]
      : [[near, 1.2, mid - hw], [near, 1.2, mid + hw], [far, 1.2, mid + hw], [far, 1.2, mid - hw]]
    ).map(p => project(...p));
  }
  return zones;
}

// downscaled async frame streaming: copy the fresh frame to a small 2D canvas, encode off the hot path
const streamCanvas = document.createElement('canvas');
const streamCtx = streamCanvas.getContext('2d');
let ws = null, wsOpen = false, sendAcc = 0, sending = false;

function connectWS() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => { wsOpen = true; ws.send(JSON.stringify({ type: 'zones', zones: computeZones() })); };
  ws.onclose = () => { wsOpen = false; setTimeout(connectWS, 800); };
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    liveSignals = m.signals;
    livePhase = `${m.phase} ${m.stage}`;
    liveBoxes = m.boxes || [];
    liveCounts = m.counts;
    const emg = m.emergencies || [];
    banner.style.display = emg.length ? 'block' : 'none';
    if (emg.length) banner.textContent = '🚑 EMERGENCY PREEMPT — clearing ' + emg.join(', ');
    if (m.metrics) {
      const load = modeT > 3 ? (modeWait / modeT).toFixed(1) : '…';
      const tel = m.telemetry ? ` · ${m.telemetry.infer_ms}ms/frame` : '';
      statLine.textContent = `${queuedNow()} queued · ${load} veh waiting/s${tel}`;
      waHist.push(m.metrics.adaptive);
      wfHist.push(m.metrics.fixed);
      if (waHist.length > 230) { waHist.shift(); wfHist.shift(); }
      drawSpark();
    }
  };
}
function streamFrame() {
  if (!wsOpen || sending) return;
  sending = true;
  const w = 720, h = Math.round(innerHeight / innerWidth * 720);
  streamCanvas.width = w;
  streamCanvas.height = h;
  streamCtx.drawImage(renderer.domElement, 0, 0, w, h);              // same-frame copy: no preserveDrawingBuffer
  const emergencies = [...new Set(cars.filter(c => c.type === 'ambulance' && c.u < ROAD_HALF).map(c => c.dir))];
  streamCanvas.toBlob(blob => {
    sending = false;
    if (!blob || !wsOpen) return;
    const fr = new FileReader();
    fr.onload = () => { try { ws.send(JSON.stringify({ type: 'frame', image: fr.result, emergencies })); } catch {} };
    fr.readAsDataURL(blob);
  }, 'image/jpeg', 0.6);
}

// ─────────────────────────── capture mode (auto-labeled training frames) ───────────────────────────
const CLASS_ID = { car: 0, motorcycle: 1, bus: 2, truck: 3, ambulance: 4, autorickshaw: 5 };
const _bb = new THREE.Box3();
let capN = 0, capAcc = 0;

function labelFor(car) {
  _bb.setFromObject(car.mesh);
  let x1 = 2, y1 = 2, x2 = -2, y2 = -2;
  for (const X of [_bb.min.x, _bb.max.x]) for (const Y of [_bb.min.y, _bb.max.y]) for (const Z of [_bb.min.z, _bb.max.z]) {
    const [sx, sy] = project(X, Y, Z);
    x1 = Math.min(x1, sx); y1 = Math.min(y1, sy);
    x2 = Math.max(x2, sx); y2 = Math.max(y2, sy);
  }
  x1 = Math.max(0, x1); y1 = Math.max(0, y1);
  x2 = Math.min(1, x2); y2 = Math.min(1, y2);
  const w = x2 - x1, h = y2 - y1;
  if (w <= 0.006 || h <= 0.006) return null;
  const cls = CLASS_ID[TYPES[car.type].cls || car.type];
  return `${cls} ${((x1 + x2) / 2).toFixed(6)} ${((y1 + y2) / 2).toFixed(6)} ${w.toFixed(6)} ${h.toFixed(6)}`;
}
function captureTick(dt) {
  capAcc += dt;
  if (capAcc < 0.2 || capN >= CAP) return;
  capAcc = 0;
  const label = cars.map(labelFor).filter(Boolean).join('\n');
  streamCanvas.width = renderer.domElement.width;
  streamCanvas.height = renderer.domElement.height;
  streamCtx.drawImage(renderer.domElement, 0, 0);
  const image = streamCanvas.toDataURL('image/jpeg', 0.9);
  const name = 'frame_' + String(capN).padStart(5, '0');
  fetch('/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, image, label }) }).catch(() => {});
  if (++capN >= CAP) console.log('CAPTURE DONE', CAP);
}

// ─────────────────────────── main loop ───────────────────────────
const clock = new THREE.Clock();
function tick() {
  const dt = Math.min(clock.getDelta(), 0.05);
  simTime += dt;
  const fixedLabel = tickSignals(dt);
  moveCars(dt);
  spawnTick();
  if (LIVE) { modeWait += queuedNow() * dt; modeT += dt; }

  const c = (LIVE && liveCounts) || counts();
  hud.phase.textContent = (LIVE && systemOn) ? livePhase : fixedLabel + (LIVE ? ' (FIXED)' : '');
  for (const d of ['N', 'S', 'E', 'W']) hud[d].textContent = c[d] ?? '—';
  hud.total.textContent = cars.length;

  if (controls) controls.update();
  renderer.render(scene, camera);
  if (LIVE) {
    drawOverlay();
    sendAcc += dt;
    if (sendAcc >= 0.25 && ready) { sendAcc = 0; streamFrame(); }
  }
  if (CAP && ready) captureTick(dt);
  requestAnimationFrame(tick);
}

loadModels().then(() => {
  for (let i = 0; i < 28; i++) addCar(DIRS[(Math.random() * DIRS.length) | 0], -START + Math.random() * (START - 6));
  scenarioPanel();
  if (LIVE) { buildLiveUI(); connectWS(); }
  clock.start();
  tick();
});

addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
  sizeOverlay();
});
