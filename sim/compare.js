// R.E.L.A.Y. — split-screen: FIXED TIMER vs ADAPTIVE on IDENTICAL arrivals.
// Two independent junction worlds, one seeded RNG stream each (same seed) → the exact same
// vehicles arrive at the exact same times. Only the signal controller differs. Left rots, right flows.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

// ─── shared config (matches main.js) ───
const ROAD_HALF = 5, LANE = 2.5, STOP = 6, HALF_JCT = 5;
const GAP = 1.6, SPEED = 14, START = 70, EXIT = 70;
const MODELS = 'assets/models/';
const TYPES = {
  motorcycle: { files: ['polypizza_motorcycle.glb', 'polypizza_scooter.glb'], len: 2.1, weight: 0.50, pcu: 0.3, spd: 1.15 },
  car:        { files: ['kenney_sedan.glb', 'kenney_sedan-sports.glb', 'kenney_hatchback-sports.glb', 'kenney_suv.glb', 'kenney_suv-luxury.glb', 'kenney_van.glb'], len: 4.2, weight: 0.20, pcu: 1, spd: 1.0 },
  taxi:       { files: ['kenney_taxi.glb'], len: 4.2, weight: 0.12, pcu: 1, spd: 1.0 },
  truck:      { files: ['kenney_truck.glb', 'kenney_delivery.glb', 'kenney_garbage-truck.glb', 'kenney_truck-flat.glb'], len: 6, weight: 0.06, pcu: 2.5, spd: 0.82 },
  bus:        { files: ['polypizza_bus.glb'], len: 10.5, weight: 0.06, pcu: 2.5, spd: 0.80 },
  ambulance:  { files: ['kenney_ambulance.glb'], len: 5, weight: 0.02, pcu: 2, spd: 1.05 },
};
const APPROACH = {
  N: { axis: 'z', sign: -1, off: -LANE, group: 'NS', rotY: 0 },
  S: { axis: 'z', sign: +1, off: +LANE, group: 'NS', rotY: Math.PI },
  E: { axis: 'x', sign: -1, off: +LANE, group: 'EW', rotY: Math.PI / 2 },
  W: { axis: 'x', sign: +1, off: -LANE, group: 'EW', rotY: -Math.PI / 2 },
};
const DIRS = Object.keys(APPROACH);
// imbalanced Kathmandu-style demand: NS busy, EW light (the fixed timer's nightmare)
const LAMBDA = { N: 0.40, S: 0.34, E: 0.07, W: 0.07 };   // vehicles/second

// mulberry32 — deterministic stream so BOTH worlds see identical arrivals
function rng(seed) { let a = seed >>> 0; return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }

// ─── renderer / lighting (one renderer, two scissored viewports) ───
const canvas = document.getElementById('app');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 0.82;
renderer.setScissorTest(true);
const pmrem = new THREE.PMREMGenerator(renderer);
const envTex = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

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

const BULB = { red: 0xff3b30, yellow: 0xffcc00, green: 0x34c759, off: 0x181b20 };

// ─── vehicle model pools (shared templates, cloned per world) ───
const loader = new GLTFLoader();
const pools = {};
function normalize(root, targetLen) {
  root.traverse(o => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
  let bb = new THREE.Box3().setFromObject(root), size = new THREE.Vector3(); bb.getSize(size);
  if (size.x > size.z) root.rotation.y = Math.PI / 2;
  bb = new THREE.Box3().setFromObject(root); bb.getSize(size);
  const ctr = new THREE.Vector3(); bb.getCenter(ctr);
  root.position.set(-ctr.x, -bb.min.y, -ctr.z);
  const pivot = new THREE.Group(); pivot.add(root);
  pivot.scale.setScalar(targetLen / Math.max(size.x, size.z));
  pivot.rotation.y = Math.PI;
  return pivot;
}
const loadGLB = url => new Promise((res, rej) => loader.load(url, g => res(g.scene), undefined, rej));
async function loadModels() {
  for (const [type, cfg] of Object.entries(TYPES)) {
    pools[type] = [];
    for (const f of cfg.files) {
      try { pools[type].push(normalize(await loadGLB(MODELS + f), cfg.len)); } catch (e) { console.warn('load failed', f); }
    }
  }
}

// ─── controllers ───
class FixedTimer {                      // today's Kathmandu light: blind clock
  constructor() { this.seq = [['NS', 10], ['Y', 3], ['R', 1.5], ['EW', 10], ['Y2', 3], ['R2', 1.5]]; this.i = 0; this.t = 0; }
  tick(_counts, _emg, dt) {
    this.t += dt;
    if (this.t >= this.seq[this.i][1]) { this.t = 0; this.i = (this.i + 1) % this.seq.length; }
    const s = this.seq[this.i][0];
    return { NS: s === 'NS' ? 'green' : s === 'Y' ? 'yellow' : 'red', EW: s === 'EW' ? 'green' : s === 'Y2' ? 'yellow' : 'red' };
  }
}
class Adaptive {                        // JS port of the weighted max-pressure controller (src/controller.py)
  constructor() {
    this.t = { minG: 4, maxG: 25, yellow: 3, allRed: 1.5, gap: 2.0, maxWait: 45, wWait: 0.4, hyst: 1.0 };
    this.phase = 'NS'; this.target = null; this.stage = 'ALLRED'; this.elapsed = this.t.allRed;
    this.gapT = 0; this.wait = { N: 0, S: 0, E: 0, W: 0 };
    this.PH = { NS: ['N', 'S'], EW: ['E', 'W'] };
  }
  demand(p, c) { return this.PH[p].reduce((s, a) => s + (c[a] || 0), 0); }
  score(p, c, emg) {
    const oldest = Math.max(...this.PH[p].map(a => this.wait[a]));
    const boost = this.PH[p].some(a => emg.has(a)) ? 1e6 : 0;
    return this.demand(p, c) + this.t.wWait * oldest + boost;
  }
  best(c, emg) {
    const cands = Object.keys(this.PH).filter(p => this.demand(p, c) > 0);
    if (!cands.length) return null;
    return cands.reduce((a, b) => this.score(a, c, emg) >= this.score(b, c, emg) ? a : b);
  }
  emgPhase(emg) { for (const p in this.PH) if (this.PH[p].some(a => emg.has(a))) return p; return null; }
  starved(c) {
    const s = DIRS.filter(a => this.wait[a] >= this.t.maxWait && (c[a] || 0) > 0);
    if (!s.length) return null;
    const worst = s.reduce((a, b) => this.wait[a] >= this.wait[b] ? a : b);
    for (const p in this.PH) if (this.PH[p].includes(worst)) return p;
    return null;
  }
  switchTo(p) { if (p && p !== this.phase) { this.target = p; this.stage = 'YELLOW'; this.elapsed = 0; } }
  tick(c, emgArr, dt) {
    const emg = new Set(emgArr);
    const served = this.stage === 'GREEN' ? this.PH[this.phase] : [];
    for (const a of DIRS) this.wait[a] = served.includes(a) ? 0 : this.wait[a] + dt;
    this.elapsed += dt;
    if (this.stage === 'GREEN') {
      const sd = this.demand(this.phase, c);
      this.gapT = sd < 0.5 ? this.gapT + dt : 0;
      const empty = sd < 0.5, b = this.best(c, emg), e = this.emgPhase(emg);
      if (e && e !== this.phase && (this.elapsed >= this.t.minG || empty)) this.switchTo(e);
      else if (e === this.phase) { /* hold for the ambulance */ }
      else if (empty && b && b !== this.phase) this.switchTo(b);
      else if (this.elapsed >= this.t.minG) {
        const st = this.starved(c);
        if (st && st !== this.phase) this.switchTo(st);
        else if (this.gapT >= this.t.gap && b && b !== this.phase) this.switchTo(b);
        else if (this.elapsed >= this.t.maxG && b && b !== this.phase) this.switchTo(b);
        else if (b && b !== this.phase && this.score(b, c, emg) > this.score(this.phase, c, emg) + this.t.hyst) this.switchTo(b);
      }
    } else if (this.stage === 'YELLOW') {
      if (this.elapsed >= this.t.yellow) { this.stage = 'ALLRED'; this.elapsed = 0; }
    } else {
      if (this.target === null) this.target = this.emgPhase(emg) || this.starved(c) || this.best(c, emg);
      if (this.target !== null && this.elapsed >= this.t.allRed) {
        this.phase = this.target; this.stage = 'GREEN'; this.elapsed = 0; this.gapT = 0; this.target = null;
      }
    }
    const out = {};
    for (const p in this.PH) out[p] = (p === this.phase && this.stage === 'GREEN') ? 'green' : (p === this.phase && this.stage === 'YELLOW') ? 'yellow' : 'red';
    return out;
  }
}

// ─── one junction world ───
class World {
  constructor(controller, seed) {
    this.ctrl = controller;
    this.rand = rng(seed);
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x8ba6bf);
    this.scene.fog = new THREE.Fog(0x8ba6bf, 180, 400);
    this.scene.environment = envTex;
    this.camera = new THREE.PerspectiveCamera(44, (innerWidth / 2) / innerHeight, 0.1, 800);
    this.camera.position.set(26, 30, 38);
    this.camera.lookAt(0, -1, 0);
    this.cars = [];
    this.wait = 0;                      // cumulative veh-seconds queued
    this.nextArr = {}; DIRS.forEach(d => this.nextArr[d] = 1 + this.rand() * 2);
    this.time = 0;
    this.signals = { NS: 'red', EW: 'red' };
    this.buildScene();
  }
  buildScene() {
    const s = this.scene;
    s.add(new THREE.HemisphereLight(0xbcd6f0, 0x55564a, 0.3));
    const sun = new THREE.DirectionalLight(0xfff2e0, 1.5);
    sun.position.set(48, 78, 30); sun.castShadow = true;
    sun.shadow.mapSize.set(1024, 1024);
    sun.shadow.camera.near = 10; sun.shadow.camera.far = 240;
    sun.shadow.camera.left = -95; sun.shadow.camera.right = 95;
    sun.shadow.camera.top = 95; sun.shadow.camera.bottom = -95;
    s.add(sun);
    const M = {
      ground: new THREE.MeshStandardMaterial({ map: noiseTex(256, [120, 122, 120], 22), roughness: 1 }),
      road: new THREE.MeshStandardMaterial({ map: noiseTex(256, [34, 36, 40], 16), roughness: 0.92 }),
      mark: new THREE.MeshStandardMaterial({ color: 0xf6f2e8, roughness: 0.5 }),
      side: new THREE.MeshStandardMaterial({ color: 0x8f9398, roughness: 0.95 }),
      pole: new THREE.MeshStandardMaterial({ color: 0x2b2f33, roughness: 0.7, metalness: 0.5 }),
    };
    M.ground.map.repeat.set(60, 60); M.road.map.repeat.set(40, 40);
    const box = (w, h, d, mat, x, y, z) => { const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat); m.position.set(x, y, z); m.receiveShadow = true; s.add(m); return m; };
    box(500, 0.2, 500, M.ground, 0, -0.1, 0);
    box(ROAD_HALF * 2, 0.12, 500, M.road, 0, 0, 0);
    box(500, 0.12, ROAD_HALF * 2, M.road, 0, 0.001, 0);
    for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
      box(30, 0.26, 30, M.side, sx * (ROAD_HALF + 15), 0.13, sz * (ROAD_HALF + 15));
      const h = 7 + this.rand() * 8;
      const b = box(12, h, 12, new THREE.MeshStandardMaterial({ color: new THREE.Color().setHSL(0.08, 0.12, 0.3), roughness: 0.9 }), sx * (ROAD_HALF + 24), h / 2, sz * (ROAD_HALF + 24));
      b.castShadow = true;
    }
    for (let d = HALF_JCT + 2; d < 220; d += 8) for (const g of [-1, 1]) {
      box(0.3, 0.02, 3, M.mark, 0, 0.14, g * d);
      box(3, 0.02, 0.3, M.mark, g * d, 0.141, 0);
    }
    this.heads = {};
    for (const dir of DIRS) {
      const a = APPROACH[dir], g = new THREE.Group();
      const pole = new THREE.Mesh(new THREE.BoxGeometry(0.3, 6, 0.3), M.pole); pole.position.y = 3; g.add(pole);
      const housing = new THREE.Mesh(new THREE.BoxGeometry(1, 3, 0.6), new THREE.MeshStandardMaterial({ color: 0x111417 })); housing.position.y = 6; g.add(housing);
      const bulbs = {};
      ['red', 'yellow', 'green'].forEach((c, i) => {
        const m = new THREE.Mesh(new THREE.SphereGeometry(0.34, 12, 12), new THREE.MeshStandardMaterial({ color: BULB.off, emissive: BULB.off }));
        m.position.set(0, 7 - i, 0.32); g.add(m); bulbs[c] = m;
      });
      const coord = a.sign * (-STOP);
      if (a.axis === 'z') g.position.set(a.off > 0 ? -ROAD_HALF - 1.2 : ROAD_HALF + 1.2, 0, coord + a.sign * 1.5);
      else g.position.set(coord + a.sign * 1.5, 0, a.off > 0 ? ROAD_HALF + 1.2 : -ROAD_HALF - 1.2);
      g.userData.bulbs = bulbs; s.add(g); this.heads[dir] = g;
    }
  }
  setHead(dir, state) {
    const b = this.heads[dir].userData.bulbs;
    for (const c of ['red', 'yellow', 'green']) {
      const on = c === state;
      b[c].material.color.setHex(on ? BULB[c] : BULB.off);
      b[c].material.emissive.setHex(on ? BULB[c] : BULB.off);
      b[c].material.emissiveIntensity = on ? 2.2 : 1;
    }
  }
  pickType() { let r = this.rand(), sum = 0; for (const t in TYPES) { sum += TYPES[t].weight; if (r <= sum) return t; } return 'car'; }
  place(c) {
    const a = APPROACH[c.dir], coord = a.sign * c.u;
    if (a.axis === 'z') c.mesh.position.set(a.off, 0, coord);
    else c.mesh.position.set(coord, 0, a.off);
  }
  add(dir, type, u = -START) {
    const p = pools[type]; if (!p || !p.length) return;
    const T = TYPES[type];
    if (this.cars.some(c => c.dir === dir && Math.abs(c.u - u) < (c.len + T.len) / 2 + GAP)) return;
    const mesh = new THREE.Group();
    mesh.add(p[(this.rand() * p.length) | 0].clone(true));
    mesh.rotation.y = APPROACH[dir].rotY;
    const c = { dir, u, mesh, len: T.len, type, speed: SPEED * (T.spd || 1) * (0.85 + this.rand() * 0.3) };
    this.place(c); this.scene.add(mesh); this.cars.push(c);
  }
  counts() { const c = { N: 0, S: 0, E: 0, W: 0 }; for (const car of this.cars) if (car.u < HALF_JCT) c[car.dir]++; return c; }
  emergencies() { return [...new Set(this.cars.filter(c => c.type === 'ambulance' && c.u < HALF_JCT).map(c => c.dir))]; }
  queued() {                             // stopped in an approach = queued
    return this.cars.filter(c => c.u < -STOP + 0.5 && c.blocked).length;
  }
  step(dt) {
    this.time += dt;
    // identical arrivals: seeded stream (same seed both worlds → same schedule/type/speed draws)
    for (const d of DIRS) {
      while (this.time >= this.nextArr[d]) {
        this.add(d, this.pickType());
        this.nextArr[d] += -Math.log(1 - this.rand()) / LAMBDA[d];   // exponential inter-arrival
      }
    }
    const sig = this.ctrl.tick(this.counts(), this.emergencies(), dt);
    this.signals = sig;
    for (const dir of DIRS) this.setHead(dir, sig[APPROACH[dir].group]);
    // car motion
    const byDir = {}; DIRS.forEach(d => byDir[d] = []);
    for (const c of this.cars) byDir[c.dir].push(c);
    for (const dir of DIRS) {
      const list = byDir[dir].sort((p, q) => p.u - q.u);
      const mustStop = sig[APPROACH[dir].group] !== 'green';
      for (let i = 0; i < list.length; i++) {
        const c = list[i], before = c.u;
        let target = c.u + c.speed * dt;
        const stopTarget = -STOP - c.len / 2;
        // <= + epsilon: a car parked exactly AT the line must stay held (strict < released it → red-running)
        if (mustStop && c.u <= stopTarget + 0.01) target = Math.min(target, stopTarget);
        const leader = list[i + 1];
        if (leader) target = Math.min(target, leader.u - (c.len + leader.len) / 2 - GAP);
        c.u = Math.max(c.u, target);
        c.blocked = (c.u - before) < 0.25 * c.speed * dt;
        this.place(c);
      }
    }
    for (let i = this.cars.length - 1; i >= 0; i--) {
      if (this.cars[i].u > EXIT) { this.scene.remove(this.cars[i].mesh); this.cars.splice(i, 1); }
    }
    this.wait += this.queued() * dt;
  }
}

// ─── run ───
const SEED = 20260709;
const FF = +(new URLSearchParams(location.search).get('ff') || 0);   // ?ff=120 → pre-simulate 120s instantly
let L, R;
function build() {
  L = new World(new FixedTimer(), SEED); R = new World(new Adaptive(), SEED);
  window.L = L; window.R = R;                                         // exposed for inspection
  if (FF > 0) for (let t = 0; t < FF; t += 0.1) { L.step(0.1); R.step(0.1); }
}

const el = id => document.getElementById(id);
document.getElementById('b-amb').onclick = () => { L.add('N', 'ambulance'); R.add('N', 'ambulance'); };
document.getElementById('b-surge').onclick = () => {
  for (let i = 0; i < 8; i++) setTimeout(() => { L.add('N', 'motorcycle'); R.add('N', 'motorcycle'); }, i * 140);
};
document.getElementById('b-reset').onclick = () => {
  for (const w of [L, R]) for (const c of w.cars) w.scene.remove(c.mesh);
  build();
};

const clock = new THREE.Clock();
function frame() {
  const dt = Math.min(clock.getDelta(), 0.05);
  L.step(dt); R.step(dt);
  const W = innerWidth, H = innerHeight, PR = renderer.getPixelRatio();
  renderer.setViewport(0, 0, W / 2, H); renderer.setScissor(0, 0, W / 2 * PR, H * PR);
  renderer.render(L.scene, L.camera);
  renderer.setViewport(W / 2, 0, W / 2, H); renderer.setScissor(W / 2 * PR, 0, W / 2 * PR, H * PR);
  renderer.render(R.scene, R.camera);
  el('qL').textContent = L.queued(); el('qR').textContent = R.queued();
  el('wL').textContent = L.wait.toFixed(0); el('wR').textContent = R.wait.toFixed(0);
  const red = L.wait > 5 ? ((L.wait - R.wait) / L.wait * 100).toFixed(0) : '—';
  el('verdict').textContent = `same traffic · R.E.L.A.Y. ↓ ${red}% waiting`;
  requestAnimationFrame(frame);
}

loadModels().then(() => { build(); clock.start(); frame(); });

addEventListener('resize', () => {
  renderer.setSize(innerWidth, innerHeight);
  for (const w of [L, R]) if (w) { w.camera.aspect = (innerWidth / 2) / innerHeight; w.camera.updateProjectionMatrix(); }
});
