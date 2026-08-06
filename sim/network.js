// R.E.L.A.Y. — Network.
//
// One straight arterial with THREE signalized junctions at short spacing:
//   Jct 1 (T, stem south) → Jct 2 (4-way, stems N+S) → Jct 3 (T, stem north).
// The two T stems sit on opposite sides. Generic city look — no place names.
//
// Each junction runs its OWN decentralized max-pressure controller; neighbours share downstream
// queue state (a −0.4× coupling term) so a backed-up junction stops feeding its neighbour. Toggle
// R.E.L.A.Y. OFF to fall back to blind fixed 20s timers at every junction and watch the queues grow.
//
// Movement is a waypoint-graph (lane polylines). The physics envelope, blob shadows, rider clones,
// 3-disc no-touch separation, stuck-escalation, local knot-squeeze, and the ambulance green-wave are
// ported verbatim from the solved corridor build — do not re-derive them. Built for 60fps: merged
// static geometry, no shadow maps.
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
const MAX_CARS = 24;
const DETECT = 45;              // queue detection zone before a stop line (m)

// PCU-ish demand weights: a bike is cheap, a bus is heavy
const PCU = { motorcycle: 0.4, car: 1, taxi: 1, truck: 2, bus: 2.5, ambulance: 1 };   // local demo weights — intentionally NOT controller.py's PCU table

// vehicle mix — motorcycle-heavy generic city, same TYPES table as the main sim
const TYPES = {
  motorcycle: { files: [['polypizza_motorcycle.glb', 0], ['polypizza_scooter.glb', Math.PI]], len: 2.1, weight: 0.46, spd: 1.15, rider: true },
  car:        { files: ['kenney_sedan.glb', 'kenney_sedan-sports.glb', 'kenney_hatchback-sports.glb', 'kenney_suv.glb', 'kenney_suv-luxury.glb', 'kenney_van.glb'], len: 4.2, weight: 0.24, spd: 1.0 },
  taxi:       { files: ['kenney_taxi.glb'], len: 4.2, weight: 0.12, spd: 1.0, cls: 'car' },
  truck:      { files: ['kenney_truck.glb', 'kenney_delivery.glb', 'kenney_garbage-truck.glb', 'kenney_truck-flat.glb'], len: 6, weight: 0.06, spd: 0.82 },
  bus:        { files: ['polypizza_bus.glb'], len: 10.5, weight: 0.06, spd: 0.80 },
  ambulance:  { files: ['kenney_ambulance.glb'], len: 5, weight: 0, spd: 1.05 },   // button-only: a parked organic ambulance would latch a junction's preempt
};

// ── anchors [x=east, z=south]. Arterial runs straight along x (z=0); stems run perpendicular. ──
const A = {
  W:  [-175, 0],   // west arterial edge
  J1: [-90, 0],    // Junction 1 (T)
  J2: [0, 0],      // Junction 2 (4-way)
  J3: [90, 0],     // Junction 3 (T)
  E:  [175, 0],    // east arterial edge
  S1: [-90, 70],   // J1 south stem edge
  N2: [0, -70],    // J2 north stem edge
  S2: [0, 70],     // J2 south stem edge
  N3: [90, -70],   // J3 north stem edge (opposite side from J1's stem)
};

// junctions: box radius, arms (each arm names the neighbour anchor its traffic comes FROM),
// non-conflicting phase groups, and — per through-arm — the downstream approach it feeds
// (the junction-to-junction coordination link).
const JUN = {
  J1: {
    name: 'Jct 1 · T', center: A.J1, boxR: 11,
    arms: { W: { from: 'W' }, E: { from: 'J2' }, S: { from: 'S1' } },
    phases: [['W', 'E'], ['S']],
    down: { W: ['J2', 'W'] },               // eastbound feeds J2's west approach
  },
  J2: {
    name: 'Jct 2 · 4-way', center: A.J2, boxR: 11,
    arms: { W: { from: 'J1' }, E: { from: 'J3' }, N: { from: 'N2' }, S: { from: 'S2' } },
    // split N and S into their own phases: both stems carry permitted left turns that would sweep
    // through the box centre and lock if discharged together. Separate greens keep them conflict-free.
    phases: [['W', 'E'], ['N'], ['S']],
    down: { W: ['J3', 'W'], E: ['J1', 'E'] },
  },
  J3: {
    name: 'Jct 3 · T', center: A.J3, boxR: 11,
    arms: { W: { from: 'J2' }, E: { from: 'E' }, N: { from: 'N3' } },
    phases: [['W', 'E'], ['N']],
    down: { E: ['J2', 'E'] },               // westbound feeds J2's east approach
  },
};

// routes: ordered anchor path + the (junction,arm) it obeys at each junction it crosses.
// crossing='Jx' flags a turn that cuts across the opposing through stream (needs gap acceptance).
// lanes = how many parallel lane variants to generate (2 on the arterial, 1 on the stems).
// NOTE: only STEM→arterial turns are modelled (they discharge on their own stem-green with the
// arterial red, so the box is conflict-free). The reverse — arterial→stem left turns — are
// deliberately omitted: with no protected turn phase they enter the box during the arterial green,
// yield to opposing through traffic while sitting in the box, and get stranded across the phase
// boundary, deadlocking against the next phase's stem discharge. Dropping them keeps the network
// deadlock-free while still demonstrating turns at every stem.
const ROUTES = {
  W_E:  { seq: ['W', 'J1', 'J2', 'J3', 'E'], stops: [['J1', 'W'], ['J2', 'W'], ['J3', 'W']], lanes: 2, w: 3.0 },
  E_W:  { seq: ['E', 'J3', 'J2', 'J1', 'W'], stops: [['J3', 'E'], ['J2', 'E'], ['J1', 'E']], lanes: 2, w: 3.0 },

  S1_E: { seq: ['S1', 'J1', 'J2', 'J3', 'E'], stops: [['J1', 'S'], ['J2', 'W'], ['J3', 'W']], lanes: 1, w: 1.0, crossing: 'J1' },
  S2_E: { seq: ['S2', 'J2', 'J3', 'E'], stops: [['J2', 'S'], ['J3', 'W']], lanes: 1, w: 1.0, crossing: 'J2' },
  N2_W: { seq: ['N2', 'J2', 'J1', 'W'], stops: [['J2', 'N'], ['J1', 'E']], lanes: 1, w: 1.0, crossing: 'J2' },
  N3_W: { seq: ['N3', 'J3', 'J2', 'J1', 'W'], stops: [['J3', 'N'], ['J2', 'E'], ['J1', 'E']], lanes: 1, w: 1.0, crossing: 'J3' },

  AMB:  { seq: ['W', 'J1', 'J2', 'J3', 'E'], stops: [['J1', 'W'], ['J2', 'W'], ['J3', 'W']], lanes: 1, w: 0 },
};

// which entry edge each organic route starts from (AMB is button-only, excluded)
const ENTRY_OF = { W_E: 'W', E_W: 'E', S1_E: 'S1', S2_E: 'S2', N2_W: 'N2', N3_W: 'N3' };

// ─────────────────────────── renderer / scene ───────────────────────────
const canvas = document.getElementById('app');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
renderer.setSize(innerWidth, innerHeight);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 0.95;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x9ab4cc);
scene.fog = new THREE.Fog(0x9ab4cc, 240, 720);
scene.environment = new THREE.PMREMGenerator(renderer).fromScene(new RoomEnvironment(), 0.04).texture;

const camera = new THREE.PerspectiveCamera(46, innerWidth / innerHeight, 0.1, 1600);
camera.position.set(4, 214, 176);                // south of the arterial, looking north across all 3 junctions
camera.lookAt(0, 0, -2);

const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, 0, -2);
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
  { key: 'art', anchors: ['W', 'J1', 'J2', 'J3', 'E'], half: 7.8, main: true },   // 2+2 arterial
  { key: 's1',  anchors: ['S1', 'J1'], half: 4.4 },
  { key: 'n2',  anchors: ['N2', 'J2'], half: 4.4 },
  { key: 's2',  anchors: ['S2', 'J2'], half: 4.4 },
  { key: 'n3',  anchors: ['N3', 'J3'], half: 4.4 },
];
const roadPolys = {};
ROADS.forEach(r => { roadPolys[r.key] = anchorPoly(r.anchors); });

// text-bubble sprite (junction id labels), same look as the main sim's N/S/E/W markers
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
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: (() => { const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t; })(), depthTest: false, transparent: true }));
  sp.scale.set(scale, scale / 4, 1); sp.position.set(x, y, z); sp.renderOrder = 5;
  scene.add(sp);
  return sp;
}

function buildWorld() {
  mergedBoxes([[1500, 0.2, 1500, 0, -0.12, 0]], MAT.grass);

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

  // ── lane markings: yellow centre dashes + white lane dividers on the arterial ──
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
    if (r.main) { put(LANE_W, whiteGeos, 0.16); put(-LANE_W, whiteGeos, 0.16); }     // 2+2 lane dividers
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

  // ── low-poly buildings + trees scattered beyond the sidewalks (shared window materials) ──
  const bMats = ['#8a7f6d', '#7d8489', '#93867b', '#877e70'].map(t =>
    new THREE.MeshStandardMaterial({ map: windowsTexture(t), roughness: 0.9 }));
  const trunks = [], crowns = [];
  for (const r of ROADS) {
    const poly = roadPolys[r.key];
    const stride = r.main ? 7 : 5;
    for (let i = 4; i < poly.length - 3; i += stride) {
      for (const s of [1, -1]) {
        const base = offsetLeft(poly, s * (r.half + 5))[i];
        if (Math.random() < 0.42) {
          const h = 6 + Math.random() * 10, w = 7 + Math.random() * 6, d = 7 + Math.random() * 6;
          const off = offsetLeft(poly, s * (r.half + 8 + w / 2))[i];
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

  // ── junction id tags (orientation aid — not place names) ──
  label('J1', A.J1[0], A.J1[1] - 2, 15, 20);
  label('J2', A.J2[0], A.J2[1] - 2, 15, 20);
  label('J3', A.J3[0], A.J3[1] - 2, 15, 20);
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
    const m = new THREE.Mesh(new THREE.SphereGeometry(0.4 * scale, 12, 12),
      // toneMapped:false — ACES washed lit red to peach / green to mint-white at demo exposure
      new THREE.MeshStandardMaterial({ color: BULB.off, emissive: BULB.off, toneMapped: false }));
    m.position.set(0, (1 - i) * scale, 0.32 * scale); g.add(m); bulbs[c] = m;
    // rear repeater sharing the lens material — the aspect reads from every camera angle
    const rep = new THREE.Mesh(new THREE.CircleGeometry(0.17 * scale, 10), m.material);
    rep.position.set(0, (1 - i) * scale, -0.33 * scale);
    rep.rotation.y = Math.PI;
    g.add(rep);
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
    b[c].material.emissiveIntensity = on ? 2.2 : 1;   // saturated, not blown out (toneMapped:false)
  }
}

// ─────────────────────────── route library (shared lane polylines) ───────────────────────────
// Insert axis-aligned "gate" points a fixed short distance before/after each junction the route
// passes THROUGH, so the Catmull-Rom tangent at the junction follows the true road directions.
// Without this, a far stem anchor (70m away) throws a diagonal tangent that bulges the turn curve
// into the oncoming lane — a head-on freeze that tows cars endlessly.
function expandSeq(seq) {
  const P = seq.map(k => A[k]);
  const out = [P[0].slice()];
  for (let i = 1; i < P.length - 1; i++) {
    const prev = P[i - 1], cur = P[i], next = P[i + 1];
    if (seq[i] in JUN) {
      const D = JUN[seq[i]].boxR + 7;
      let ix = cur[0] - prev[0], iz = cur[1] - prev[1]; let L = Math.hypot(ix, iz) || 1; ix /= L; iz /= L;
      let ox = next[0] - cur[0], oz = next[1] - cur[1]; let M = Math.hypot(ox, oz) || 1; ox /= M; oz /= M;
      out.push([cur[0] - ix * D, cur[1] - iz * D]);   // gate in  (along the incoming road)
      out.push(cur.slice());                          // junction centre
      out.push([cur[0] + ox * D, cur[1] + oz * D]);   // gate out (along the outgoing road)
    } else {
      out.push(cur.slice());
    }
  }
  out.push(P[P.length - 1].slice());
  return out;
}
function buildRoute(def, lane) {
  const center = resample(catmull(expandSeq(def.seq)), STEP);
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
  // interpolate yaw along the shortest arc too — snapping to head[lo] made turners visibly
  // click-rotate every STEP metres through an arc
  const dy = head[hi] - head[lo], wrapped = Math.atan2(Math.sin(dy), Math.cos(dy));
  return { x: pts[lo][0] + (pts[hi][0] - pts[lo][0]) * t, z: pts[lo][1] + (pts[hi][1] - pts[lo][1]) * t, ry: head[lo] + wrapped * t };
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
  // any type whose GLBs all failed still spawns as a plain box of the right footprint — the sim
  // keeps moving and tracking every vehicle with zero model files on disk
  const tint = { taxi: 0xd9b23a, ambulance: 0xf2f4f6, bus: 0x2f6f4f };
  for (const [type, cfg] of Object.entries(TYPES)) {
    if (pools[type].length) continue;
    const w = type === 'motorcycle' ? 0.7 : cfg.len >= 6 ? 2.3 : 1.75;
    const h = type === 'motorcycle' ? 1.3 : Math.min(3.2, cfg.len * 0.3 + 0.9);
    const box = new THREE.Mesh(new THREE.BoxGeometry(w, h, cfg.len),
      new THREE.MeshStandardMaterial({ color: tint[type] ?? 0x8a939c, roughness: 0.7 }));
    box.position.y = h / 2;
    const g = new THREE.Group(); g.add(box);
    pools[type].push(g);
  }
  ready = true;
}

function pickType() {
  let r = Math.random(), sum = 0;
  for (const t in TYPES) { sum += TYPES[t].weight; if (r <= sum) return t; }
  return 'car';
}

const cars = [];
const blobGeos = {};                              // shadow-blob geometry cache, keyed by vehicle length
const blobGeo = len => blobGeos[len] ??= new THREE.PlaneGeometry(len * 0.9, len * 1.25);
function placeCar(c) {
  const p = poseAt(c.route, c.s);
  c.mesh.position.set(p.x, 0, p.z);
  c.mesh.rotation.y = p.ry;
}
function addCar(routeName, { type = null, lane = null, slack = false, at = 0 } = {}) {
  const emerg = type === 'ambulance';   // a siren gets real slack — the 🚑 button must never die at the cap
  if (!ready || cars.length >= MAX_CARS + (emerg ? 12 : 8) || (cars.length >= MAX_CARS && !slack && !emerg)) return;
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
  // seeds land at arbitrary route fractions — never seed INSIDE a junction box: a mid-box,
  // mid-turn stranger is exactly the wedge the tow truck otherwise clears 35s into the demo
  if (at > 0) for (const jid in JUN) {
    const jc = JUN[jid].center;
    if (Math.hypot(head.x - jc[0], head.z - jc[1]) < JUN[jid].boxR + 2) return;
  }
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
  const blob = new THREE.Mesh(blobGeo(T.len), blobMat);   // shared per length — never disposed per car
  blob.rotation.x = -Math.PI / 2; blob.position.y = 0.02;
  mesh.add(blob);
  const c = { route, routeName, lane, s: s0, mesh, len: T.len, type,
    speed: SPEED * (T.spd || 1) * (0.85 + Math.random() * 0.3), vel: 0, stuck: 0, blocked: false,
    frozRef: s0, frozT: 0 };
  placeCar(c);
  scene.add(mesh);
  cars.push(c);
}

// hard separation: every vehicle is a capsule (spine segment + half-WIDTH); two bodies may NEVER
// overlap. Radius must model width, never length — len/6 discs made a bus 1.75m "wide", so the
// adjacent lane 3m away read as a collision and whole lanes phantom-froze (root-caused in the
// main sim; ported here verbatim).
const halfW = c => c.type === 'motorcycle' ? 0.5 : c.len >= 6 ? 1.25 : 0.95;
function spineOf(x, z, ry, len, r) {
  const hx = -Math.sin(ry), hz = -Math.cos(ry), h = Math.max(0.1, len / 2 - r);
  return [x - hx * h, z - hz * h, x + hx * h, z + hz * h];
}
function pointSegDist(px, pz, s) {
  const dx = s[2] - s[0], dz = s[3] - s[1], L2 = dx * dx + dz * dz;
  const t = L2 ? Math.max(0, Math.min(1, ((px - s[0]) * dx + (pz - s[1]) * dz) / L2)) : 0;
  return Math.hypot(px - (s[0] + t * dx), pz - (s[1] + t * dz));
}
function segDist(A, B) {
  const rx = A[2] - A[0], rz = A[3] - A[1], qx = B[2] - B[0], qz = B[3] - B[1];
  const d = rx * qz - rz * qx, wx = B[0] - A[0], wz = B[1] - A[1];
  if (d) {                                                // segments cross → zero distance
    const s = (wx * qz - wz * qx) / d, t = (wx * rz - wz * rx) / d;
    if (s >= 0 && s <= 1 && t >= 0 && t <= 1) return 0;
  }
  return Math.min(pointSegDist(A[0], A[1], B), pointSegDist(A[2], A[3], B),
                  pointSegDist(B[0], B[1], A), pointSegDist(B[2], B[3], A));
}
function minGapAt(c, s) {
  const p = poseAt(c.route, s), rc = halfW(c), mine = spineOf(p.x, p.z, p.ry, c.len, rc);
  let gap = Infinity;
  for (const o of cars) {
    if (o === c) continue;
    const q = o.mesh.position, dx = q.x - p.x, dz = q.z - p.z;
    const reach = (c.len + o.len) * 0.7 + 2;
    if (dx * dx + dz * dz > reach * reach) continue;      // coarse cull
    const ro = halfW(o);
    gap = Math.min(gap, segDist(mine, spineOf(q.x, q.z, o.mesh.rotation.y, o.len, ro)) - rc - ro);
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
    // only SAME-heading traffic is a leader — a crossing car as leader would freeze vMax to 0 and
    // gridlock the whole corridor behind it; crossing/oncoming bodies are the no-touch check's job
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
const BOX_CAP = 4;                                // "do not block the junction": box admission ceiling
const CROSS_PATIENCE = 6;                         // s a crossing turn yields for before it edges in
const boxCount = {};                              // vehicles currently inside each junction box
const turnClaim = {};                             // jid → long vehicle currently sweeping its crossing turn
let phaseGreen = {};                              // phaseGreen[jid] = Set of arms currently green
let maxStuck = 0, peakStuck = 0, towCount = 0;

function moveCars(dt) {
  // one allocation-free pass: box occupancy per junction + the worst stuck clock
  for (const jid in JUN) boxCount[jid] = 0;
  maxStuck = 0;
  for (const v of cars) {
    if ((v.stuck || 0) > maxStuck) maxStuck = v.stuck;
    for (const jid in JUN) {
      const jc = JUN[jid].center;
      if (Math.hypot(v.mesh.position.x - jc[0], v.mesh.position.z - jc[1]) < JUN[jid].boxR) boxCount[jid]++;
    }
  }
  if (maxStuck > peakStuck) peakStuck = maxStuck;

  // long-vehicle turn claim (ported from the main sim's solved bus-turn jam): while a bus/truck
  // sweeps its crossing turn through a box, hold every other approach of that junction — cars
  // flooding in on their green wall off the rest of the arc and wedge the box for half a minute.
  // A claimant that wedges anyway (stuck > 3) releases its claim so squeeze/tow attack the knot.
  for (const jid in JUN) turnClaim[jid] = null;
  for (const v of cars) {
    if (v.len < 6 || (v.stuck || 0) > 3) continue;
    for (const st of v.route.stops) {
      if (v.s < st.stopS) break;                  // stops are in encounter order — rest not reached
      if (st.crossing && v.s > st.stopS + 0.5 && v.s < st.sOut + v.len) { turnClaim[st.jid] = v; break; }
    }
  }

  for (const c of cars) {
    const before = c.s;
    let target = c.s + c.speed * dt;
    let stopDist = Infinity;

    const lg = leaderGap(c);
    const st = nextApproach(c);
    if (st) {
      const R = JUN[st.jid].boxR, jc = JUN[st.jid].center;
      const green = phaseGreen[st.jid] && phaseGreen[st.jid].has(st.arm);
      const boxFull = boxCount[st.jid] >= BOX_CAP;
      let hold = !green || boxFull;
      const claimant = turnClaim[st.jid];
      if (claimant && claimant !== c && c.type !== 'ambulance') hold = true;
      // arc-clearance pre-commit: a long crossing vehicle enters only an EMPTY box — mid-arc it
      // cannot thread gaps the way a car can, and one stranger on the swept path wedges it
      if (!hold && st.crossing && c.len >= 6 && boxCount[st.jid] > 0) hold = true;
      // crossing turn: on green, yield to fast conflicting box traffic — but only for a bounded
      // patience window. There is no protected turn phase, so on a saturated arterial a gap may
      // never open; after CROSS_PATIENCE the turner stops yielding and edges in as the no-touch
      // check allows, so it can NEVER starve to a 60s tow (nor block the through cars in its lane).
      // The ambulance has right of way (exclusive green path) and never yields.
      if (!hold && st.crossing && c.type !== 'ambulance') {
        let conflict = false;
        for (const o of cars) {
          if (o === c) continue;
          if (Math.hypot(o.mesh.position.x - jc[0], o.mesh.position.z - jc[1]) < R && (o.vel || 0) > 1.5) { conflict = true; break; }
        }
        if (conflict && (c.yieldWait || 0) < CROSS_PATIENCE) { hold = true; c.yieldWait = (c.yieldWait || 0) + dt; }
        else if (!conflict) c.yieldWait = 0;
      } else c.yieldWait = 0;
      // anti-spillback (compact corridor): don't enter the box unless the exit beyond it can hold
      // me. At ~90m junction spacing a jam at the next junction backs into this box and locks it —
      // so if my same-heading leader is closer than a full box-crossing, wait at the stop line and
      // KEEP THE BOX CLEAR. The ambulance is exempt (exclusive green path).
      if (!hold && c.type !== 'ambulance' && (st.stopS - c.s) < R + 6 && lg < 2 * R + c.len + GAP) hold = true;
      if (hold) {
        const line = st.stopS - c.len / 2;
        if (c.s <= line + 0.02) { target = Math.min(target, line); stopDist = Math.min(stopDist, line - c.s); }
      }
    }

    if (lg < Infinity) { const wall = lg - GAP; target = Math.min(target, c.s + Math.max(0, wall)); stopDist = Math.min(stopDist, wall); }

    // driving envelope: v² = 2·a·d braking toward the CONSTRAINT, corner cap by lateral-g
    const allowed = Math.max(0, target - c.s);
    let vMax = Math.min(c.speed, Math.sqrt(2 * 5.5 * Math.max(0, stopDist)));
    vMax = Math.min(vMax, vcapAt(c.route, c.s));
    const acc = c.type === 'bus' || c.type === 'truck' ? 3.5 : 6.5;
    c.vel = (c.vel ?? 0) < vMax ? Math.min(vMax, (c.vel ?? 0) + acc * dt) : Math.max(vMax, c.vel - 8 * dt);
    let adv = Math.min(allowed, c.vel * dt);

    if (adv > 1e-4) {
      const now = minGapAt(c, c.s);
      const next = minGapAt(c, c.s + adv);
      const clearAhead = next > 1.2;              // genuinely unobstructed road ahead
      // squeeze winner is LOCAL: one per knot — a global max-stuck token let a single un-squeezable
      // car far away starve every other knot of its escape and gridlock the whole corridor
      const wedged = (c.stuck || 0) > 4 && !cars.some(o => o !== c && (o.stuck || 0) > (c.stuck || 0) &&
        Math.hypot(o.mesh.position.x - c.mesh.position.x, o.mesh.position.z - c.mesh.position.z) < 25);
      const inching = (c.stuck || 0) > 1.5;
      // crawl ONLY when a body is actually close ahead. On open road run free — otherwise a car
      // that once inched stays throttled below the reset threshold forever, so its stuck clock
      // ratchets to a tow while it is cruising a clear road (the "inching ratchet").
      if ((wedged || inching) && !clearAhead) adv = Math.min(adv, c.speed * 0.25 * dt);
      const blockedNow = wedged ? next < 0.02
                       : inching ? next < 0.06 && next < now
                       : next < SEP && next < now;
      // c.stuck is the SQUEEZE clock — it drives the inching/wedged creep-out, so it must build
      // whenever a car isn't making clean progress. It is NOT the tow trigger (that is the
      // displacement window below), so building it while crawling no longer tows a healthy car.
      if (blockedNow) { adv = 0; c.vel = 0; c.stuck = (c.stuck || 0) + dt; }
      else if (clearAhead) c.stuck = 0;           // free-flowing → clear the squeeze clock
      else if (adv < 0.3 * c.speed * dt) c.stuck = (c.stuck || 0) + dt;
      else c.stuck = 0;
    } else {
      c.stuck = minGapAt(c, c.s) < SEP ? (c.stuck || 0) + dt : 0;
    }
    c.s += adv;
    // displacement-window freeze detector — the TOW trigger. A car that advances < 3 m keeps its
    // window running; any 3 m of progress resets it (moving up one queue spot = real progress, so a
    // car edging up a multi-cycle queue never trips it). Only a genuinely frozen car — a deadlock
    // that truly never moves — runs the clock up and is towed.
    if (c.s - c.frozRef > 3) { c.frozRef = c.s; c.frozT = 0; } else c.frozT += dt;
    c.blocked = (c.s - before) < 0.25 * c.speed * dt;
    placeCar(c);
  }

  for (let i = cars.length - 1; i >= 0; i--) {
    const c = cars[i];
    const inBox = Object.keys(JUN).some(jid =>
      Math.hypot(c.mesh.position.x - JUN[jid].center[0], c.mesh.position.z - JUN[jid].center[1]) < JUN[jid].boxR);
    // tow truck: a genuinely frozen car (no 3 m of progress for a long time) never lives forever —
    // 52 s if it is blocking a junction box, 75 s on open road. Both are under the 60 s that would
    // read as a stuck vehicle on an open approach; box freezes get the shorter fuse because a
    // stalled car inside the junction blocks every conflicting movement. Crawling/queuing never
    // trips this (moving up one queue spot resets the window).
    const towed = (c.frozT > 40 && inBox) || c.frozT > 58;
    if (c.s >= c.route.total - 0.3 || towed) {
      if (towed) towCount++;
      scene.remove(c.mesh);                        // geometry/materials are shared with the pools — nothing to dispose
      cars.splice(i, 1);
    }
  }
}

// ─────────────────────────── decentralized signal control ───────────────────────────
let simTime = 0, systemOn = true;
const MIN_GREEN = 5, YELLOW = 2, ALLRED = 2.5, MAX_GREEN = 34, MAX_WAIT = 26, FIX_GREEN = 18, PREEMPT_HOLD = 2.5, MAX_PREEMPT = 25;
const FLOW_TAU = 60;      // seconds of memory in the arrival/discharge estimators (src/controller.py)

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
  J1: ['Arterial E⇄W', 'South arm'], J2: ['Arterial E⇄W', 'North arm', 'South arm'], J3: ['Arterial E⇄W', 'North arm'],
};
class Controller {
  constructor(jid) {
    this.jid = jid; this.J = JUN[jid];
    this.pi = 0; this.pending = 0; this.sub = 'green'; this.t = 0; this.ge = 0;
    this.wait = Object.fromEntries(Object.keys(this.J.arms).map(a => [a, 0]));
    // measured per arm, same as src/controller.py: nothing about the site is configured
    this.arr = Object.fromEntries(Object.keys(this.J.arms).map(a => [a, 0]));
    this.dis = Object.fromEntries(Object.keys(this.J.arms).map(a => [a, 0]));
    this.prevQ = {};
    this.floor = MIN_GREEN;                        // green length decided at onset
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
  // Webster's cycle on the rates we measured, split by critical ratio. Phases here are already
  // disjoint, so Y is just their sum. Below saturation this lands under MIN_GREEN and never binds.
  serviceFloor() {
    const ratio = this.J.phases.map(p => Math.max(...p.map(a => this.dis[a] > 0 ? this.arr[a] / this.dis[a] : 0)));
    const Y = ratio.reduce((a, b) => a + b, 0);
    if (Y <= 0) return MIN_GREEN;
    if (Y >= 1) return MAX_GREEN;                  // no cycle clears this demand; hold the longest green
    const L = this.J.phases.length * (YELLOW + ALLRED);
    const C = (1.5 * L + 5) / (1 - Y);
    return Math.max(MIN_GREEN, (C - L) * ratio[this.pi] / Y);
  }

  // what handing over throws away: the served arms stop discharging for yellow + all-red,
  // capped at what they still have queued, so leaving a drained phase is free
  switchCost(Q) {
    const lost = YELLOW + ALLRED;
    return this.J.phases[this.pi].reduce((s, a) => s + Math.min(Q[a] || 0, this.dis[a] * lost), 0);
  }

  startYellow(nextP) { this.pending = nextP; this.sub = 'yellow'; this.t = 0; }
  update(dt, allQ, amb) {
    const Q = allQ[this.jid], cur = this.J.phases[this.pi];
    // one pass: learn this arm's rates, and age its queue. On green the clock unwinds by what
    // actually discharged rather than resetting, so a queue that is not moving keeps its age.
    const alpha = Math.min(1, dt / FLOW_TAU);
    for (const arm in this.J.arms) {
      const green = this.sub === 'green' && cur.includes(arm);
      const prev = this.prevQ[arm], q = Q[arm] || 0;
      this.prevQ[arm] = q;
      if (prev !== undefined) {
        const rate = (q - prev) / dt;
        if (green) { if (prev > 0) this.dis[arm] = Math.max(0, this.dis[arm] + alpha * (this.arr[arm] - rate - this.dis[arm])); }
        else this.arr[arm] = Math.max(0, this.arr[arm] + alpha * (rate - this.arr[arm]));
      }
      if (!green) this.wait[arm] = q > 0.1 ? this.wait[arm] + dt : 0;
      else if (q <= 0.1) this.wait[arm] = 0;
      else if (prev > 0) this.wait[arm] *= Math.max(0, 1 - Math.max(0, prev - q) / prev);
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
      } else {
        const served = cur.every(a => (Q[a] || 0) <= 0.1);
        let best = this.pi, bp = -Infinity;
        for (let p = 0; p < this.J.phases.length; p++) { const pr = this.pressure(p, allQ); if (pr > bp) { bp = pr; best = p; } }
        // an empty phase has no vehicle to strand, so it yields without waiting out the floor
        if (served && best !== this.pi && this.ge >= MIN_GREEN) { this.startYellow(best); this.apply(); return; }
        if (this.ge >= this.floor) {
          // force-serve the worst-treated queue: an arm on green is being served, not starved, and
          // an empty arm cannot starve at all. Without both, a full junction just rotates.
          const oldestServed = Math.max(0, ...cur.filter(a => (Q[a] || 0) > 0.1).map(a => this.wait[a]));
          let forced = -1, worst = MAX_WAIT;
          for (const arm in this.wait) {
            if (cur.includes(arm) || (Q[arm] || 0) <= 0.1) continue;
            if (this.wait[arm] > worst && this.wait[arm] > oldestServed) { worst = this.wait[arm]; forced = this.phaseOf(arm); }
          }
          let nextP = this.pi;
          if (forced >= 0) nextP = forced;
          else if (this.ge >= MAX_GREEN) nextP = best;
          else if (best !== this.pi &&
                   this.pressure(best, allQ) > this.pressure(this.pi, allQ) + 1.5 + this.switchCost(Q)) nextP = best;
          if (nextP !== this.pi) this.startYellow(nextP);
          else if (served) this.ge = this.floor;             // idle: don't burn toward max-green
        }
      }
    } else if (this.sub === 'yellow') {
      if ((this.t += dt) >= YELLOW) { this.sub = 'allred'; this.t = 0; }
    } else {
      if ((this.t += dt) >= ALLRED) { this.pi = this.pending; this.sub = 'green'; this.t = 0; this.ge = 0; this.floor = this.serviceFloor(); }
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
function tickSignals(dt) {
  const { Q, amb } = computeQueues();
  for (const jid in controllers) controllers[jid].update(dt, Q, amb);
}

// ─────────────────────────── spawns ───────────────────────────
const ENTRY_RATE = { W: 0.58, E: 0.55, S1: 0.16, S2: 0.16, N2: 0.16, N3: 0.16 };
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
    addCar(pickRoute(entry), {});
    nextSpawn[entry] = simTime + (0.5 + Math.random() * 1.7) / (ENTRY_RATE[entry] || 1);
  }
}
function spawnAmbulance() {
  // the button must NEVER silently no-op: if the west entry is congested the default s=0 spawn is
  // rejected by the clearance check, so retry a little further along the arterial until one takes.
  const attempt = () => {
    for (let at = 0; at <= 0.12; at += 0.015) {
      const n = cars.length;
      addCar('AMB', { type: 'ambulance', slack: true, at });
      if (cars.length > n) return true;
    }
    return false;
  };
  if (attempt()) return;
  // entry fully plugged, no gap anywhere: rearmost queued cars near the entry yield their slot —
  // exactly where a real siren pushes into a jam (mirrors the main sim's forceSpawn).
  const start = poseAt(ROUTE_LIB.AMB[0], 2);
  for (let k = 0; k < 3; k++) {
    const rear = cars.filter(c => c.type !== 'ambulance' && c.s < 32 &&
        Math.hypot(c.mesh.position.x - start.x, c.mesh.position.z - start.z) < 30)
      .sort((a, b) => a.s - b.s)[0];
    if (!rear) return;
    scene.remove(rear.mesh);                              // blob geometry is shared — never disposed
    cars.splice(cars.indexOf(rear), 1);
    if (attempt()) return;
  }
}

// network-wide instantaneous queue (vehicles stopped/slow at any approach)
function queuedNow() {
  let n = 0;
  for (const c of cars) {
    const st = nextApproach(c);
    if (st && st.stopS - c.s < DETECT && (c.vel || 0) < 2.6 && c.blocked) n++;
  }
  return n;
}

// debug handle
window.__net = {
  cars, junctions: JUN, controllers,
  queued: () => queuedNow(), boxCount: () => boxCount, phaseGreen: () => phaseGreen,
  systemOn: () => systemOn, maxStuck: () => maxStuck, peakStuck: () => peakStuck, towCount: () => towCount,
  camera, controls,
};
// deterministic fixed-dt advance for soak testing (background tabs throttle rAF and lie about wall-clock)
window.__burst = (steps = 600, dt = 1 / 30) => {
  let maxSeen = 0, everOver60 = 0;
  for (let i = 0; i < steps; i++) {
    simTime += dt; tickSignals(dt); moveCars(dt); spawnTick();
    if (maxStuck > maxSeen) maxSeen = maxStuck;
    if (cars.some(c => (c.stuck || 0) > 60)) everOver60++;
  }
  return { simSec: +simTime.toFixed(1), cars: cars.length, maxStuckNow: +maxStuck.toFixed(1),
    maxStuckInBurst: +maxSeen.toFixed(1), peakStuckEver: +peakStuck.toFixed(1), towCount, framesOver60: everOver60 };
};

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
  .relay-btn.amb{ background:rgba(255,59,48,.16); border-color:rgba(255,59,48,.55); color:#ffd7d4; }
  .relay-btn.amb:hover{ background:rgba(255,59,48,.28); }
  .glass{ background:var(--panel); backdrop-filter:blur(9px); -webkit-backdrop-filter:blur(9px);
    border:1px solid var(--line); border-radius:11px; box-shadow:0 6px 22px rgba(0,0,0,.35); }
  .relay-status{ position:fixed; left:14px; top:14px; z-index:11; min-width:216px; padding:11px 13px;
    color:var(--txt); font:12px ui-monospace,monospace; }
  .rs-row{ display:flex; justify-content:space-between; align-items:baseline; gap:14px; margin-top:6px; }
  .rs-n{ color:var(--muted); font-size:10.5px; letter-spacing:.06em; text-transform:uppercase; }
  .rs-p{ color:var(--grn); font-weight:700; font-size:12px; }
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
  .relay-scenario{ position:fixed; top:14px; right:14px; z-index:11; display:flex; flex-direction:column;
    gap:7px; align-items:flex-end; }
  .sc-body{ display:none; padding:10px; width:238px; flex-wrap:wrap; gap:6px; }
  .sc-body.open{ display:flex; }
  .sc-body .full{ flex-basis:100%; }
  .sc-body .half{ flex:1 1 44%; }
  .sc-cap{ flex-basis:100%; color:var(--muted); font:10px ui-monospace,monospace; letter-spacing:.05em;
    text-transform:uppercase; margin-top:2px; }
  .relay-banner{ position:fixed; top:14px; left:50%; transform:translateX(-50%); z-index:13; display:none;
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

  // status panel (top-left): brand + live phase per junction — one box, not three chips
  const status = document.createElement('div');
  status.className = 'relay-status glass';
  status.innerHTML = '<div class="rp-title">R.E.L.A.Y · network</div>' +
    Object.keys(JUN).map(jid =>
      `<div class="rs-row"><span class="rs-n">${JUN[jid].name}</span><span class="rs-p" id="chip-${jid}">—</span></div>`).join('');
  document.body.appendChild(status);
  for (const jid in JUN) chipEls[jid] = status.querySelector('#chip-' + jid);

  // queue stat + trend chart (bottom-right)
  const panel = document.createElement('div');
  panel.className = 'relay-panel glass';
  panel.innerHTML =
    '<div id="m-stat" class="rp-stat">0</div>' +
    '<div id="m-sub" class="rp-sub">vehicles queued now</div>' +
    '<div class="rp-cap">queued · last 90s</div>' +
    '<canvas id="mini"></canvas>' +
    '<div class="rp-legend"><span class="sw on"></span>R.E.L.A.Y. on&nbsp;&nbsp;<span class="sw off"></span>fixed timer</div>';
  document.body.appendChild(panel);
  statLine = panel.querySelector('#m-stat');
  subLine = panel.querySelector('#m-sub');
  miniChart = panel.querySelector('#mini');
  miniChart.width = 256 * PR; miniChart.height = 88 * PR;
  mctx = miniChart.getContext('2d'); mctx.scale(PR, PR);
  drawChart();

  // scenario tools (top-right): ONE visible button; ambulance + surges live in a fold-out
  const sp = document.createElement('div');
  sp.className = 'relay-scenario';
  const scBody = document.createElement('div');
  scBody.className = 'sc-body glass';
  const scBtn = button('⚡ scenarios ▾', () => {
    scBtn.textContent = scBody.classList.toggle('open') ? '⚡ scenarios ▴' : '⚡ scenarios ▾';
  });
  const amb = button('🚑 Ambulance green-wave', spawnAmbulance);
  amb.className = 'relay-btn amb full';
  scBody.appendChild(amb);
  const cap = document.createElement('div');
  cap.className = 'sc-cap'; cap.textContent = 'surge +8 vehicles onto:';
  scBody.appendChild(cap);
  // surge spawns slack past the soft cap and retry down the route; the MAX_CARS+8 hard ceiling
  // still bounds them, so back-to-back surges at full load deliver what fits, not a pile-up.
  const surgeOne = route => {
    // stride in METERS, not route fraction — 0.015×total was ~4m, smaller than one parked car's
    // clearance shadow, so a queued entry ate every retry.
    const total = ROUTE_LIB[route][0].total;
    for (let m = 0; m <= 60; m += 6) {
      const n = cars.length;
      addCar(route, { slack: true, at: m / total });
      if (cars.length > n) return;
    }
  };
  const surge = (label, route) => {
    const b = button(label, () => { for (let i = 0; i < 8; i++) setTimeout(() => surgeOne(route), i * 140); });
    b.classList.add('half');
    return b;
  };
  scBody.appendChild(surge('West', 'W_E'));
  scBody.appendChild(surge('East', 'E_W'));
  scBody.appendChild(surge('J1 south', 'S1_E'));
  scBody.appendChild(surge('J2 north', 'N2_W'));
  scBody.appendChild(surge('J2 south', 'S2_E'));
  scBody.appendChild(surge('J3 north', 'N3_W'));
  sp.append(scBtn, scBody);
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
// persistent handle for external pollers (compare bridge) — mutated, never rebuilt per frame
const RELAY = window.RELAY = { mode: 'relay', queued: 0, cars: 0, maxStuck: 0, phases: {} };
const setText = (el, s) => { if (el && el.__t !== s) { el.__t = s; el.textContent = s; } };
function tick() {
  const dt = Math.min(clock.getDelta(), 0.05);
  simTime += dt;
  tickSignals(dt);
  moveCars(dt);
  spawnTick();

  const q = queuedNow();                           // one scan per frame — every consumer shares it
  RELAY.mode = systemOn ? 'relay' : 'fixed';
  RELAY.queued = q; RELAY.cars = cars.length; RELAY.maxStuck = +maxStuck.toFixed(1);
  for (const jid in JUN) {
    const label = controllers[jid].chip();
    RELAY.phases[jid] = label;
    setText(chipEls[jid], label);
  }
  if (statLine) {
    setText(statLine, String(q));
    if (statLine.__on !== systemOn) { statLine.__on = systemOn; statLine.style.color = systemOn ? 'var(--grn)' : 'var(--red)'; }
    setText(subLine, `vehicles queued now · ${cars.length} in network`);
  }
  if (banner) {
    let names = '';
    for (const j in controllers) if (controllers[j].emergency) names += (names ? ' → ' : '') + j;
    banner.style.display = names ? 'block' : 'none';
    if (names) setText(banner, '🚑 EMERGENCY PREEMPT — green wave through ' + names);
  }
  if (mctx) {
    qSampleAcc += dt;
    if (qSampleAcc >= 0.75) { qSampleAcc = 0; qHist.push({ v: q, on: systemOn }); if (qHist.length > 120) qHist.shift(); drawChart(); }
  }

  controls.update();
  renderer.render(scene, camera);
  requestAnimationFrame(tick);
}

// ─────────────────────────── startup (visible .catch — never hang on "loading") ───────────────────────────
loadModels().then(() => {
  buildHUD();
  const entries = Object.keys(ENTRY_RATE);
  for (let i = 0; i < 18; i++) {                   // seed traffic spread along the network
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
