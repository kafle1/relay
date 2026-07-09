// R.E.L.A.Y. — Balkhu Corridor, Kathmandu.
//
// A stylized-but-recognizable stretch of Ring Road: J1 Balkhu Chowk → Balkhu Bridge over the
// Bagmati → J2 Kuleshwor/Vayodha → the curve down to J3 Ekantakuna. Each junction runs its OWN
// decentralized max-pressure controller; neighbours share downstream queue state so a backed-up
// bridge makes J1 stop feeding it. Toggle to a blind fixed 20s timer to see the difference.
//
// Movement is a waypoint-graph (lane polylines), NOT main.js's single-junction u-space — but the
// physics envelope, blob shadows, rider clones, and the 3-disc no-touch separation are ported
// verbatim-adapted from main.js. Built for 60fps: merged static geometry, no shadow maps.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { clone as cloneSkinned } from 'three/addons/utils/SkeletonUtils.js';

// ─────────────────────────── config ───────────────────────────
const LANE_W = 3;
const STEP = 2;                 // uniform route resampling (m) — poseAt indexes on this
const SPEED = 13;               // free-flow pace (m/s) before per-type + jitter
const GAP = 1.7;                // bumper daylight to a leader (m)
const SEP = 0.35;               // hard minimum surface gap in the no-touch check (m)
const MAX_CARS = 90;
const DETECT = 45;              // queue detection zone before a stop line (m)
const STOP_SETBACK = 2.6;       // stop line sits this far ahead of the junction box edge

// PCU-ish demand weights (match main.js's spirit): a bike is cheap, a bus is heavy
const PCU = { motorcycle: 0.4, car: 1, taxi: 1, truck: 2, bus: 2.5, ambulance: 1 };

// vehicle mix — motorcycle-dominant Kathmandu, copied from main.js TYPES
const TYPES = {
  motorcycle: { files: [['polypizza_motorcycle.glb', 0], ['polypizza_scooter.glb', Math.PI]], len: 2.1, weight: 0.50, spd: 1.15, rider: true },
  car:        { files: ['kenney_sedan.glb', 'kenney_sedan-sports.glb', 'kenney_hatchback-sports.glb', 'kenney_suv.glb', 'kenney_suv-luxury.glb', 'kenney_van.glb'], len: 4.2, weight: 0.20, spd: 1.0 },
  taxi:       { files: ['kenney_taxi.glb'], len: 4.2, weight: 0.12, spd: 1.0, cls: 'car' },
  truck:      { files: ['kenney_truck.glb', 'kenney_delivery.glb', 'kenney_garbage-truck.glb', 'kenney_truck-flat.glb'], len: 6, weight: 0.06, spd: 0.82 },
  bus:        { files: ['polypizza_bus.glb'], len: 10.5, weight: 0.06, spd: 0.80 },
  ambulance:  { files: ['kenney_ambulance.glb'], len: 5, weight: 0, spd: 1.05 },   // button-only here: a parked organic ambulance would latch a junction's preempt
};

// ── corridor anchors [x=east, z=north], origin = Balkhu Chowk. Long distances compressed. ──
const A = {
  KAL: [-158, 50],   // Kalanki edge (NW): Ring Rd enters heading ~110°
  J1:  [0, 0],       // Balkhu Chowk
  BRW: [17, 5],      // bridge west bank
  BRE: [41, 13],     // bridge east bank
  J2:  [58, 21],     // Kuleshwor / Vayodha junction (north bridgehead)
  C1:  [152, -34],   // Ring Rd shaping point (curve toward SE)
  C2:  [258, -116],
  J3:  [362, -192],  // Ekantakuna Chowk (1.9km compressed to ~430m of road)
  EKA: [474, -302],  // Ekantakuna far edge (SE)
  DAK: [-74, -94],   // Dakshinkali Rd edge (SW of J1) — the truck/bus entry
  VAY: [92, 104],    // University Marg → Vayodha Hospitals (N of J2)
  BAG: [16, 98],     // Bagmati Marg (river east-bank road, N of J2)
  TIK: [380, -308],  // Tikabhairab Rd edge (S of J3)
};

// junctions: box radius, arms (each arm names the neighbour anchor its traffic comes FROM),
// non-conflicting phase groups, and — per through-arm — the downstream approach it feeds
// (the junction-to-junction coordination link).
const JUN = {
  J1: {
    name: 'J1 Balkhu', center: A.J1, boxR: 12,
    arms: { W: { from: 'KAL' }, E: { from: 'BRW' }, SW: { from: 'DAK' } },
    phases: [['W', 'E'], ['SW']],
    down: { W: ['J2', 'W'] },              // Kalanki→east feeds the bridge → J2's bridge approach
  },
  J2: {
    name: 'J2 Kuleshwor', center: A.J2, boxR: 12,
    arms: { W: { from: 'BRE' }, E: { from: 'C1' }, Nu: { from: 'VAY' }, Nb: { from: 'BAG' } },
    phases: [['W', 'E'], ['Nu', 'Nb']],
    down: { W: ['J3', 'W'], E: ['J1', 'E'] },
  },
  J3: {
    name: 'J3 Sanepa', center: A.J3, boxR: 12,
    arms: { W: { from: 'C2' }, E: { from: 'EKA' }, S: { from: 'TIK' } },
    phases: [['W', 'E'], ['S']],
    down: { E: ['J2', 'E'] },
  },
};

// routes: ordered anchor path + the (junction,arm) it obeys at each junction it crosses.
// crossing=true flags a turn that cuts across the opposing through stream (needs gap acceptance).
// lanes = how many parallel lane variants to generate (2 on the Ring, 1 on minor arms).
const ROUTES = {
  KAL_EKA: { seq: ['KAL', 'J1', 'BRW', 'BRE', 'J2', 'C1', 'C2', 'J3', 'EKA'], stops: [['J1', 'W'], ['J2', 'W'], ['J3', 'W']], lanes: 2, w: 3.2 },
  EKA_KAL: { seq: ['EKA', 'J3', 'C2', 'C1', 'J2', 'BRE', 'BRW', 'J1', 'KAL'], stops: [['J3', 'E'], ['J2', 'E'], ['J1', 'E']], lanes: 2, w: 3.0 },
  DAK_KAL: { seq: ['DAK', 'J1', 'KAL'], stops: [['J1', 'SW']], lanes: 1, w: 1.1, heavy: true },
  KAL_DAK: { seq: ['KAL', 'J1', 'DAK'], stops: [['J1', 'W']], lanes: 1, w: 1.0, heavy: true, crossing: 'J1' },
  DAK_EKA: { seq: ['DAK', 'J1', 'BRW', 'BRE', 'J2', 'C1', 'C2', 'J3', 'EKA'], stops: [['J1', 'SW'], ['J2', 'W'], ['J3', 'W']], lanes: 1, w: 1.0 },
  VAY_KAL: { seq: ['VAY', 'J2', 'BRE', 'BRW', 'J1', 'KAL'], stops: [['J2', 'Nu'], ['J1', 'E']], lanes: 1, w: 0.7 },
  KAL_VAY: { seq: ['KAL', 'J1', 'BRW', 'BRE', 'J2', 'VAY'], stops: [['J1', 'W'], ['J2', 'W']], lanes: 1, w: 0.7, crossing: 'J2' },
  BAG_EKA: { seq: ['BAG', 'J2', 'C1', 'C2', 'J3', 'EKA'], stops: [['J2', 'Nb'], ['J3', 'W']], lanes: 1, w: 0.45 },
  EKA_VAY: { seq: ['EKA', 'J3', 'C2', 'C1', 'J2', 'VAY'], stops: [['J3', 'E'], ['J2', 'E']], lanes: 1, w: 0.55, crossing: 'J2' },
  TIK_KAL: { seq: ['TIK', 'J3', 'C2', 'C1', 'J2', 'BRE', 'BRW', 'J1', 'KAL'], stops: [['J3', 'S'], ['J2', 'E'], ['J1', 'E']], lanes: 1, w: 0.6 },
  KAL_TIK: { seq: ['KAL', 'J1', 'BRW', 'BRE', 'J2', 'C1', 'C2', 'J3', 'TIK'], stops: [['J1', 'W'], ['J2', 'W'], ['J3', 'W']], lanes: 1, w: 0.5, crossing: 'J3' },
  AMB: { seq: ['DAK', 'J1', 'BRW', 'BRE', 'J2', 'VAY'], stops: [['J1', 'SW'], ['J2', 'W']], lanes: 1, w: 0, crossing: 'J2' },
};

// which entry edge each route starts from (for spawn scheduling)
const ENTRY_OF = { KAL_EKA: 'KAL', KAL_DAK: 'KAL', KAL_VAY: 'KAL', KAL_TIK: 'KAL',
  EKA_KAL: 'EKA', EKA_VAY: 'EKA', DAK_KAL: 'DAK', DAK_EKA: 'DAK', VAY_KAL: 'VAY',
  BAG_EKA: 'BAG', TIK_KAL: 'TIK' };

// ─────────────────────────── renderer / scene ───────────────────────────
const canvas = document.getElementById('app');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
renderer.setSize(innerWidth, innerHeight);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 0.95;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x9ab4cc);
scene.fog = new THREE.Fog(0x9ab4cc, 340, 720);
scene.environment = new THREE.PMREMGenerator(renderer).fromScene(new RoomEnvironment(), 0.04).texture;

const camera = new THREE.PerspectiveCamera(46, innerWidth / innerHeight, 0.1, 1600);
camera.position.set(310, 230, 160);              // south of the corridor: J1+bridge left, Ekantakuna right
camera.lookAt(170, 0, -90);

const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(170, 0, -90);
controls.enableDamping = true;
controls.maxPolarAngle = Math.PI * 0.49;
controls.maxDistance = 620;

scene.add(new THREE.HemisphereLight(0xcfe0f0, 0x5a604e, 0.55));
const sun = new THREE.DirectionalLight(0xfff2df, 1.35);
sun.position.set(80, 120, 40);
scene.add(sun);

// ─────────────────────────── materials + merge helper ───────────────────────────
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
  t.colorSpace = THREE.SRGBColorSpace;           // canvas pixels are sRGB — without this everything washes out
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(repeat, repeat);
  return t;
}
function windowsTexture(tint) {
  const c = document.createElement('canvas');
  c.width = 128; c.height = 256;
  const g = c.getContext('2d');
  g.fillStyle = tint; g.fillRect(0, 0, 128, 256);
  for (let y = 12; y < 244; y += 26) for (let x = 10; x < 118; x += 24) {
    g.fillStyle = Math.random() < 0.35 ? '#cfd8b8' : '#20262e';
    g.fillRect(x, y, 14, 16);
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}
const MAT = {
  grass:    new THREE.MeshStandardMaterial({ map: noiseTexture([96, 112, 74], 20, 90), roughness: 1 }),
  asphalt:  new THREE.MeshStandardMaterial({ map: noiseTexture([46, 48, 52], 14, 12), roughness: 0.95 }),
  paint:    new THREE.MeshStandardMaterial({ color: 0xf2eee2, roughness: 0.6 }),
  yellow:   new THREE.MeshStandardMaterial({ color: 0xd9b23a, roughness: 0.6 }),
  sidewalk: new THREE.MeshStandardMaterial({ map: noiseTexture([172, 166, 152], 18, 20), roughness: 1 }),
  curb:     new THREE.MeshStandardMaterial({ color: 0x9b968a, roughness: 0.9 }),
  pole:     new THREE.MeshStandardMaterial({ color: 0x2b2f33, roughness: 0.7, metalness: 0.4 }),
  housing:  new THREE.MeshStandardMaterial({ color: 0x14171b, roughness: 0.6 }),
  trunk:    new THREE.MeshStandardMaterial({ color: 0x6b503a, roughness: 1 }),
  leaves:   new THREE.MeshStandardMaterial({ color: 0x4f7a3d, roughness: 1 }),
  water:    new THREE.MeshStandardMaterial({ color: 0x2f6f7a, roughness: 0.35, metalness: 0.15, transparent: true, opacity: 0.92 }),
  bank:     new THREE.MeshStandardMaterial({ color: 0x6f6242, roughness: 1 }),
  deck:     new THREE.MeshStandardMaterial({ color: 0x3a3d42, roughness: 0.9 }),
  rail:     new THREE.MeshStandardMaterial({ color: 0xcdd2d6, roughness: 0.6, metalness: 0.2 }),
  hospital: new THREE.MeshStandardMaterial({ color: 0xf3f4f6, roughness: 0.85 }),
  redcross: new THREE.MeshStandardMaterial({ color: 0xd6352b, roughness: 0.6 }),
};
function mergedBoxes(parts, material) {
  const geos = parts.map(([w, h, d, x, y, z]) => new THREE.BoxGeometry(w, h, d).translate(x, y, z));
  const mesh = new THREE.Mesh(mergeGeometries(geos), material);
  geos.forEach(g => g.dispose());
  scene.add(mesh);
  return mesh;
}

// ─────────────────────────── polyline geometry helpers ───────────────────────────
function crPoint(p0, p1, p2, p3, t) {
  const t2 = t * t, t3 = t2 * t;
  const f = (a, b, c, d) => 0.5 * (2 * b + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t2 + (-a + 3 * b - 3 * c + d) * t3);
  return [f(p0[0], p1[0], p2[0], p3[0]), f(p0[1], p1[1], p2[1], p3[1])];
}
function catmull(points, subdiv = 20) {
  if (points.length < 2) return points.map(p => p.slice());
  const at = i => points[Math.max(0, Math.min(points.length - 1, i))];
  const out = [];
  for (let i = 0; i < points.length - 1; i++)
    for (let t = 0; t < subdiv; t++) out.push(crPoint(at(i - 1), at(i), at(i + 1), at(i + 2), t / subdiv));
  out.push(points[points.length - 1].slice());
  return out;
}
function resample(poly, step) {
  const out = [poly[0].slice()];
  let ax = poly[0][0], az = poly[0][1], acc = 0;
  for (let i = 1; i < poly.length; i++) {
    let bx = poly[i][0], bz = poly[i][1], seg = Math.hypot(bx - ax, bz - az);
    while (acc + seg >= step && seg > 1e-6) {
      const t = (step - acc) / seg;
      ax += (bx - ax) * t; az += (bz - az) * t;
      out.push([ax, az]); acc = 0; seg = Math.hypot(bx - ax, bz - az);
    }
    acc += seg; ax = bx; az = bz;
  }
  const last = poly[poly.length - 1];
  if (Math.hypot(out[out.length - 1][0] - last[0], out[out.length - 1][1] - last[1]) > 0.05) out.push(last.slice());
  return out;
}
function offsetLeft(poly, dist) {
  if (!dist) return poly.map(p => p.slice());
  const n = poly.length, out = [];
  for (let i = 0; i < n; i++) {
    const a = poly[Math.max(0, i - 1)], b = poly[Math.min(n - 1, i + 1)];
    let dx = b[0] - a[0], dz = b[1] - a[1]; const L = Math.hypot(dx, dz) || 1; dx /= L; dz /= L;
    out.push([poly[i][0] - dz * dist, poly[i][1] + dx * dist]);   // left normal of travel dir
  }
  return out;
}
const yawFromDir = (dx, dz) => Math.atan2(-dx, -dz);   // model faces -Z; align it to travel dir

// a flat ribbon (triangle strip) of constant half-width along a polyline, at height y
function ribbonGeo(poly, halfW, y) {
  const n = poly.length, pos = [], uv = [], idx = [];
  for (let i = 0; i < n; i++) {
    const a = poly[Math.max(0, i - 1)], b = poly[Math.min(n - 1, i + 1)];
    let dx = b[0] - a[0], dz = b[1] - a[1]; const L = Math.hypot(dx, dz) || 1; dx /= L; dz /= L;
    const lx = -dz, lz = dx;
    pos.push(poly[i][0] + lx * halfW, y, poly[i][1] + lz * halfW);
    pos.push(poly[i][0] - lx * halfW, y, poly[i][1] - lz * halfW);
    uv.push(0, i * 0.15, 1, i * 0.15);
  }
  for (let i = 0; i < n - 1; i++) { const k = i * 2; idx.push(k, k + 1, k + 2, k + 1, k + 3, k + 2); }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
  g.setIndex(idx); g.computeVertexNormals();
  return g;
}
function dashGeo(cx, cz, ry, wid, len, y) {
  return new THREE.BoxGeometry(wid, 0.03, len)
    .applyMatrix4(new THREE.Matrix4().makeRotationY(ry))
    .applyMatrix4(new THREE.Matrix4().makeTranslation(cx, y, cz));
}
const anchorPoly = names => resample(catmull(names.map(k => A[k])), STEP);

const ROADS = [
  { key: 'ring', anchors: ['KAL', 'J1', 'BRW', 'BRE', 'J2', 'C1', 'C2', 'J3', 'EKA'], half: 7.8, ring: true },
  { key: 'dak', anchors: ['DAK', 'J1'], half: 4.4 },
  { key: 'univ', anchors: ['VAY', 'J2'], half: 4.4 },
  { key: 'bag', anchors: ['BAG', 'J2'], half: 4.4 },
  { key: 'tik', anchors: ['TIK', 'J3'], half: 4.4 },
];
const roadPolys = {};
ROADS.forEach(r => { roadPolys[r.key] = anchorPoly(r.anchors); });

// text-bubble sprite (place / junction labels), same look as main.js's N/S/E/W markers
function label(text, x, z, y, scale, fg = '#7dd3fc') {
  const c = document.createElement('canvas'); c.width = 512; c.height = 128;
  const g = c.getContext('2d');
  g.fillStyle = 'rgba(12,15,19,0.82)';
  const r = 24; g.beginPath();
  g.moveTo(r, 4); g.arcTo(508, 4, 508, 124, r); g.arcTo(508, 124, 4, 124, r);
  g.arcTo(4, 124, 4, 4, r); g.arcTo(4, 4, 508, 4, r); g.fill();
  g.fillStyle = fg; g.font = '700 58px ui-monospace, monospace';
  g.textAlign = 'center'; g.textBaseline = 'middle';
  g.fillText(text, 256, 70);
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), depthTest: false, transparent: true }));
  sp.scale.set(scale, scale / 4, 1); sp.position.set(x, y, z); sp.renderOrder = 5;
  scene.add(sp);
  return sp;
}

function buildWorld() {
  mergedBoxes([[1500, 0.2, 1500, 120, -0.12, -60]], MAT.grass);

  // ── Bagmati River: a blue-green ribbon under the bridge, with mud/green banks ──
  // banks + water sit just ABOVE the ground plane (a flat plane can't carve a channel) but BELOW
  // the road (0.06) and bridge deck (0.2), so the bridge reads as spanning the river
  const riverPoly = resample(catmull([[-14, 128], [8, 72], [22, 32], [30, 9], [40, -26], [74, -84], [128, -150]]), STEP);
  scene.add(new THREE.Mesh(ribbonGeo(riverPoly, 11, -0.02), MAT.bank));
  scene.add(new THREE.Mesh(ribbonGeo(riverPoly, 8.5, 0.01), MAT.water));

  // ── roads (asphalt), sidewalks, kerb lips ── all merged per material
  const asphalt = [], sidewalks = [], kerbs = [];
  for (const r of ROADS) {
    const poly = roadPolys[r.key];
    asphalt.push(ribbonGeo(poly, r.half, 0.06));
    for (const s of [1, -1]) {
      sidewalks.push(ribbonGeo(offsetLeft(poly, s * (r.half + 2.1)), 2.0, 0.14));
      kerbs.push(ribbonGeo(offsetLeft(poly, s * (r.half + 0.25)), 0.28, 0.17));
    }
  }
  scene.add(new THREE.Mesh(mergeGeometries(asphalt), MAT.asphalt));
  scene.add(new THREE.Mesh(mergeGeometries(sidewalks), MAT.sidewalk));
  scene.add(new THREE.Mesh(mergeGeometries(kerbs), MAT.curb));

  // ── Balkhu Bridge: raised deck strip + kerbs + railing posts over the river span ──
  const bridgePoly = resample(catmull([A.J1, A.BRW, A.BRE, A.J2]), STEP)
    .filter(p => p[0] > 12 && p[0] < 46);
  scene.add(new THREE.Mesh(ribbonGeo(bridgePoly, 8.4, 0.2), MAT.deck));
  const railBoxes = [];
  for (const s of [1, -1]) {
    const rail = offsetLeft(bridgePoly, s * 8.1);
    for (let i = 0; i < rail.length; i++) {
      railBoxes.push([0.35, 1.3, 0.35, rail[i][0], 0.65, rail[i][1]]);              // post
      if (i > 0) {                                                                  // top rail segment
        const mx = (rail[i][0] + rail[i - 1][0]) / 2, mz = (rail[i][1] + rail[i - 1][1]) / 2;
        railBoxes.push([0.9, 0.18, 0.9, mx, 1.25, mz]);
      }
    }
  }
  mergedBoxes(railBoxes, MAT.rail);

  // ── lane markings: yellow centre dashes + white lane dividers ──
  const whiteGeos = [], yellowGeos = [];
  for (const r of ROADS) {
    const poly = roadPolys[r.key];
    const put = (offset, arr, wid) => {
      const line = offsetLeft(poly, offset);
      for (let i = 1; i < line.length - 1; i++) {
        if (i % 3 === 2) continue;                                                  // dash gap
        const a = line[i - 1], b = line[i + 1];
        arr.push(dashGeo(line[i][0], line[i][1], yawFromDir(b[0] - a[0], b[1] - a[1]), wid, 1.5, 0.135));
      }
    };
    put(0, yellowGeos, 0.28);                                                        // centreline
    if (r.ring) { put(LANE_W, whiteGeos, 0.16); put(-LANE_W, whiteGeos, 0.16); }     // 2+2 lane dividers
  }
  if (whiteGeos.length) scene.add(new THREE.Mesh(mergeGeometries(whiteGeos), MAT.paint));
  if (yellowGeos.length) scene.add(new THREE.Mesh(mergeGeometries(yellowGeos), MAT.yellow));

  // ── yellow box-junction hatching in each junction box ──
  for (const jid in JUN) {
    const jn = JUN[jid], bars = [], R = jn.boxR;
    for (let i = -R; i <= R; i += 2.2) bars.push([0.16, 0.02, R * 2.4, i, 0.13, 0]);
    const mesh = new THREE.Mesh(mergeGeometries(bars.map(([w, h, d, x, y, z]) =>
      new THREE.BoxGeometry(w, h, d).translate(x, y, z))), MAT.yellow);
    mesh.rotation.y = Math.PI / 4; mesh.scale.set(0.7, 1, 0.7);
    mesh.position.set(jn.center[0], 0, jn.center[1]);
    scene.add(mesh);
  }

  // ── low-poly buildings + trees scattered beyond the sidewalks ──
  // share a handful of window materials rather than one texture per building (draw-call budget)
  const bMats = ['#8a7f6d', '#7d8489', '#93867b', '#877e70'].map(t =>
    new THREE.MeshStandardMaterial({ map: windowsTexture(t), roughness: 0.9 }));
  const trunks = [], crowns = [];
  for (const r of ROADS) {
    const poly = roadPolys[r.key];
    const stride = r.ring ? 7 : 5;
    for (let i = 4; i < poly.length - 3; i += stride) {
      for (const s of [1, -1]) {
        const base = offsetLeft(poly, s * (r.half + 4.5))[i];
        if (Math.random() < 0.55) {
          const h = 8 + Math.random() * 16, w = 8 + Math.random() * 7, d = 8 + Math.random() * 7;
          const off = offsetLeft(poly, s * (r.half + 4.5 + w / 2))[i];
          const b = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), bMats[(Math.random() * bMats.length) | 0]);
          b.position.set(off[0], h / 2, off[1]); scene.add(b);
        } else {
          trunks.push([0.4, 2.4, 0.4, base[0], 1.2, base[1]]);
          crowns.push([2.8, 2.8, 2.8, base[0], 3.7, base[1]]);
        }
      }
    }
  }
  mergedBoxes(trunks, MAT.trunk);
  mergedBoxes(crowns, MAT.leaves);

  // ── Vayodha Hospitals: white block with a red cross, beside the University Marg arm ──
  const hx = 108, hz = 92;
  const hb = new THREE.Mesh(new THREE.BoxGeometry(18, 20, 14), MAT.hospital);
  hb.position.set(hx, 10, hz); scene.add(hb);
  const cross = new THREE.Group();
  cross.add(new THREE.Mesh(new THREE.BoxGeometry(1.4, 5, 0.4), MAT.redcross));
  cross.add(new THREE.Mesh(new THREE.BoxGeometry(5, 1.4, 0.4), MAT.redcross));
  cross.position.set(hx - 9.2, 14, hz); cross.rotation.y = -Math.PI / 2; scene.add(cross);

  // ── place / feature labels ──
  label('Kalanki', A.KAL[0] + 8, A.KAL[1] + 6, 12, 30);
  label('Dakshinkali Rd', A.DAK[0] - 4, A.DAK[1] - 8, 11, 34, '#ffcc00');
  label('Vayodha / Kuleshwor', A.VAY[0] + 6, A.VAY[1] + 10, 13, 40, '#86efac');
  label('Bagmati Marg', A.BAG[0] - 10, A.BAG[1] + 8, 11, 32, '#86efac');
  label('Sanepa', A.EKA[0] + 6, A.EKA[1] + 8, 13, 32);
  label('Dhobighat', A.TIK[0] + 8, A.TIK[1] - 6, 11, 34, '#ffcc00');
  label('Balkhu Bridge', 29, 9, 8, 30, '#cdd2d6');
  label('Bagmati River', 8, 60, 4, 30, '#7fd6e0');

  // ── landmarks straight off the OSM extent the founder pinned ──
  // Balkhu Khola: the small stream hugging Dakshinkali Rd, joining the Bagmati by the bridge
  const kholaPoly = resample(catmull([[-96, -118], [-58, -68], [-26, -26], [-6, 6], [4, 34]]), STEP);
  scene.add(new THREE.Mesh(ribbonGeo(kholaPoly, 4.5, -0.02), MAT.bank));
  scene.add(new THREE.Mesh(ribbonGeo(kholaPoly, 3.2, 0.005), MAT.water));
  label('Balkhu Khola', -52, -66, 4, 26, '#7fd6e0');

  // Balkhu Bus Park: gravel lot beside Dakshinkali Rd (parked buses placed once models load)
  const lot = new THREE.Mesh(new THREE.BoxGeometry(40, 0.08, 26), MAT.sidewalk);
  lot.position.set(-46, 0.1, -34); scene.add(lot);
  label('Balkhu Bus Park', -46, -34, 9, 34, '#ffcc00');

  // TU (Kirtipur side), Shiva Jyoti + Megha hospitals, Sajha petrol pump
  const tu = new THREE.Mesh(new THREE.BoxGeometry(24, 9, 18), MAT.hospital);
  tu.position.set(-124, 4.5, -76); scene.add(tu);
  label('TU · Kirtipur', -124, -76, 12, 30, '#e6d9a8');
  const mkHospital = (x, z, w, h, d, name) => {
    const b = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), MAT.hospital);
    b.position.set(x, h / 2, z); scene.add(b);
    const cr = new THREE.Group();
    cr.add(new THREE.Mesh(new THREE.BoxGeometry(1.2, 4, 0.4), MAT.redcross));
    cr.add(new THREE.Mesh(new THREE.BoxGeometry(4, 1.2, 0.4), MAT.redcross));
    cr.position.set(x, h * 0.7, z + d / 2 + 0.3); scene.add(cr);
    if (name) label(name, x, z + d / 2 + 4, h + 4, 28, '#fda4af');
  };
  mkHospital(66, 78, 12, 12, 10, 'Shiva Jyoti');
  mkHospital(322, -148, 16, 14, 12, 'Megha Hospital');
  // Sajha Petrol Pump: orange canopy on posts, east of the Kuleshwor arm
  const canopy = new THREE.Mesh(new THREE.BoxGeometry(14, 0.7, 10),
    new THREE.MeshStandardMaterial({ color: 0xe07a2f, roughness: 0.7 }));
  canopy.position.set(112, 5.4, 62); scene.add(canopy);
  mergedBoxes([[0.5, 5, 0.5, 107, 2.5, 58], [0.5, 5, 0.5, 117, 2.5, 58],
               [0.5, 5, 0.5, 107, 2.5, 66], [0.5, 5, 0.5, 117, 2.5, 66],
               [6, 1.4, 1.4, 112, 0.7, 62]], MAT.pole);
  label('Sajha Petrol', 112, 70, 10, 26, '#fdba74');

  // Ring Rd bus stops from the map: Balkhu + Sanepa shelters (post + roof + back panel)
  const shelter = (x, z, ry, name) => {
    const g = new THREE.Group();
    const roof = new THREE.Mesh(new THREE.BoxGeometry(7, 0.3, 2.6),
      new THREE.MeshStandardMaterial({ color: 0x3577b0, roughness: 0.6 }));
    roof.position.y = 2.7; g.add(roof);
    const back = new THREE.Mesh(new THREE.BoxGeometry(7, 1.6, 0.15), MAT.rail);
    back.position.set(0, 1.4, -1.2); g.add(back);
    [[-3.2, 1.2], [3.2, 1.2]].forEach(([px, pz]) => {
      const p = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.09, 2.7, 6), MAT.pole);
      p.position.set(px, 1.35, pz); g.add(p);
    });
    g.position.set(x, 0, z); g.rotation.y = ry;
    scene.add(g);
    label(name + ' stop', x, z + 5, 7, 22, '#93c5fd');
  };
  shelter(-46, 26, 0.35, 'Balkhu');
  shelter(300, -128, -0.5, 'Sanepa');
}
buildWorld();

// ─────────────────────────── arms: zebra + stop line + signal heads ───────────────────────────
const BULB = { red: 0xff3b30, yellow: 0xffcc00, green: 0x34c759, off: 0x181b20 };
function makeHead(scale = 1) {
  const g = new THREE.Group(), bulbs = {};
  const back = new THREE.Mesh(new THREE.BoxGeometry(1.5 * scale, 3.5 * scale, 0.1), MAT.housing);
  back.position.z = -0.26 * scale; g.add(back);
  g.add(new THREE.Mesh(new THREE.BoxGeometry(1 * scale, 3 * scale, 0.6 * scale), MAT.housing));
  ['red', 'yellow', 'green'].forEach((c, i) => {
    const m = new THREE.Mesh(new THREE.SphereGeometry(0.34 * scale, 12, 12),
      new THREE.MeshStandardMaterial({ color: BULB.off, emissive: BULB.off }));
    m.position.set(0, (1 - i) * scale, 0.32 * scale); g.add(m); bulbs[c] = m;
  });
  return { g, bulbs };
}

const ARMG = {};                                 // arm geometry cache: outward dir, stop point, road half
const signalHeads = {};                          // signalHeads[jid][arm] = { bulbs }
function buildArms() {
  const zebra = [], stopLines = [];
  for (const jid in JUN) {
    const jn = JUN[jid]; ARMG[jid] = {}; signalHeads[jid] = {};
    for (const arm in jn.arms) {
      const from = A[jn.arms[arm].from];
      let ox = from[0] - jn.center[0], oz = from[1] - jn.center[1];
      const L = Math.hypot(ox, oz) || 1; ox /= L; oz /= L;
      const px = -oz, pz = ox;                             // across-road (perp) unit
      const half = (arm === 'W' || arm === 'E') ? 7.8 : 4.4;
      const stopD = jn.boxR + 3.4, zebD = jn.boxR + 1.9;
      const sx = jn.center[0] + ox * stopD, sz = jn.center[1] + oz * stopD;
      ARMG[jid][arm] = { ox, oz, px, pz, half, stopX: sx, stopZ: sz };

      // stop line (one bar across the incoming half) — sits behind the zebra
      stopLines.push(dashGeo(jn.center[0] + ox * stopD, jn.center[1] + oz * stopD,
        yawFromDir(px, pz), 0.5, half * 2 - 0.6, 0.145));
      // zebra bars (closer to the box than the stop line)
      for (let t = -half + 0.7; t <= half - 0.7; t += 1.3)
        zebra.push(dashGeo(jn.center[0] + ox * zebD + px * t, jn.center[1] + oz * zebD + pz * t,
          yawFromDir(ox, oz), 0.62, 2.4, 0.148));

      // signal: kerb pole + head on the approaching car's left, facing outward at the queue
      const lx = oz, lz = -ox;                             // left of the inbound car
      const poleX = sx + lx * (half + 1.4), poleZ = sz + lz * (half + 1.4);
      const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.14, 0.16, 6, 8), MAT.pole);
      pole.position.set(poleX, 3, poleZ); scene.add(pole);
      const head = makeHead();
      head.g.position.set(poleX, 5.6, poleZ);
      head.g.rotation.y = Math.atan2(ox, oz);              // bulbs face outward toward the queue
      scene.add(head.g);
      signalHeads[jid][arm] = { bulbs: head.bulbs };
    }
  }
  scene.add(new THREE.Mesh(mergeGeometries(zebra), MAT.paint));
  scene.add(new THREE.Mesh(mergeGeometries(stopLines), MAT.paint));
}
buildArms();

function setSignal(jid, arm, state) {
  const b = signalHeads[jid][arm].bulbs;
  for (const c of ['red', 'yellow', 'green']) {
    const on = c === state;
    b[c].material.color.setHex(on ? BULB[c] : BULB.off);
    b[c].material.emissive.setHex(on ? BULB[c] : BULB.off);
    b[c].material.emissiveIntensity = on ? 2.2 : 1;
  }
}

// ─────────────────────────── route library (shared lane polylines) ───────────────────────────
function buildRoute(def, lane) {
  const center = resample(catmull(def.seq.map(k => A[k])), STEP);
  const pts = resample(offsetLeft(center, (0.5 + lane) * LANE_W), STEP);
  const n = pts.length, cum = [0], head = [], vcap = [];
  for (let i = 1; i < n; i++) cum[i] = cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
  for (let i = 0; i < n; i++) {
    const a = pts[Math.max(0, i - 1)], b = pts[Math.min(n - 1, i + 1)];
    head[i] = yawFromDir(b[0] - a[0], b[1] - a[1]);
  }
  const raw = [];
  for (let i = 0; i < n; i++) {
    const a = pts[Math.max(0, i - 1)], b = pts[i], c = pts[Math.min(n - 1, i + 1)];
    const v1x = b[0] - a[0], v1z = b[1] - a[1], v2x = c[0] - b[0], v2z = c[1] - b[1];
    const ang = Math.abs(Math.atan2(v1x * v2z - v1z * v2x, v1x * v2x + v1z * v2z));
    const radius = ang > 1e-3 ? STEP / ang : 1e4;
    raw[i] = Math.max(3.2, Math.min(SPEED, Math.sqrt(3.4 * radius)));
  }
  for (let i = 0; i < n; i++) {                            // brake before the apex: window-min the cap
    let m = raw[i];
    for (let k = -3; k <= 3; k++) m = Math.min(m, raw[Math.max(0, Math.min(n - 1, i + k))]);
    vcap[i] = m;
  }
  // stops along the route, in encounter order
  const stops = [];
  let searchFrom = 0;
  for (const [jid, arm] of def.stops) {
    const c = JUN[jid].center, R = JUN[jid].boxR;
    const d = i => Math.hypot(pts[i][0] - c[0], pts[i][1] - c[1]);
    let stopI = -1, inI = -1, outI = -1;
    for (let i = searchFrom; i < n; i++) {
      if (stopI < 0 && d(i) <= R + 3.2) stopI = i;
      if (stopI >= 0 && inI < 0 && d(i) <= R) inI = i;
      if (inI >= 0) { if (d(i) <= R) outI = i; else break; }
    }
    if (stopI < 0) continue;
    if (inI < 0) inI = stopI;
    if (outI < 0) outI = Math.min(n - 1, inI + 1);
    stops.push({ jid, arm, stopS: cum[stopI], sIn: cum[inI], sOut: cum[outI],
      crossing: def.crossing === jid });
    searchFrom = outI + 1;
  }
  return { pts, cum, head, vcap, total: cum[n - 1], stops, n };
}

const ROUTE_LIB = {};                            // ROUTE_LIB[name] = [route per lane]
for (const name in ROUTES) {
  const def = ROUTES[name];
  ROUTE_LIB[name] = [];
  for (let lane = 0; lane < def.lanes; lane++) ROUTE_LIB[name].push(buildRoute(def, lane));
}

function poseAt(route, s) {
  const { cum, pts, head, n } = route;
  if (s <= 0) return { x: pts[0][0], z: pts[0][1], ry: head[0] };
  if (s >= route.total) return { x: pts[n - 1][0], z: pts[n - 1][1], ry: head[n - 1] };
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (cum[mid] <= s) lo = mid; else hi = mid; }
  const seg = cum[hi] - cum[lo] || 1, t = (s - cum[lo]) / seg;
  return { x: pts[lo][0] + (pts[hi][0] - pts[lo][0]) * t, z: pts[lo][1] + (pts[hi][1] - pts[lo][1]) * t, ry: head[lo] };
}
function vcapAt(route, s) {
  const i = Math.max(0, Math.min(route.n - 1, Math.round(s / STEP)));
  return route.vcap[i];
}

// ─────────────────────────── vehicles ───────────────────────────
const loader = new GLTFLoader();
const pools = {};
let ready = false;

const blobMat = (() => {
  const c = document.createElement('canvas'); c.width = c.height = 64;
  const g = c.getContext('2d');
  const grad = g.createRadialGradient(32, 32, 4, 32, 32, 30);
  grad.addColorStop(0, 'rgba(0,0,0,0.38)'); grad.addColorStop(1, 'rgba(0,0,0,0)');
  g.fillStyle = grad; g.fillRect(0, 0, 64, 64);
  return new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(c), transparent: true, depthWrite: false });
})();

function normalize(root, targetLen, rot = Math.PI) {
  let bb = new THREE.Box3().setFromObject(root), size = new THREE.Vector3();
  bb.getSize(size);
  if (size.x > size.z) root.rotation.y = Math.PI / 2;
  bb = new THREE.Box3().setFromObject(root); bb.getSize(size);
  const ctr = new THREE.Vector3(); bb.getCenter(ctr);
  root.position.set(-ctr.x, -bb.min.y, -ctr.z);
  const pivot = new THREE.Group(); pivot.add(root);
  pivot.scale.setScalar(targetLen / Math.max(size.x, size.z, 0.01));
  pivot.rotation.y = rot;
  return pivot;
}

let riderTemplate = null;
async function loadModels() {
  const load = url => new Promise((res, rej) => loader.load(url, g => res(g.scene), undefined, rej));
  for (const [type, cfg] of Object.entries(TYPES)) {
    pools[type] = [];
    for (const entry of cfg.files) {
      const [f, rot] = Array.isArray(entry) ? entry : [entry, cfg.rot];
      try { pools[type].push(normalize(await load('assets/models/' + f), cfg.len, rot)); }
      catch { console.warn('model failed to load:', f); }
    }
  }
  try {
    const man = await load('assets/models/polypizza_pedestrian_man.glb');
    const bb = new THREE.Box3().setFromObject(man), size = new THREE.Vector3();
    bb.getSize(size);
    man.scale.setScalar(1.55 / Math.max(size.y, 0.01));
    const ctr = new THREE.Vector3(); bb.getCenter(ctr);
    man.position.set(-ctr.x * man.scale.x, 0, -ctr.z * man.scale.z);
    riderTemplate = man;
  } catch { console.warn('rider model missing — bikes ride themselves'); }
  ready = Object.values(pools).some(p => p.length);
}

function pickType() {
  let r = Math.random(), sum = 0;
  for (const t in TYPES) { sum += TYPES[t].weight; if (r <= sum) return t; }
  return 'car';
}

const cars = [];
window.__relay = { cars, camera, controls };
function placeCar(c) {
  const p = poseAt(c.route, c.s);
  c.mesh.position.set(p.x, 0, p.z);
  c.mesh.rotation.y = p.ry;
}
function addCar(routeName, { type = null, lane = null, slack = false, at = 0 } = {}) {
  if (!ready || cars.length >= MAX_CARS + 8 || (cars.length >= MAX_CARS && !slack)) return;
  const variants = ROUTE_LIB[routeName];
  if (!variants || !variants.length) return;
  lane = lane != null ? Math.min(lane, variants.length - 1) : (Math.random() * variants.length) | 0;
  const route = variants[lane];
  type = type || pickType();
  const pool = pools[type];
  if (!pool || !pool.length) return;
  const T = TYPES[type];
  const s0 = Math.max(0, Math.min(route.total - 6, at * route.total));
  // spawn clearance: never drop a car on top of one already sitting on the same lane polyline
  const head = poseAt(route, s0);
  for (const o of cars) {
    if (Math.hypot(o.mesh.position.x - head.x, o.mesh.position.z - head.z) < (T.len + o.len) / 2 + GAP) return;
  }
  const mesh = new THREE.Group();
  mesh.add(pool[(Math.random() * pool.length) | 0].clone(true));
  if (T.rider && riderTemplate) {
    const rider = cloneSkinned(riderTemplate);
    rider.position.y = 0.55;
    rider.traverse(o => { o.frustumCulled = false; });
    mesh.add(rider);
  }
  const blob = new THREE.Mesh(new THREE.PlaneGeometry(T.len * 0.9, T.len * 1.25), blobMat);
  blob.rotation.x = -Math.PI / 2; blob.position.y = 0.02;
  mesh.add(blob);
  const c = { route, routeName, lane, s: s0, mesh, len: T.len, type,
    speed: SPEED * (T.spd || 1) * (0.85 + Math.random() * 0.3), vel: 0, stuck: 0, blocked: false };
  placeCar(c);
  scene.add(mesh);
  cars.push(c);
}

// hard separation: every vehicle is three discs along its heading; two bodies may NEVER overlap.
const discRad = c => c.type === 'motorcycle' ? 0.55 : Math.max(0.9, c.len / 6);
function discs(x, z, ry, len) {
  const hx = -Math.sin(ry), hz = -Math.cos(ry), s = len / 3;
  return [[x - hx * s, z - hz * s], [x, z], [x + hx * s, z + hz * s]];
}
function minGapAt(c, s) {
  const p = poseAt(c.route, s), mine = discs(p.x, p.z, p.ry, c.len), rc = discRad(c);
  let gap = Infinity;
  for (const o of cars) {
    if (o === c) continue;
    const q = o.mesh.position, dx = q.x - p.x, dz = q.z - p.z;
    const reach = (c.len + o.len) * 0.7 + 2;
    if (dx * dx + dz * dz > reach * reach) continue;
    const theirs = discs(q.x, q.z, o.mesh.rotation.y, o.len), ro = discRad(o);
    for (const [ax, az] of mine) for (const [bx, bz] of theirs)
      gap = Math.min(gap, Math.hypot(ax - bx, az - bz) - rc - ro);
  }
  return gap;
}
// surface distance to the nearest car ahead within a lane-wide corridor (smooth car-following)
function leaderGap(c) {
  const p = c.mesh.position, ry = c.mesh.rotation.y;
  const hx = -Math.sin(ry), hz = -Math.cos(ry);
  let best = Infinity;
  for (const o of cars) {
    if (o === c) continue;
    const dx = o.mesh.position.x - p.x, dz = o.mesh.position.z - p.z;
    const ahead = dx * hx + dz * hz, beside = Math.abs(dx * hz - dz * hx);
    if (ahead <= 0 || ahead > 60 || beside >= 2.0) continue;
    // only SAME-heading traffic is a leader — a crossing car here froze vMax to 0 and the whole
    // corridor gridlocked behind it; crossing/oncoming bodies are the no-touch check's job
    if (Math.cos(o.mesh.rotation.y - ry) < 0.5) continue;
    best = Math.min(best, ahead - (c.len + o.len) / 2);
  }
  return best;
}

// next stop line the car still has to obey (the one it hasn't crossed into the box of yet)
function nextApproach(c) {
  for (const st of c.route.stops) if (c.s < st.stopS) return st;
  return null;
}
const BOX_CAP = 6;                                // "do not block the junction": box admission ceiling
const boxCount = {};                              // vehicles currently inside each junction box
let phaseGreen = {};                              // phaseGreen[jid] = Set of arms currently green
let maxStuck = 0;

function moveCars(dt) {
  for (const jid in JUN) {
    const c = JUN[jid].center;
    boxCount[jid] = cars.filter(v => Math.hypot(v.mesh.position.x - c[0], v.mesh.position.z - c[1]) < JUN[jid].boxR).length;
  }
  maxStuck = Math.max(0, ...cars.map(c => c.stuck || 0));

  for (const c of cars) {
    const before = c.s;
    let target = c.s + c.speed * dt;
    let stopDist = Infinity;

    const st = nextApproach(c);
    if (st) {
      const green = phaseGreen[st.jid] && phaseGreen[st.jid].has(st.arm);
      const boxFull = boxCount[st.jid] >= BOX_CAP;
      let hold = !green || boxFull;
      // crossing turn: even on green, yield until the box clears of fast conflicting traffic
      // (the ambulance has right of way and is given an exclusive-green path, so it never yields)
      if (!hold && st.crossing && c.type !== 'ambulance') {
        const jc = JUN[st.jid].center;
        for (const o of cars) {
          if (o === c) continue;
          if (Math.hypot(o.mesh.position.x - jc[0], o.mesh.position.z - jc[1]) < JUN[st.jid].boxR && (o.vel || 0) > 1.5) { hold = true; break; }
        }
      }
      if (hold) {
        const line = st.stopS - c.len / 2;
        if (c.s <= line + 0.02) { target = Math.min(target, line); stopDist = Math.min(stopDist, line - c.s); }
      }
    }

    const lg = leaderGap(c);
    if (lg < Infinity) { const wall = lg - GAP; target = Math.min(target, c.s + Math.max(0, wall)); stopDist = Math.min(stopDist, wall); }

    // driving envelope (ported): v² = 2·a·d braking toward the CONSTRAINT, corner cap by lateral-g
    const allowed = Math.max(0, target - c.s);
    let vMax = Math.min(c.speed, Math.sqrt(2 * 5.5 * Math.max(0, stopDist)));
    vMax = Math.min(vMax, vcapAt(c.route, c.s));
    const acc = c.type === 'bus' || c.type === 'truck' ? 3.5 : 6.5;
    c.vel = (c.vel ?? 0) < vMax ? Math.min(vMax, (c.vel ?? 0) + acc * dt) : Math.max(vMax, c.vel - 8 * dt);
    let adv = Math.min(allowed, c.vel * dt);

    if (adv > 1e-4) {
      const now = minGapAt(c, c.s);
      const next = minGapAt(c, c.s + adv);
      // squeeze winner is LOCAL: one per knot — a global max-stuck token let a single un-squeezable
      // car far away starve every other knot of its escape and gridlock the whole corridor
      const wedged = (c.stuck || 0) > 4 && !cars.some(o => o !== c && (o.stuck || 0) > (c.stuck || 0) &&
        Math.hypot(o.mesh.position.x - c.mesh.position.x, o.mesh.position.z - c.mesh.position.z) < 25);
      const inching = (c.stuck || 0) > 1.5;
      if (wedged || inching) adv = Math.min(adv, c.speed * 0.25 * dt);
      const blockedNow = wedged ? next < 0.02
                       : inching ? next < 0.06 && next < now
                       : next < SEP && next < now;
      if (blockedNow) { adv = 0; c.vel = 0; c.stuck = (c.stuck || 0) + dt; }
      // a centimetre inch is not freedom — resetting on it trapped cars oscillating at the
      // inching threshold forever; only a clean advance clears the stuck clock
      else if (adv < 0.3 * c.speed * dt) c.stuck = (c.stuck || 0) + dt;
      else c.stuck = 0;
    } else {
      // held (red / leader / queue) is NOT a wedge; only a genuine no-touch conflict counts as stuck,
      // otherwise a light-waiting car would be trapped in the quarter-speed inching mode after green
      c.stuck = minGapAt(c, c.s) < SEP ? (c.stuck || 0) + dt : 0;
    }
    c.s += adv;
    c.blocked = (c.s - before) < 0.25 * c.speed * dt;
    placeCar(c);
  }

  for (let i = cars.length - 1; i >= 0; i--) {
    const c = cars[i];
    const inBox = Object.keys(JUN).some(jid =>
      Math.hypot(c.mesh.position.x - JUN[jid].center[0], c.mesh.position.z - JUN[jid].center[1]) < JUN[jid].boxR);
    const towed = ((c.stuck || 0) > 30 && inBox) || (c.stuck || 0) > 60;   // tow truck: no wedge lives forever
    if (c.s >= c.route.total - 0.3 || towed) {
      c.mesh.traverse(o => { if (o.geometry && o.geometry.type === 'PlaneGeometry') o.geometry.dispose(); });
      scene.remove(c.mesh);
      cars.splice(i, 1);
    }
  }
}

// ─────────────────────────── decentralized signal control ───────────────────────────
let simTime = 0, systemOn = true;
const MIN_GREEN = 5, YELLOW = 2, ALLRED = 1, MAX_GREEN = 42, MAX_WAIT = 45, FIX_GREEN = 20, PREEMPT_HOLD = 2.5, MAX_PREEMPT = 25;

// PCU-weighted queue on each approach + which approaches have an ambulance closing in
function computeQueues() {
  const Q = {}, amb = {};
  for (const jid in JUN) { Q[jid] = {}; amb[jid] = {}; for (const arm in JUN[jid].arms) { Q[jid][arm] = 0; amb[jid][arm] = false; } }
  for (const c of cars) {
    const st = nextApproach(c);
    if (!st) continue;
    const d = st.stopS - c.s;
    if (c.type === 'ambulance' && d < DETECT + 18) amb[st.jid][st.arm] = true;
    if (d < DETECT && (c.vel || 0) < 2.6) Q[st.jid][st.arm] += PCU[c.type] || 1;
  }
  return { Q, amb };
}

const PHASE_NAME = {                              // friendly label per junction phase index
  J1: ['Ring W⇄E', 'Dakshinkali'], J2: ['Ring W⇄E', 'Kuleshwor / Vayodha'], J3: ['Ring W⇄E', 'Dhobighat'],
};
class Controller {
  constructor(jid) {
    this.jid = jid; this.J = JUN[jid];
    this.pi = 0; this.pending = 0; this.sub = 'green'; this.t = 0; this.ge = 0;
    this.wait = Object.fromEntries(Object.keys(this.J.arms).map(a => [a, 0]));
    this.preArm = null; this.preUntil = 0;
  }
  phaseOf(arm) { return this.J.phases.findIndex(p => p.includes(arm)); }
  pressure(pi, allQ) {
    let s = 0;
    for (const arm of this.J.phases[pi]) {
      const dn = this.J.down[arm];
      const dq = dn ? (allQ[dn[0]][dn[1]] || 0) : 0;         // neighbour's queue on the exit link = coordination
      s += (allQ[this.jid][arm] || 0) - 0.4 * dq;
    }
    return s;
  }
  startYellow(nextP) { this.pending = nextP; this.sub = 'yellow'; this.t = 0; }
  update(dt, allQ, amb) {
    const Q = allQ[this.jid], cur = this.J.phases[this.pi];
    for (const arm in this.J.arms) {
      const green = this.sub === 'green' && cur.includes(arm);
      if (Q[arm] > 0.1 && !green) this.wait[arm] += dt; else if (green) this.wait[arm] = 0;
    }
    if (!systemOn) { this.updateFixed(dt); this.apply(); return; }

    let seen = null;
    for (const arm in amb[this.jid]) if (amb[this.jid][arm]) { seen = arm; break; }
    if (seen) {
      this.preArm = seen;
      this.preUntil = simTime + PREEMPT_HOLD;
      this.preStart ??= simTime;                   // continuous-hold start, for the hard bound below
    } else if (simTime >= this.preUntil) {
      this.preStart = null;
    }
    // MAX_PREEMPT: a stalled/parked ambulance must never own the junction forever
    const preempt = simTime < this.preUntil && simTime - (this.preStart ?? simTime) < MAX_PREEMPT ? this.preArm : null;

    if (this.sub === 'green') {
      this.ge += dt;
      if (preempt != null) {
        const want = this.phaseOf(preempt);
        if (want !== this.pi && this.ge >= 2) this.startYellow(want);
      } else if (this.ge >= MIN_GREEN) {
        const served = cur.every(a => Q[a] <= 0.1);
        let forced = -1, worst = MAX_WAIT;
        for (const arm in this.wait) if (this.wait[arm] > worst) { worst = this.wait[arm]; forced = this.phaseOf(arm); }
        let best = this.pi, bp = -Infinity;
        for (let p = 0; p < this.J.phases.length; p++) { const pr = this.pressure(p, allQ); if (pr > bp) { bp = pr; best = p; } }
        let nextP = this.pi;
        if (forced >= 0) nextP = forced;
        else if (served || this.ge >= MAX_GREEN) nextP = best;
        else if (best !== this.pi && this.pressure(best, allQ) > this.pressure(this.pi, allQ) + 1.5 && this.ge >= MIN_GREEN + 3) nextP = best;
        if (nextP !== this.pi) this.startYellow(nextP);
        else if (served) this.ge = MIN_GREEN;                // idle: don't burn toward max-green on an empty phase
      }
    } else if (this.sub === 'yellow') {
      if ((this.t += dt) >= YELLOW) { this.sub = 'allred'; this.t = 0; }
    } else {
      if ((this.t += dt) >= ALLRED) { this.pi = this.pending; this.sub = 'green'; this.t = 0; this.ge = 0; }
    }
    this.apply();
  }
  updateFixed(dt) {                                // blind 20s cycle — no sensing, no preempt
    this.t += dt;
    if (this.sub === 'green') { if (this.t >= FIX_GREEN) { this.pending = (this.pi + 1) % this.J.phases.length; this.sub = 'yellow'; this.t = 0; } }
    else if (this.sub === 'yellow') { if (this.t >= YELLOW) { this.sub = 'allred'; this.t = 0; } }
    else if (this.t >= ALLRED) { this.pi = this.pending; this.sub = 'green'; this.t = 0; }
  }
  apply() {
    const green = new Set(), cur = this.J.phases[this.pi];
    // during an ambulance preempt, give ONLY the ambulance's approach green (its phase-mates stay
    // red) — an exclusive, conflict-free path across the box
    const emerg = simTime < this.preUntil && cur.includes(this.preArm);
    for (const arm in this.J.arms) {
      let state = 'red';
      if (this.sub === 'green' && cur.includes(arm) && (!emerg || arm === this.preArm)) { state = 'green'; green.add(arm); }
      else if (this.sub === 'yellow' && cur.includes(arm)) state = 'yellow';
      setSignal(this.jid, arm, state);
    }
    phaseGreen[this.jid] = green;
  }
  chip() {
    const nm = PHASE_NAME[this.jid][this.pi] || this.J.phases[this.pi].join('+');
    return this.sub === 'green' ? nm : this.sub === 'yellow' ? nm + ' ⚠' : 'all-red';
  }
  get emergency() { return simTime < this.preUntil; }
}
const controllers = Object.fromEntries(Object.keys(JUN).map(jid => [jid, new Controller(jid)]));
Object.assign(window.__relay, { controllers, pg: () => phaseGreen, box: () => boxCount });
function tickSignals(dt) {
  const { Q, amb } = computeQueues();
  for (const jid in controllers) controllers[jid].update(dt, Q, amb);
}

// ─────────────────────────── spawns ───────────────────────────
const ENTRY_RATE = { KAL: 1.5, EKA: 1.35, DAK: 0.8, VAY: 0.5, BAG: 0.34, TIK: 0.5 };
const ROUTES_BY_ENTRY = {};
for (const name in ENTRY_OF) (ROUTES_BY_ENTRY[ENTRY_OF[name]] ||= []).push(name);
const nextSpawn = Object.fromEntries(Object.keys(ENTRY_RATE).map(e => [e, Math.random() * 2]));

function pickRoute(entry) {
  const names = ROUTES_BY_ENTRY[entry];
  let tot = names.reduce((s, n) => s + ROUTES[n].w, 0), r = Math.random() * tot;
  for (const n of names) { r -= ROUTES[n].w; if (r <= 0) return n; }
  return names[0];
}
function spawnTick() {
  for (const entry in ENTRY_RATE) {
    if (simTime < nextSpawn[entry]) continue;
    const name = pickRoute(entry);
    // Balkhu is the truck/bus entry: bias heavy vehicles onto the Dakshinkali↔Kalanki moves
    const type = ROUTES[name].heavy && Math.random() < 0.45 ? (Math.random() < 0.6 ? 'truck' : 'bus') : null;
    addCar(name, { type });
    nextSpawn[entry] = simTime + (0.5 + Math.random() * 1.7) / (ENTRY_RATE[entry] || 1);
  }
}
function spawnAmbulance() { addCar('AMB', { type: 'ambulance', slack: true }); }

// corridor-wide instantaneous queue (vehicles stopped/slow at any approach)
function queuedNow() {
  let n = 0;
  for (const c of cars) {
    const st = nextApproach(c);
    if (st && st.stopS - c.s < DETECT && (c.vel || 0) < 2.6 && c.blocked) n++;
  }
  return n;
}

// ─────────────────────────── HUD (control-room chrome) ───────────────────────────
const PR = Math.min(devicePixelRatio, 1.5);
const qHist = [];                                // {v,on} queued samples, ~90s of history
let statLine, subLine, miniChart, mctx, banner, chipEls = {};
let qSampleAcc = 0;

function button(label, onClick, id) {
  const b = document.createElement('button');
  b.textContent = label; b.className = 'relay-btn';
  if (id) b.id = id;
  b.onclick = onClick;
  return b;
}
function injectStyles() {
  const s = document.createElement('style');
  s.textContent = `
  :root{ --bg:#0b0d10; --panel:rgba(16,19,24,.72); --line:rgba(255,255,255,.09); --line2:rgba(255,255,255,.16);
    --txt:#e8eaed; --muted:#9aa0a6; --cy:#7dd3fc; --grn:#86efac; --red:#ff6b62; --amb:#ffcc00; }
  .relay-btn{ font:600 12px/1 ui-monospace,"SF Mono",Menlo,monospace; color:var(--txt);
    background:rgba(28,33,41,.9); border:1px solid var(--line2); border-radius:7px;
    padding:7px 10px; cursor:pointer; transition:background .14s,border-color .14s,transform .06s; }
  .relay-btn:hover{ background:rgba(42,49,60,.95); border-color:rgba(125,211,252,.5); }
  .relay-btn:active{ transform:translateY(1px); }
  .glass{ background:var(--panel); backdrop-filter:blur(9px); -webkit-backdrop-filter:blur(9px);
    border:1px solid var(--line); border-radius:11px; box-shadow:0 6px 22px rgba(0,0,0,.35); }
  .relay-headline{ position:fixed; top:14px; left:50%; transform:translateX(-50%); z-index:11;
    max-width:min(720px,94vw); text-align:center; color:var(--txt); padding:9px 16px;
    font:600 12.5px ui-monospace,monospace; line-height:1.45; }
  .relay-headline b{ color:var(--cy); }
  .relay-chips{ position:fixed; left:14px; top:66px; z-index:11; display:flex; flex-direction:column; gap:8px; }
  .relay-chip{ min-width:196px; padding:9px 12px; color:var(--txt); font:12px ui-monospace,monospace; }
  .relay-chip .cn{ color:var(--cy); font-weight:700; font-size:10.5px; letter-spacing:.07em; text-transform:uppercase; }
  .relay-chip .cp{ font-size:13px; font-weight:700; margin-top:4px; color:var(--grn); }
  .relay-panel{ position:fixed; right:14px; bottom:14px; z-index:10; width:262px; padding:12px 13px;
    font:12px ui-monospace,Menlo,monospace; color:var(--txt); }
  .rp-title{ color:var(--cy); letter-spacing:.08em; font-weight:700; font-size:11px; text-transform:uppercase;
    display:flex; align-items:center; gap:7px; margin-bottom:9px; }
  .rp-title::before{ content:""; width:7px; height:7px; border-radius:50%; background:var(--grn);
    box-shadow:0 0 8px var(--grn); animation:relay-pulse 1.8s ease-in-out infinite; }
  @keyframes relay-pulse{ 0%,100%{opacity:1} 50%{opacity:.35} }
  .rp-stat{ font-size:26px; font-weight:700; color:var(--grn); line-height:1.1; font-variant-numeric:tabular-nums; transition:color .2s; }
  .rp-sub{ color:var(--muted); font-size:11.5px; margin-top:3px; margin-bottom:11px; }
  .rp-cap{ color:var(--muted); font-size:10px; letter-spacing:.05em; text-transform:uppercase; margin-bottom:6px; }
  #mini{ width:256px; height:88px; display:block; }
  .rp-legend{ color:var(--muted); font-size:11px; margin-top:8px; display:flex; align-items:center; gap:6px; }
  .rp-legend .sw{ width:10px; height:10px; border-radius:2px; display:inline-block; }
  .rp-legend .sw.on{ background:var(--grn); } .rp-legend .sw.off{ background:var(--red); }
  #sys-toggle{ position:fixed; bottom:16px; left:50%; transform:translateX(-50%); z-index:12;
    font:700 14px ui-monospace,monospace; border:none; border-radius:11px; padding:13px 24px; cursor:pointer;
    box-shadow:0 8px 26px rgba(0,0,0,.42); transition:background .16s,color .16s,transform .06s; letter-spacing:.02em; }
  #sys-toggle:active{ transform:translateX(-50%) translateY(1px); }
  .relay-scenario{ position:fixed; top:14px; right:14px; z-index:11; display:flex; gap:6px; flex-wrap:wrap;
    max-width:270px; justify-content:flex-end; }
  .relay-banner{ position:fixed; top:96px; left:50%; transform:translateX(-50%); z-index:13; display:none;
    font:700 13px ui-monospace,monospace; color:#fff; background:rgba(255,59,48,.94);
    padding:9px 18px; border-radius:10px; border:1px solid rgba(255,255,255,.25);
    animation:relay-flash 1s steps(1,end) infinite; }
  @keyframes relay-flash{ 0%,100%{box-shadow:0 6px 24px rgba(255,59,48,.55)} 50%{box-shadow:0 4px 10px rgba(255,59,48,.2)} }
  `;
  document.head.appendChild(s);
}
// queued-over-time chart: one series, each segment coloured by the mode that produced it
function drawChart() {
  if (!mctx) return;
  const cw = 256, ch = 88, x0 = 22, x1 = cw - 4, y0 = 7, y1 = ch - 13;
  mctx.clearRect(0, 0, cw, ch);
  const mx = Math.max(4, ...qHist.map(s => s.v));
  mctx.font = '9px ui-monospace, monospace';
  mctx.strokeStyle = 'rgba(255,255,255,.09)'; mctx.lineWidth = 1;
  mctx.fillStyle = '#6b7178'; mctx.textBaseline = 'middle'; mctx.textAlign = 'right';
  for (const f of [0, 0.5, 1]) {
    const y = y1 - f * (y1 - y0);
    mctx.beginPath(); mctx.moveTo(x0, y); mctx.lineTo(x1, y); mctx.stroke();
    mctx.fillText(Math.round(f * mx), x0 - 4, y);
  }
  mctx.textBaseline = 'alphabetic'; mctx.textAlign = 'left'; mctx.fillText('-90s', x0, ch - 2);
  mctx.textAlign = 'right'; mctx.fillText('now', x1, ch - 2);
  if (qHist.length < 2) return;
  const px = i => x0 + (i / (qHist.length - 1)) * (x1 - x0);
  const py = v => y1 - (v / mx) * (y1 - y0);
  mctx.lineWidth = 2; mctx.lineJoin = 'round'; mctx.lineCap = 'round';
  for (let i = 1; i < qHist.length; i++) {
    mctx.strokeStyle = qHist[i].on ? '#86efac' : '#ff6b62';
    mctx.beginPath(); mctx.moveTo(px(i - 1), py(qHist[i - 1].v)); mctx.lineTo(px(i), py(qHist[i].v)); mctx.stroke();
  }
  const last = qHist[qHist.length - 1];
  mctx.fillStyle = last.on ? '#86efac' : '#ff6b62';
  mctx.beginPath(); mctx.arc(px(qHist.length - 1), py(last.v), 2.6, 0, Math.PI * 2); mctx.fill();
}

function buildHUD() {
  injectStyles();
  const hl = document.createElement('div');
  hl.className = 'relay-headline glass';
  hl.innerHTML = '<b>J1 Balkhu · J2 Kuleshwor · J3 Sanepa</b> — each junction decides locally, neighbours share queue state';
  document.body.appendChild(hl);

  const chips = document.createElement('div');
  chips.className = 'relay-chips';
  for (const jid in JUN) {
    const el = document.createElement('div');
    el.className = 'relay-chip glass';
    el.innerHTML = `<div class="cn">${JUN[jid].name}</div><div class="cp" id="chip-${jid}">—</div>`;
    chips.appendChild(el);
    chipEls[jid] = el.querySelector('#chip-' + jid);
  }
  document.body.appendChild(chips);

  const panel = document.createElement('div');
  panel.className = 'relay-panel glass';
  panel.innerHTML =
    '<div class="rp-title">R.E.L.A.Y · corridor</div>' +
    '<div id="m-stat" class="rp-stat">0</div>' +
    '<div id="m-sub" class="rp-sub">vehicles queued now</div>' +
    '<div class="rp-cap">vehicles queued · last 90s</div>' +
    '<canvas id="mini"></canvas>' +
    '<div class="rp-legend"><span class="sw on"></span>R.E.L.A.Y. on&nbsp;&nbsp;<span class="sw off"></span>fixed timer</div>';
  document.body.appendChild(panel);
  statLine = panel.querySelector('#m-stat');
  subLine = panel.querySelector('#m-sub');
  miniChart = panel.querySelector('#mini');
  miniChart.width = 256 * PR; miniChart.height = 88 * PR;
  mctx = miniChart.getContext('2d'); mctx.scale(PR, PR);
  drawChart();

  const sp = document.createElement('div');
  sp.className = 'relay-scenario';
  sp.appendChild(button('🚑 Ambulance → Vayodha', spawnAmbulance));
  sp.appendChild(button('surge Kalanki', () => { for (let i = 0; i < 8; i++) setTimeout(() => addCar('KAL_EKA', {}), i * 140); }));
  sp.appendChild(button('surge Sanepa', () => { for (let i = 0; i < 8; i++) setTimeout(() => addCar('EKA_KAL', {}), i * 140); }));
  document.body.appendChild(sp);

  banner = document.createElement('div');
  banner.className = 'relay-banner';
  document.body.appendChild(banner);

  const toggle = button('', () => { systemOn = !systemOn; paintToggle(); }, 'sys-toggle');
  const paintToggle = () => {
    toggle.textContent = systemOn ? '●  R.E.L.A.Y. ON — click for fixed timer' : '○  FIXED TIMER — click to switch R.E.L.A.Y. on';
    toggle.style.background = systemOn ? 'var(--grn)' : 'var(--red)';
    toggle.style.color = systemOn ? '#0b0d10' : '#fff';
  };
  paintToggle();
  document.body.appendChild(toggle);
}

// ─────────────────────────── main loop ───────────────────────────
const clock = new THREE.Clock();
function tick() {
  const dt = Math.min(clock.getDelta(), 0.05);
  simTime += dt;
  tickSignals(dt);
  moveCars(dt);
  spawnTick();

  for (const jid in JUN) if (chipEls[jid]) chipEls[jid].textContent = controllers[jid].chip();
  if (statLine) {
    const q = queuedNow();
    statLine.textContent = q;
    statLine.style.color = systemOn ? 'var(--grn)' : 'var(--red)';
    subLine.textContent = `vehicles queued now · ${cars.length} in corridor`;
  }
  const emerg = Object.keys(JUN).filter(j => controllers[j].emergency);
  if (banner) {
    banner.style.display = emerg.length ? 'block' : 'none';
    if (emerg.length) banner.textContent = '🚑 EMERGENCY PREEMPT — green wave through ' + emerg.join(' → ');
  }
  if (mctx) {
    qSampleAcc += dt;
    if (qSampleAcc >= 0.75) { qSampleAcc = 0; qHist.push({ v: queuedNow(), on: systemOn }); if (qHist.length > 120) qHist.shift(); drawChart(); }
  }

  controls.update();
  renderer.render(scene, camera);

  window.RELAY = {
    mode: systemOn ? 'relay' : 'fixed', queued: queuedNow(), cars: cars.length,
    phases: Object.fromEntries(Object.keys(JUN).map(j => [j, controllers[j].chip()])),
  };
  requestAnimationFrame(tick);
}

// ─────────────────────────── startup (visible .catch — never hang on "loading") ───────────────────────────
// static street life: parked buses in the bus park, cargo trucks on the Dakshinkali shoulder
// (the documented Balkhu congestion cause), bikes crowding the chowk sidewalk. Scenery, not agents.
function placeParked() {
  const park = (type, x, z, ry) => {
    const pool = pools[type];
    if (!pool || !pool.length) return;
    const m = pool[(Math.random() * pool.length) | 0].clone(true);
    m.position.set(x, 0, z); m.rotation.y = ry + (Math.random() - 0.5) * 0.12;
    scene.add(m);
  };
  for (let i = 0; i < 6; i++)                                        // bus park: two rows of three
    park('bus', -58 + (i % 3) * 13, -40 + ((i / 3) | 0) * 12, Math.PI / 2);
  for (let i = 0; i < 5; i++)                                        // cargo trucks, Dakshinkali shoulder
    park('truck', -20 - i * 11, -32 - i * 12, Math.atan2(-74, -94) + Math.PI / 2);
  for (let i = 0; i < 10; i++)                                       // bike cluster by the chowk
    park('motorcycle', -18 + (i % 5) * 1.6, 16 + ((i / 5) | 0) * 2.6, Math.PI * 0.5 + Math.random() * 0.5);
}

loadModels().then(() => {
  placeParked();
  buildHUD();
  const entries = Object.keys(ENTRY_RATE);
  for (let i = 0; i < 40; i++) {                   // seed traffic spread along the corridor
    const e = entries[(Math.random() * entries.length) | 0];
    addCar(pickRoute(e), { at: 0.1 + Math.random() * 0.85 });
  }
  document.getElementById('loading')?.remove();
  clock.start();
  tick();
}).catch(e => {
  console.error('startup failed:', e);
  const el = document.getElementById('loading');
  if (el) el.textContent = '✗ startup error: ' + (e && e.message || e);
});

addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});
