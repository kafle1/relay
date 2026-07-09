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
import { clone as cloneSkinned } from 'three/addons/utils/SkeletonUtils.js';
import { initPeds } from './peds.js?v=3';   // versioned: module caches must not pin an old pedestrian API

// ─────────────────────────── config ───────────────────────────
const P = new URLSearchParams(location.search);
const LIVE = P.has('live');
const CAP = +(P.get('capture') || 0);
const TOPO = ['T', '2'].includes((P.get('topo') || '').toUpperCase()) ? (P.get('topo') || '').toUpperCase() : '4';
const LANES = Math.min(3, Math.max(1, Number.isFinite(+P.get('lanes')) && P.get('lanes') ? +P.get('lanes') : 1));
const EMBED = P.has('embed');       // rendered inside compare.html — parent owns the chrome
const LOCKFIX = P.has('lockfixed'); // pin the dumb fixed timer + the imbalanced live demand pattern

const LANE_W = 3;
const ROAD_HALF = LANES * LANE_W + 1.9;          // lanes + shoulder: junction box gets real turning room
const ZEBRA = { from: ROAD_HALF + 0.7, to: ROAD_HALF + 3.3 };
const STOP = ZEBRA.to + 0.9;                     // stop line sits BEHIND the crossing
const START = 78 + ROAD_HALF;                    // spawn / despawn distance from the centre
const GAP = 1.6, SPEED = 14, MAX_CARS = 70;

// vehicle mix — Kathmandu-style: motorcycle-dominant, taxis, few heavy vehicles, rare ambulance.
// spd = pace multiplier (each vehicle adds its own jitter); cls = detector class when it differs.
const TYPES = {
  // rot overrides the default 180° model-facing fix; rider puts a human on the saddle
  // per-file facing fix: the two bike models ship with opposite native orientations
  motorcycle: { files: [['polypizza_motorcycle.glb', 0], ['polypizza_scooter.glb', Math.PI]], len: 2.1, weight: 0.50, spd: 1.15, rider: true },
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
const OPP = { N: 'S', S: 'N', E: 'W', W: 'E' };  // oncoming approach (arm may be absent on T/2 topologies)
const GROUPS = [...new Set(DIRS.map(d => APPROACH[d].group))];
const laneOff = (dir, lane) => APPROACH[dir].side * ((lane + 0.5) * LANE_W);
const hasRoad = (axis, s) =>                     // does a road arm exist on this side of the centre?
  axis === 'z' ? true : TOPO === '4' || (TOPO === 'T' && s === 1);

// turning: which arm a vehicle exits through, per approach and movement (right-hand traffic)
const EXITS = {
  N: { straight: 'S', right: 'W', left: 'E' },
  S: { straight: 'N', right: 'E', left: 'W' },
  E: { straight: 'W', right: 'N', left: 'S' },
  W: { straight: 'E', right: 'S', left: 'N' },
};
const ARM = {                                     // world-side data for exits (independent of APPROACH deletions)
  N: { axis: 'z', out: +1, side: -1, rotY: Math.PI },
  S: { axis: 'z', out: -1, side: +1, rotY: 0 },
  E: { axis: 'x', out: +1, side: +1, rotY: -Math.PI / 2 },
  W: { axis: 'x', out: -1, side: -1, rotY: Math.PI / 2 },
};

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
camera.position.set(26, 28, 38);                 // elevated overview
camera.lookAt(0, -1, 2);

// view modes: single overview, or one pole-mounted CCTV per approach (how a real deployment sees)
let camMode = (P.get('cam') === 'cctv' && !CAP && !LIVE) ? 'cctv' : 'overview';   // capture/live are calibrated to the overview camera
const armMarkers = [];              // floating N/S/E/W sprites — hidden in CCTV (pole cams sit next to them: giant letters)
const poleCams = {};
function buildPoleCams() {
  for (const dir of DIRS) {
    const a = APPROACH[dir];
    const cam = new THREE.PerspectiveCamera(52, 1, 0.1, 400);
    const along = a.sign * -(STOP + 6);              // a few metres behind the signal head
    const aside = (a.side > 0 ? -1 : 1) * (ROAD_HALF + 4.5);
    const eye = a.axis === 'z' ? [aside, 7.5, along] : [along, 7.5, -aside];
    const back = a.sign * -(STOP + 38);
    const look = a.axis === 'z' ? [a.side * ROAD_HALF / 2, 0.5, back] : [back, 0.5, a.side * ROAD_HALF / 2];
    cam.position.set(...eye);
    cam.lookAt(...look);
    poleCams[dir] = cam;
  }
}
buildPoleCams();

let controls = null;
if (!CAP) {                                      // drag anywhere except capture (training needs one fixed pose)
  controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(0, 0, 0);
  controls.enableDamping = true;
  controls.maxPolarAngle = Math.PI * 0.49;
  // in live mode the detector's zones depend on the camera — recompute them after every drag
  controls.addEventListener('end', () => {
    if (LIVE && wsOpen) ws.send(JSON.stringify({ type: 'zones', zones: computeZones() }));
  });
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

  // floating N/S/E/W markers over each arm so you always know which approach is which
  for (const dir of DIRS) {
    const arm = ARM[dir];
    const c = document.createElement('canvas');
    c.width = c.height = 128;
    const g = c.getContext('2d');
    g.fillStyle = 'rgba(15,18,22,0.75)';
    g.beginPath(); g.arc(64, 64, 56, 0, Math.PI * 2); g.fill();
    g.fillStyle = '#7dd3fc';
    g.font = '700 72px ui-monospace, monospace';
    g.textAlign = 'center'; g.textBaseline = 'middle';
    g.fillText(dir, 64, 68);
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c) }));
    sprite.scale.setScalar(4.5);
    const d = R + 18;
    sprite.position.set(arm.axis === 'x' ? arm.out * d : 0, 10, arm.axis === 'z' ? arm.out * d : 0);
    scene.add(sprite);
    armMarkers.push(sprite);
  }
}
buildWorld();

// ─────────────────────────── signal heads ───────────────────────────
const BULB = { red: 0xff3b30, yellow: 0xffcc00, green: 0x34c759, off: 0x181b20 };
const signalHeads = {};
function makeHead(scale = 1) {                             // housing + backplate + 3 hooded lenses
  const g = new THREE.Group(), bulbs = {};
  const back = new THREE.Mesh(new THREE.BoxGeometry(1.5 * scale, 3.5 * scale, 0.1), MAT.housing);
  back.position.z = -0.26 * scale;
  g.add(back);
  g.add(new THREE.Mesh(new THREE.BoxGeometry(1 * scale, 3 * scale, 0.6 * scale), MAT.housing));
  ['red', 'yellow', 'green'].forEach((c, i) => {
    const y = (1 - i) * scale;
    const m = new THREE.Mesh(new THREE.SphereGeometry(0.34 * scale, 14, 14),
      new THREE.MeshStandardMaterial({ color: BULB.off, emissive: BULB.off, emissiveIntensity: 1 }));
    m.position.set(0, y, 0.32 * scale);
    g.add(m);
    bulbs[c] = m;
    // hood/visor over each lens — reads as a real traffic-light head at any distance, and shades
    // the lit lens so the active colour pops instead of washing into the housing
    const hood = new THREE.Mesh(
      new THREE.CylinderGeometry(0.42 * scale, 0.42 * scale, 0.34 * scale, 14, 1, true, 0, Math.PI),
      MAT.housing);
    hood.rotation.set(Math.PI / 2, 0, 0);
    hood.position.set(0, y + 0.12 * scale, 0.36 * scale);
    g.add(hood);
  });
  return { g, bulbs };
}
// every approach gets a kerb pole; multi-lane approaches also get an overhead gantry with
// one head per lane, all facing the traffic they control. World-space placement throughout.
for (const dir of DIRS) {
  const a = APPROACH[dir], root = new THREE.Group(), bulbSets = [];
  const along = a.sign * -(STOP + 1.2);
  const kerbPerp = a.side * (ROAD_HALF + 1.6);             // kerb on this approach's own side
  const world = (perp, y) => a.axis === 'z' ? [perp, y, along] : [along, y, perp];

  const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.14, 0.16, LANES > 1 ? 7.2 : 6, 8), MAT.pole);
  pole.position.set(...world(kerbPerp, LANES > 1 ? 3.6 : 3));
  root.add(pole);

  const kerbHead = makeHead();
  kerbHead.g.position.set(...world(kerbPerp, 6));
  kerbHead.g.rotation.y = a.rotY;
  root.add(kerbHead.g);
  bulbSets.push(kerbHead.bulbs);

  if (LANES > 1) {                                          // gantry arm from the pole across the incoming half
    const innerPerp = laneOff(dir, LANES - 1) - a.side * (LANE_W / 2);
    const armLen = Math.abs(kerbPerp - innerPerp), armMid = (kerbPerp + innerPerp) / 2;
    const arm = new THREE.Mesh(
      a.axis === 'z' ? new THREE.BoxGeometry(armLen, 0.22, 0.22) : new THREE.BoxGeometry(0.22, 0.22, armLen),
      MAT.pole);
    arm.position.set(...world(armMid, 7.1));
    root.add(arm);
    for (let k = 0; k < LANES; k++) {
      const laneHead = makeHead(0.7);                      // per-lane head, one over each lane centre
      laneHead.g.position.set(...world(laneOff(dir, k), 6.4));
      laneHead.g.rotation.y = a.rotY;
      root.add(laneHead.g);
      bulbSets.push(laneHead.bulbs);
    }
  }

  const cam = new THREE.Group();                          // the CCTV unit itself, aimed up the approach
  const body = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.32, 0.8), MAT.housing);
  const lens = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.12, 0.3, 8), MAT.pole);
  lens.rotation.x = Math.PI / 2;
  lens.position.set(0, 0, 0.5);
  cam.add(body, lens);
  cam.position.set(...world(kerbPerp, LANES > 1 ? 7.6 : 6.4));
  cam.rotation.y = a.rotY + Math.PI;                      // watches the incoming queue
  cam.rotation.x = -0.35;
  root.add(cam);
  root.userData.bulbSets = bulbSets;
  scene.add(root);
  signalHeads[dir] = root;
}
function setSignal(dir, state) {
  for (const b of signalHeads[dir].userData.bulbSets) {
    for (const c of ['red', 'yellow', 'green']) {
      const on = c === state;
      b[c].material.color.setHex(on ? BULB[c] : BULB.off);
      b[c].material.emissive.setHex(on ? BULB[c] : BULB.off);
      b[c].material.emissiveIntensity = on ? 3.2 : 1;     // lit lens glows hard; dark lenses stay matte
      b[c].scale.setScalar(on ? 1.18 : 1);                // active lens swells slightly — reads at distance
    }
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

function normalize(root, targetLen, rot = Math.PI) {
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
  pivot.scale.setScalar(targetLen / Math.max(size.x, size.z, 0.01)); // floor: degenerate bbox must not scale to Infinity
  pivot.rotation.y = rot;                                // most models face -Z; bikes already face +Z
  return pivot;
}

let riderTemplate = null;                                // human for the bike saddles
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
    man.scale.setScalar(1.55 / Math.max(size.y, 0.01));  // seated-human height
    const ctr = new THREE.Vector3();
    bb.getCenter(ctr);
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
window.__relay = { cars };                       // console/debug handle (read-only use)
// route: cars pick a movement (straight / left / right) and swing onto the exit arm at a pivot
function planRoute(dir, lane) {
  const arms = Object.entries(EXITS[dir]).filter(([, arm]) => DIRS.includes(arm));
  // lane discipline: right turns from the kerb lane, left turns from the inner lane — a turn from
  // any other lane sweeps across its neighbours' straight paths. On an approach with no straight
  // exit (T stem) every lane must turn, so the non-kerb lanes all go left: a lane with no legal
  // move would fall back to a null route and drive straight off the map.
  const hasStraight = arms.some(([m]) => m === 'straight');
  const options = arms.filter(([m]) => m === 'straight' || LANES === 1
    || (m === 'right' ? lane === LANES - 1 : hasStraight ? lane === 0 : lane < LANES - 1));
  if (!options.length) return null;
  const weights = { straight: 0.55, right: 0.2, left: 0.25 };
  let r0 = Math.random() * options.reduce((s, [m]) => s + weights[m], 0);
  let move = options[0][0], arm = options[0][1];
  for (const [m, a] of options) { r0 -= weights[m]; if (r0 <= 0) { move = m; arm = a; break; } }
  if (move === 'straight') return null;

  // tangent quarter-circle between the entry lane line and the exit lane line
  const a = APPROACH[dir], exit = ARM[arm];
  const o1 = laneOff(dir, lane);                          // entry lane (perp coordinate)
  const o2 = -exit.side * ((lane + 0.5) * LANE_W);        // exit lane (along coordinate)
  const r = move === 'right' ? Math.max(2.4, ROAD_HALF * 0.55) : ROAD_HALF + LANE_W / 2 + 0.6;
  const ex = exit.out;                                    // exit travel sign on the perp axis
  const ez = -a.sign;                                     // approach side of the exit line
  const rotSign = Math.sign(ex * ez);
  // rotSign orients the arc's position math only. world() swaps x/z for E/W entries — a
  // reflection, which mirrors handedness — so mesh yaw must come from the real entry→exit
  // heading change, never from rotSign (that spun E/W turners backwards through the arc).
  const dYaw = ARM[arm].rotY - a.rotY;
  const yawSign = Math.sign(Math.atan2(Math.sin(dYaw), Math.cos(dYaw)));
  return {
    crossing: move === 'left',                            // wide arc across the oncoming flow → must yield
    uA: o2 / a.sign - r,                                  // arc begins here (u-space)
    arcLen: r * Math.PI / 2, r, ex, rotSign, yawSign,
    Cperp: o1 + r * ex, Calong: o2 + r * ez, o2,
    exitStartPerp: o1 + ex * r,
    entryRotY: a.rotY, exitRotY: ARM[arm].rotY,
  };
}
function poseOf(c, u) {                                   // pure pose: movement can test a position before taking it
  const a = APPROACH[c.dir];
  const world = (perp, along) => a.axis === 'z' ? [perp, along] : [along, perp];
  const r = c.route;
  if (r && u >= r.uA + r.arcLen) {                        // past the arc: straight out the exit arm
    const [x, z] = world(r.exitStartPerp + r.ex * (u - r.uA - r.arcLen), r.o2);
    return { x, z, ry: r.exitRotY };
  }
  if (r && u >= r.uA) {                                   // on the arc
    const th = (u - r.uA) / r.r;
    const [x, z] = world(r.Cperp - r.ex * r.r * Math.cos(th), r.Calong + r.rotSign * -r.ex * r.r * Math.sin(th));
    return { x, z, ry: r.entryRotY + r.yawSign * th };
  }
  const [x, z] = world(laneOff(c.dir, c.lane), a.sign * u); // straight approach
  return { x, z, ry: a.rotY };
}
function placeCar(c) {
  const p = poseOf(c, c.u);
  c.mesh.position.set(p.x, 0, p.z);
  c.mesh.rotation.y = p.ry;
}
function addCar(dir, u, forcedType) {
  // forced spawns (ambulance / surge buttons) get slack over the cap, but never unbounded
  const cap = carCap();
  if (!ready || cars.length >= cap + 10 || (cars.length >= cap && !forcedType)) return;
  const type = forcedType || pickType();
  const pool = pools[type];
  if (!pool || !pool.length) return;
  const T = TYPES[type], lane = (Math.random() * LANES) | 0;
  if (cars.some(c => c.dir === dir && c.lane === lane && Math.abs(c.u - u) < (c.len + T.len) / 2 + GAP)) return;
  const mesh = new THREE.Group();
  mesh.add(pool[(Math.random() * pool.length) | 0].clone(true));
  if (T.rider && riderTemplate) {
    const rider = cloneSkinned(riderTemplate);           // skinned model: plain .clone() renders at origin
    rider.position.y = 0.55;                             // on the saddle
    rider.traverse(o => { o.frustumCulled = false; });   // skinned bind-pose bounds mis-cull riders off-screen
    mesh.add(rider);
  }
  const blob = new THREE.Mesh(new THREE.PlaneGeometry(T.len * 0.9, T.len * 1.25), blobMat);
  blob.rotation.x = -Math.PI / 2;
  blob.position.y = 0.02;
  mesh.add(blob);
  mesh.rotation.y = APPROACH[dir].rotY;
  const c = { dir, u, lane, mesh, len: T.len, type, route: planRoute(dir, lane),
              speed: SPEED * (T.spd || 1) * (0.85 + Math.random() * 0.3) };
  placeCar(c);
  scene.add(mesh);
  cars.push(c);
}

// hard separation: every vehicle is a capsule (spine segment + half-WIDTH); two bodies may NEVER
// overlap. Radius must model width, not length: the old three-disc max(0.9, len/6) made a bus
// 3.5m "wide" — parked heavies phantom-blocked the whole adjacent lane (3m apart), freezing exits.
const SEP = 0.35;                                         // minimum body daylight (m)
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
// smallest surface-to-surface gap the car would have at position u (vs all cars + people on zebras)
function minGapAt(c, u, walkers) {
  const p = poseOf(c, u), rc = halfW(c), mine = spineOf(p.x, p.z, p.ry, c.len, rc);
  let gap = Infinity;
  for (const o of cars) {
    if (o === c) continue;
    const q = o.mesh.position, dx = q.x - p.x, dz = q.z - p.z;
    const reach = (c.len + o.len) * 0.7 + 2;
    if (dx * dx + dz * dz > reach * reach) continue;      // coarse cull
    const ro = halfW(o);
    gap = Math.min(gap, segDist(mine, spineOf(q.x, q.z, o.mesh.rotation.y, o.len, ro)) - rc - ro);
  }
  for (const w of walkers)
    gap = Math.min(gap, pointSegDist(w.x, w.z, mine) - rc - 0.45);
  return gap;
}

function moveCars(dt) {
  const inBox = c => Math.abs(c.mesh.position.x) < ROAD_HALF + 1 && Math.abs(c.mesh.position.z) < ROAD_HALF + 1;
  const boxCount = cars.filter(inBox).length;
  const maxStuck = Math.max(0, ...cars.map(c => c.stuck || 0));
  // a long vehicle (bus/truck) committed to a left-turn arc sweeps almost the whole box; hold the
  // other approaches out of the box so nothing parks in its swept path and deadlocks it mid-turn.
  // scoped to a turner still making progress (stuck<3): a wedged one drops the claim so the box can
  // drain and the tow backstop can clear it — never a whole-junction freeze.
  const turnClaim = cars.some(c => c.len >= 6 && c.route && c.route.crossing
    && c.u >= c.route.uA && c.u < c.route.uA + c.route.arcLen && (c.stuck || 0) < 3);
  const walkers = peds.crossers();                        // people on a zebra are hard obstacles
  const pedArm = new Set(walkers.map(w => w.dir));
  const byLane = {};
  for (const c of cars) (byLane[c.dir + c.lane] ||= []).push(c);      // follow the leader in YOUR lane
  for (const dir of DIRS) {
    const held = signalOf(dir) !== 'green' || pedArm.has(dir);        // red light, or someone on our zebra
    for (let lane = 0; lane < LANES; lane++) {
      const list = (byLane[dir + lane] || []).sort((p, q) => p.u - q.u);
      for (let i = 0; i < list.length; i++) {
        const c = list[i], before = c.u;
        if (c.accident > 0) { c.accident -= dt; c.blocked = true; c.vel = 0; continue; }
        let target = c.u + c.speed * dt;
        let stopDist = Infinity;         // distance to the nearest thing worth braking for
        const stopAt = -STOP - c.len / 2;
        // <= + epsilon: a car parked exactly AT the line stays held (strict < would release it)
        // held → red or someone on our zebra; else don't enter a packed box ("do not block the junction")
        if (c.u <= stopAt + 0.01 && (held || boxCount >= LANES + 2
            || (turnClaim && (!c.route || c.u < c.route.uA)))) {
          target = Math.min(target, stopAt);
          stopDist = stopAt - c.u;
        }
        const leader = list[i + 1];
        // the lane wall only binds while both cars are still on the shared straight segment —
        // once either enters its turning arc their paths diverge and u-distance means nothing
        // (minGapAt still hard-guards any real proximity)
        if (leader && (!c.route || c.u < c.route.uA) && (!leader.route || leader.u < leader.route.uA)) {
          const wall = leader.u - (c.len + leader.len) / 2 - GAP;
          target = Math.min(target, wall);
          stopDist = Math.min(stopDist, wall - c.u);
        }
        // gap acceptance: a left-turner's arc crosses the oncoming flow — wait at the line for a
        // real gap. Without this, opposing streams wedge head-on mid-box and no-overlap makes the
        // knot permanent (this, not vehicle speed, is what freezes the whole junction).
        if (c.route?.crossing && c.u <= stopAt + 0.01 && target > stopAt) {
          for (const o of cars) {
            if (o.dir !== OPP[c.dir]) continue;
            const oStop = -STOP - o.len / 2;
            const committed = o.u > oStop + 0.01 && o.u < ROAD_HALF + 4;    // already in/near the box
            const incoming = o.u <= oStop + 0.01 && oStop - o.u < 12 && (o.vel ?? 0) > 2;
            // (deliberately NOT yielding to an oncoming car still parked at its line: both launch,
            // meet mid-box, and the wedge/inch tiers squeeze the turner across — Kathmandu-style.
            // Yielding to parked cars starves the turner's whole lane for the entire green.)
            if (committed || incoming) { target = stopAt; stopDist = Math.min(stopDist, stopAt - c.u); break; }
          }
        }
        // arc-clearance: don't commit a turn if a STOPPED vehicle already sits inside the box on the
        // swept path — it can't clear in time and the turner wedges mid-arc (bus vs parked box car).
        // only STOPPED in-box cars count, so the moving oncoming flow never starves the turn.
        if (c.route && c.u <= stopAt + 0.01 && target > stopAt) {
          const rt = c.route;
          for (let k = 1; k <= 6; k++) {
            const us = rt.uA + (k / 6) * rt.arcLen, ps = poseOf(c, us), rcS = halfW(c);
            const mineS = spineOf(ps.x, ps.z, ps.ry, c.len, rcS);
            let hit = false;
            for (const o of cars) {
              if (o === c || (o.vel ?? 0) > 1 || !inBox(o)) continue;
              if (segDist(mineS, spineOf(o.mesh.position.x, o.mesh.position.z, o.mesh.rotation.y, o.len, halfW(o))) - rcS - halfW(o) < SEP) { hit = true; break; }
            }
            if (hit) { target = stopAt; stopDist = Math.min(stopDist, stopAt - c.u); break; }
          }
        }
        const wasStuck = c.stuck || 0;
        if (target > c.u && c.u > stopAt && vehicleAhead(c)) {
          // the longest-stuck vehicle nudges through (Kathmandu-style) — breaks yield cycles.
          // its stopDist stays the real constraint (line/leader): folding in the one-frame nudge
          // step would collapse the envelope to a sqrt(dt) crawl (≈1 m/s at 60fps, frame-rate dependent)
          if (wasStuck > 3 && wasStuck >= maxStuck - 1e-6) target = c.u + c.speed * 0.4 * dt;
          else { target = c.u; stopDist = 0; }
          c.stuck = wasStuck + dt;
        } else if (target > c.u) {
          c.stuck = 0;
        }
        // real driving envelope: v² = 2·a·d braking toward the CONSTRAINT (stop line / leader),
        // never toward this frame's kinematic step — that capped free cars at √(2·a·v·dt) ≈ 1.6 m/s
        const allowed = Math.max(0, target - c.u);
        let vMax = Math.min(c.speed, Math.sqrt(2 * 5.5 * Math.max(0, stopDist)));
        const rt = c.route;
        if (rt && c.u < rt.uA + rt.arcLen) {
          const vArc = Math.sqrt(3.4 * rt.r);                         // brisk-but-real lateral g through the arc
          vMax = Math.min(vMax, c.u >= rt.uA ? vArc : Math.sqrt(vArc * vArc + 11 * (rt.uA - c.u)));
        }
        const acc = c.type === 'bus' || c.type === 'truck' ? 3.5 : 6.5;
        c.vel = (c.vel ?? 0) < vMax ? Math.min(vMax, (c.vel ?? 0) + acc * dt) : Math.max(vMax, c.vel - 8 * dt);
        let adv = Math.min(allowed, c.vel * dt);
        if (adv > 1e-4) {
          const now = minGapAt(c, c.u, walkers);
          const next = minGapAt(c, c.u + adv, walkers);
          // three tiers, all zero-contact: normal keeps SEP daylight; a stuck driver inches while not
          // worsening anyone's gap; the single longest-stuck driver squeezes past to break a knot.
          // read wasStuck, not c.stuck: the reset above already zeroed it for gap-blocked cars,
          // which would keep every knot-plug permanently in the strictest tier
          const wedged = wasStuck > 4 && wasStuck >= maxStuck - 1e-6;
          const inching = wasStuck > 1.5;
          if (wedged || inching) adv = Math.min(adv, c.speed * 0.25 * dt);
          const blockedNow = wedged ? next < 0.02
                           : inching ? next < 0.06 && next < now
                           : next < SEP && next < now;
          if (blockedNow) { adv = 0; c.vel = 0; c.stuck = wasStuck + dt; }   // one dt per frame, max
        }
        c.u += adv;
        c.blocked = (c.u - before) < 0.25 * c.speed * dt;
        placeCar(c);
      }
    }
  }
  for (let i = cars.length - 1; i >= 0; i--) {
    const c = cars[i];
    // ponytail: wedged >30s in/at the box → towed away (real knots get untangled by traffic police);
    // guarantees gridlock always clears — upgrade path is true multi-car negotiation.
    // u-range check too: a long vehicle can plug the box entrance while its CENTRE sits outside
    // (a 10.5m bus at the line is untowable by the position test alone)
    const towed = (c.stuck || 0) > 18 && !(c.accident > 0)
      && (inBox(c) || Math.abs(c.u) < ROAD_HALF + c.len / 2 + 2);
    if (c.u > START || towed) {
      c.mesh.traverse(o => { if (o.geometry && o.geometry.type === 'PlaneGeometry') o.geometry.dispose(); });
      scene.remove(c.mesh);
      cars.splice(i, 1);
    }
  }
}

function vehicleAhead(c) {
  const p = c.mesh.position, ry = c.mesh.rotation.y;
  const hx = -Math.sin(ry), hz = -Math.cos(ry);          // unit heading on the ground plane
  for (const o of cars) {
    if (o === c) continue;
    const dx = o.mesh.position.x - p.x, dz = o.mesh.position.z - p.z;
    const ahead = dx * hx + dz * hz;                     // along my heading
    const beside = Math.abs(dx * hz - dz * hx);          // lateral offset
    if (ahead > 0 && ahead < (c.len + o.len) / 2 + 1.2 && beside < 2.2) return true;
  }
  return false;
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
const rate = (LIVE || LOCKFIX)                    // both A/B panels share the imbalanced demand
  ? { N: 1.7, S: 1.5, E: 0.4, W: 0.4 }             // busy NS, quiet EW — where a fixed timer bleeds green
  : Object.fromEntries(DIRS.map(d => [d, 0.55 + Math.random() * 1.3]));
let simTime = 0, chaosUntil = 0, density = 1;         // density: user traffic dial (× spawn rate AND × cap)
const DENSITY_MIN = 0.3, DENSITY_MAX = 2.0;
const carCap = () => Math.round(MAX_CARS * density);
function spawnTick() {
  for (const dir of DIRS) {
    if (simTime >= nextSpawn[dir]) {
      addCar(dir, -START);
      const storm = simTime < chaosUntil ? 3 : 1;       // Kathmandu chaos mode
      nextSpawn[dir] = simTime + (0.5 + Math.random() * 1.8) / ((rate[dir] || 1) * storm * density);
    }
  }
}

const NAMES_FULL = { N: 'north', S: 'south', E: 'east', W: 'west' };
let cctvLabelEls = [];
function cctvLabels(show) {
  cctvLabelEls.forEach(el => el.remove());
  cctvLabelEls = [];
  if (!show) return;
  DIRS.forEach((dir, i) => {
    const el = document.createElement('div');
    el.textContent = `● CAM ${i + 1} — ${NAMES_FULL[dir]} approach`;
    const rows = Math.ceil(DIRS.length / 2);
    Object.assign(el.style, {
      position: 'fixed', left: (i % 2) * 50 + (i % 2 ? 1.2 : 21) + '%', top: Math.floor(i / 2) * (100 / rows) + 1.5 + '%',
      zIndex: 8, font: '600 12px ui-monospace, monospace', color: '#ff6b62',
      background: 'rgba(10,12,15,.72)', padding: '5px 10px', borderRadius: '6px',
      border: '1px solid rgba(255,255,255,.12)', pointerEvents: 'none', letterSpacing: '.04em',
    });
    document.body.appendChild(el);
    cctvLabelEls.push(el);
  });
}

// ─────────────────────────── HUD + scenario controls ───────────────────────────
const hud = Object.fromEntries(['phase', 'N', 'S', 'E', 'W', 'total'].map(k => [k, document.getElementById('h-' + k)]));
if (EMBED) document.getElementById('hud').style.display = 'none';   // compare.html draws its own labels/strips — two HUDs is clutter

function button(label, onClick, id) {
  const b = document.createElement('button');
  b.textContent = label;
  b.className = 'relay-btn';
  if (id) b.id = id;
  b.onclick = onClick;
  return b;
}
function scenarioPanel() {
  const p = document.createElement('div');
  p.className = 'relay-scenario';
  for (const d of DIRS) p.appendChild(button('🚑 ' + d, () => addCar(d, -START, 'ambulance'), 'amb-' + d));
  const surge = d => { for (let i = 0; i < 9; i++) setTimeout(() => addCar(d, -START, Math.random() < 0.65 ? 'motorcycle' : 'car'), i * 130); };
  for (const d of DIRS) p.appendChild(button('surge ' + d, () => surge(d)));
  p.appendChild(button('💥 accident', () => {
    const victim = cars.find(c => c.u > -STOP - 8 && c.u < ROAD_HALF);
    if (victim) { victim.accident = 12; victim.stuck = 0; }
  }));
  p.appendChild(button('🌀 chaos ×3', () => { chaosUntil = simTime + 30; }));

  // traffic dial: scales spawn rate AND the car cap. decreasing lets the surplus drain naturally
  // (no cars vanish mid-road); increasing fills toward the higher cap.
  const dial = document.createElement('div');
  dial.className = 'relay-dial';
  const readout = document.createElement('span');
  readout.className = 'dval';
  const showDensity = () => { readout.textContent = 'traffic ×' + density.toFixed(2).replace(/0$/, ''); };
  const bump = step => {
    density = Math.min(DENSITY_MAX, Math.max(DENSITY_MIN, +(density + step).toFixed(2)));
    showDensity();
    // on a decrease, thin the surplus by pulling the FARTHEST-BACK cars (still out near the spawn
    // edge, off the junction) — snappy feedback with nothing vanishing mid-scene; the rest drains.
    if (step < 0) {
      const far = cars.filter(c => c.u < -ROAD_HALF - 22).sort((a, b) => a.u - b.u);
      for (const c of far) {
        if (cars.length <= carCap()) break;
        c.mesh.traverse(o => { if (o.geometry && o.geometry.type === 'PlaneGeometry') o.geometry.dispose(); });
        scene.remove(c.mesh);
        cars.splice(cars.indexOf(c), 1);
      }
    }
  };
  dial.appendChild(button('− traffic', () => bump(-0.25)));
  dial.appendChild(readout);
  dial.appendChild(button('+ traffic', () => bump(0.25)));
  showDensity();
  p.appendChild(dial);

  document.body.appendChild(p);
  junctionPanel();
}

// live junction switcher: any shape × any lane count, rebuilt on the spot
function junctionPanel() {
  const p = document.createElement('div');
  p.className = 'relay-junction glass';
  const rebuild = (topo, lanes) => {
    const q = new URLSearchParams(location.search);
    q.set('topo', topo); q.set('lanes', lanes);
    location.search = q.toString();                 // clean rebuild with the new geometry
  };
  const row = (label, items, active, onPick) => {
    const r = document.createElement('div');
    r.className = 'jrow';
    const l = document.createElement('span'); l.textContent = label; r.appendChild(l);
    for (const [text, value] of items) {
      const b = button(text, () => onPick(value));
      if (String(value) === String(active)) b.classList.add('on');
      r.appendChild(b);
    }
    return r;
  };
  p.appendChild(row('shape', [['4-way', '4'], ['T', 'T'], ['2-arm', '2']], TOPO, v => rebuild(v, LANES)));
  p.appendChild(row('lanes', [['1', 1], ['2', 2], ['3', 3]], LANES, v => rebuild(TOPO, v)));
  if (!CAP && !LIVE) p.appendChild(row('view', [['overview', 'overview'], ['CCTV ×' + DIRS.length, 'cctv']], camMode, v => {                       // capture/live: labels+zones are projected via the overview camera
    camMode = v;
    cctvLabels(v === 'cctv');
    [...p.children[2].querySelectorAll('button')].forEach(b =>
      b.classList.toggle('on', (b.textContent.startsWith('CCTV') ? 'cctv' : 'overview') === v));
  }));
  document.body.appendChild(p);
}

// ─────────────────────────── shared UI styling (control-room chrome) ───────────────────────────
function injectStyles() {
  if (document.getElementById('relay-css')) return;
  const s = document.createElement('style');
  s.id = 'relay-css';
  s.textContent = `
  :root{
    --bg:#0b0d10; --panel:rgba(16,19,24,.72); --panel-solid:rgba(15,18,23,.94);
    --line:rgba(255,255,255,.09); --line2:rgba(255,255,255,.16);
    --txt:#e8eaed; --muted:#9aa0a6; --cy:#7dd3fc; --grn:#86efac; --red:#ff6b62; --amb:#ffcc00;
  }
  .relay-btn{ font:600 12px/1 ui-monospace,"SF Mono",Menlo,monospace; color:var(--txt);
    background:rgba(28,33,41,.9); border:1px solid var(--line2); border-radius:7px;
    padding:7px 10px; cursor:pointer; transition:background .14s,border-color .14s,transform .06s; }
  .relay-btn:hover{ background:rgba(42,49,60,.95); border-color:rgba(125,211,252,.5); }
  .relay-btn:active{ transform:translateY(1px); }
  .relay-btn.on{ background:var(--cy); color:#0b0d10; border-color:var(--cy); }
  .glass{ background:var(--panel); backdrop-filter:blur(9px); -webkit-backdrop-filter:blur(9px);
    border:1px solid var(--line); border-radius:11px; box-shadow:0 6px 22px rgba(0,0,0,.35); }
  .relay-panel{ position:fixed; right:14px; bottom:14px; z-index:10; width:262px; padding:12px 13px;
    font:12px ui-monospace,Menlo,monospace; color:var(--txt); }
  .rp-title{ color:var(--cy); letter-spacing:.08em; font-weight:700; font-size:11px; text-transform:uppercase;
    display:flex; align-items:center; gap:7px; margin-bottom:9px; }
  .rp-title::before{ content:""; width:7px; height:7px; border-radius:50%; background:var(--grn);
    box-shadow:0 0 8px var(--grn); animation:relay-pulse 1.8s ease-in-out infinite; }
  @keyframes relay-pulse{ 0%,100%{opacity:1} 50%{opacity:.35} }
  .rp-stat{ font-size:18px; font-weight:700; color:var(--grn); line-height:1.15; transition:color .2s; }
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
  .relay-banner{ position:fixed; top:16px; left:50%; transform:translateX(-50%); z-index:13; display:none;
    font:700 14px ui-monospace,monospace; color:#fff; background:rgba(255,59,48,.94);
    padding:9px 18px; border-radius:10px; border:1px solid rgba(255,255,255,.25);
    animation:relay-flash 1s steps(1,end) infinite; }
  @keyframes relay-flash{ 0%,100%{box-shadow:0 6px 24px rgba(255,59,48,.55)} 50%{box-shadow:0 4px 10px rgba(255,59,48,.2)} }
  .relay-scenario{ position:fixed; top:14px; right:14px; z-index:11; display:flex; gap:6px; flex-wrap:wrap;
    max-width:264px; justify-content:flex-end; }
  .relay-dial{ display:flex; gap:6px; align-items:center; }
  .relay-dial .dval{ min-width:82px; text-align:center; color:var(--txt); font:600 12px ui-monospace,monospace;
    font-variant-numeric:tabular-nums; }
  .relay-junction{ position:fixed; left:14px; top:250px; z-index:11; display:flex; flex-direction:column; gap:7px;
    padding:10px 11px; font:12px ui-monospace,monospace; color:var(--muted); }
  .relay-junction .jrow{ display:flex; gap:5px; align-items:center; }
  .relay-junction .jrow > span{ width:48px; color:var(--muted); font-size:11px; }
  .relay-help{ position:fixed; left:14px; bottom:16px; z-index:11; width:34px; height:34px; padding:0;
    border-radius:50%; font:700 15px ui-monospace,monospace; color:var(--cy); }
  .relay-explain{ position:fixed; inset:0; z-index:20; display:none; align-items:center; justify-content:center;
    background:rgba(6,8,11,.55); backdrop-filter:blur(3px); -webkit-backdrop-filter:blur(3px); opacity:0; transition:opacity .25s; }
  .relay-explain.show{ opacity:1; }
  .relay-card{ width:min(452px,92vw); padding:22px 22px 18px; color:var(--txt);
    font:13px/1.55 ui-monospace,Menlo,monospace; transform:translateY(10px); transition:transform .25s; }
  .relay-explain.show .relay-card{ transform:none; }
  .relay-card h2{ font-size:13px; letter-spacing:.1em; text-transform:uppercase; color:var(--cy); margin-bottom:5px; }
  .relay-card .lede{ color:var(--muted); font-size:12px; margin-bottom:15px; }
  .relay-card ul{ list-style:none; display:flex; flex-direction:column; gap:12px; margin-bottom:18px; }
  .relay-card li{ display:flex; gap:11px; align-items:flex-start; }
  .relay-card li .ic{ flex:0 0 auto; width:27px; height:27px; border-radius:7px; background:rgba(125,211,252,.12);
    border:1px solid var(--line2); display:flex; align-items:center; justify-content:center; font-size:14px; }
  .relay-card li b{ color:var(--txt); font-weight:700; }
  .relay-card li span{ color:var(--muted); }
  .relay-card .go{ width:100%; padding:11px; font:700 13px ui-monospace,monospace; color:#0b0d10;
    background:var(--cy); border:none; border-radius:9px; cursor:pointer; transition:filter .14s; }
  .relay-card .go:hover{ filter:brightness(1.08); }
  `;
  document.head.appendChild(s);
}

// ─────────────────────────── live mode (closed loop) ───────────────────────────
let overlay, octx, statLine, subLine, miniChart, mctx, banner;
let modeWait = 0, modeT = 0;                     // waiting accumulated under the current mode
const qHist = [];                                // {v,on} queued samples for the live chart (~90s)
let qSampleAcc = 0;
const PR = Math.min(devicePixelRatio, 1.5);

function buildLiveUI() {
  overlay = document.createElement('canvas');
  Object.assign(overlay.style, { position: 'fixed', inset: '0', width: '100vw', height: '100vh', pointerEvents: 'none', zIndex: 5 });
  document.body.appendChild(overlay);
  octx = overlay.getContext('2d');
  sizeOverlay();

  banner = document.createElement('div');
  banner.className = 'relay-banner';
  if (EMBED) banner.style.top = '64px';            // clear the compare panel's label
  document.body.appendChild(banner);

  if (EMBED) return;                               // parent (compare.html) owns the control chrome

  const panel = document.createElement('div');
  panel.className = 'relay-panel glass';
  panel.innerHTML =
    '<div class="rp-title">R.E.L.A.Y · live control</div>' +
    '<div id="m-stat" class="rp-stat">warming up…</div>' +
    '<div id="m-sub" class="rp-sub">reading the camera…</div>' +
    '<div class="rp-cap">vehicles queued · last 90s</div>' +
    '<canvas id="mini"></canvas>' +
    '<div class="rp-legend"><span class="sw on"></span>R.E.L.A.Y. on&nbsp;&nbsp;<span class="sw off"></span>fixed timer</div>';
  document.body.appendChild(panel);
  statLine = panel.querySelector('#m-stat');
  subLine = panel.querySelector('#m-sub');
  miniChart = panel.querySelector('#mini');
  miniChart.width = 256 * PR; miniChart.height = 88 * PR;
  mctx = miniChart.getContext('2d');
  mctx.scale(PR, PR);
  drawChart();

  const toggle = button('', () => { systemOn = !systemOn; modeWait = 0; modeT = 0; paintToggle(); }, 'sys-toggle');
  const paintToggle = () => {
    toggle.textContent = systemOn ? '●  R.E.L.A.Y. ON — click for fixed timer' : '○  FIXED TIMER — click to switch R.E.L.A.Y. on';
    toggle.style.background = systemOn ? 'var(--grn)' : 'var(--red)';
    toggle.style.color = systemOn ? '#0b0d10' : '#fff';
  };
  paintToggle();
  document.body.appendChild(toggle);
}
function sizeOverlay() {
  if (overlay) { overlay.width = innerWidth * PR; overlay.height = innerHeight * PR; }
}

const BOX_COLORS = { car: '#34c759', motorcycle: '#ff9f0a', bus: '#5ac8fa', truck: '#ff453a', ambulance: '#ffffff', autorickshaw: '#bf5af2' };
function drawOverlay() {
  const W = overlay.width, H = overlay.height;
  octx.clearRect(0, 0, W, H);
  if (liveBoxes.at && performance.now() - liveBoxes.at > 700) return;   // feed stalled — no ghost boxes
  octx.lineWidth = (EMBED ? 1.25 : 2) * PR;
  octx.font = `600 ${11 * PR}px ui-monospace, monospace`;
  for (const b of liveBoxes) {
    const col = BOX_COLORS[b.cls] || '#34c759';
    octx.strokeStyle = col;
    octx.strokeRect(b.x * W, b.y * H, b.w * W, b.h * H);
    // in the small compare embeds the boxes alone carry the "camera is read" story — text is noise.
    if (EMBED) continue;
    octx.fillStyle = col;
    octx.fillText(`${b.cls} ${b.conf}`, b.x * W, Math.max(12 * PR, b.y * H - 3 * PR));
  }
}
// live queued-over-time chart: one series, each segment coloured by the mode that produced it,
// so flipping R.E.L.A.Y. off paints the resulting pile-up straight into the timeline.
function drawChart() {
  if (!mctx) return;
  const cw = 256, ch = 88, x0 = 22, x1 = cw - 4, y0 = 7, y1 = ch - 13;
  mctx.clearRect(0, 0, cw, ch);
  // y-axis climbs in human steps (1/2/5/10…): "0 10 20", never "0 13 26"
  const raw = Math.max(4, ...qHist.map(s => s.v)) / 2;
  const p10 = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 5, 10].map(m => m * p10).find(v => v >= raw);
  const mx = 2 * step;
  mctx.font = '9px ui-monospace, monospace';
  mctx.strokeStyle = 'rgba(255,255,255,.09)'; mctx.lineWidth = 1;
  mctx.fillStyle = '#6b7178'; mctx.textBaseline = 'middle'; mctx.textAlign = 'right';
  for (const f of [0, 0.5, 1]) {
    const y = y1 - f * (y1 - y0);
    mctx.beginPath(); mctx.moveTo(x0, y); mctx.lineTo(x1, y); mctx.stroke();
    mctx.fillText(Math.round(f * mx), x0 - 4, y);
  }
  mctx.textBaseline = 'alphabetic'; mctx.textAlign = 'left';
  mctx.fillText('-90s', x0, ch - 2);
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
  // stale live control must not keep steering the lights: drop to the safe fixed cycle while reconnecting
  ws.onclose = () => { wsOpen = false; liveSignals = null; livePhase = 'no signal — reconnecting…'; setTimeout(connectWS, 800); };
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    liveSignals = m.signals;
    livePhase = `${m.phase} ${m.stage}`;
    liveBoxes = m.boxes || [];
    liveBoxes.at = performance.now();      // stamp: stale boxes must vanish, not float over moved traffic
    liveCounts = m.counts;
    const emg = m.emergencies || [];
    banner.style.display = emg.length ? 'block' : 'none';
    if (emg.length) {
      const clearing = emg.filter(d => m.signals[d] === 'green');
      banner.textContent = clearing.length
        ? '🚑 EMERGENCY PREEMPT — clearing ' + clearing.join(', ')
        : '🚑 emergency detected on ' + emg.join(', ') + ' — switching…';
    }
    if (m.metrics && statLine) {
      const load = modeT > 3 ? (modeWait / modeT).toFixed(1) : '—';
      const tel = m.telemetry ? ` · inference ${m.telemetry.infer_ms}ms` : '';
      if (systemOn) {
        statLine.style.color = 'var(--grn)';
        const onS = qHist.filter(x => x.on), offS = qHist.filter(x => !x.on);
        if (onS.length > 8 && offS.length > 8) {
          const avg = a => a.reduce((t, x) => t + x.v, 0) / a.length;
          const gain = Math.round((1 - avg(onS) / Math.max(1, avg(offS))) * 100);
          statLine.textContent = gain > 2 ? `▼ ${gain}% fewer queued with R.E.L.A.Y. ON (measured here)` : 'toggle OFF, then ON — watch the queues';
        } else {
          statLine.textContent = 'toggle the system OFF and back ON to measure the difference live';
        }
      } else {
        statLine.style.color = 'var(--red)';
        statLine.textContent = '▲ fixed timer — not adapting';
      }
      subLine.textContent = `${queuedNow()} queued · ${load} veh·s/s${tel}`;
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
  const pedDemand = peds.waiting();                                  // push-button-style ped input per arm
  streamCanvas.toBlob(blob => {
    sending = false;
    if (!blob || !wsOpen) return;
    const fr = new FileReader();
    fr.onload = () => { try { ws.send(JSON.stringify({ type: 'frame', image: fr.result, emergencies, peds: pedDemand })); } catch {} };
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
  const dt = Math.min(clock.getDelta(), 0.05) || 1e-6;   // duplicate rAF timestamps give dt=0, which breaks the blocked flag
  simTime += dt;
  const fixedLabel = tickSignals(dt);
  moveCars(dt);
  peds.tick(dt);
  spawnTick();
  if (LIVE || LOCKFIX) { modeWait += queuedNow() * dt; modeT += dt; }   // veh·seconds queued under the current mode

  const c = (LIVE && liveCounts) || counts();
  hud.phase.textContent = (LIVE && systemOn) ? livePhase : fixedLabel + (LIVE ? ' (FIXED)' : '');
  for (const d of ['N', 'S', 'E', 'W']) hud[d].textContent = c[d] ?? '—';
  hud.total.textContent = cars.length;

  if (controls) controls.update();
  const cctvNow = camMode === 'cctv' && DIRS.length > 1;
  for (const s of armMarkers) s.visible = !cctvNow;
  if (cctvNow) {
    renderer.setScissorTest(true);
    const W = renderer.domElement.width, H = renderer.domElement.height;
    const cw = Math.ceil(W / 2), ch = Math.ceil(H / Math.ceil(DIRS.length / 2));
    DIRS.forEach((dir, i) => {
      const x = (i % 2) * cw, yTop = Math.floor(i / 2) * ch, y = H - yTop - ch;   // GL origin = bottom-left
      renderer.setViewport(x, y, cw, ch);
      renderer.setScissor(x, y, cw, ch);
      poleCams[dir].aspect = cw / ch;
      poleCams[dir].updateProjectionMatrix();
      renderer.render(scene, poleCams[dir]);
    });
    renderer.setScissorTest(false);
    renderer.setViewport(0, 0, W, H);
  } else {
    renderer.render(scene, camera);
  }
  if (LIVE) {
    drawOverlay();
    if (mctx) {                                    // sample the live queue for the mode-coloured chart
      qSampleAcc += dt;
      if (qSampleAcc >= 0.75) { qSampleAcc = 0; qHist.push({ v: queuedNow(), on: systemOn }); if (qHist.length > 120) qHist.shift(); drawChart(); }
    }
    sendAcc += dt;
    if (sendAcc >= 0.15 && ready) { sendAcc = 0; streamFrame(); }   // ~6.7Hz: boxes track motion instead of trailing it
  }
  if (CAP && ready) captureTick(dt);

  // public state for dashboards / debugging: everything the system knows, one object
  window.RELAY = {
    mode: LIVE ? (systemOn ? 'relay' : 'fixed') : 'sim',
    phase: hud.phase.textContent, counts: c, cars: cars.length,
    queued: queuedNow(), waitPerSec: modeT > 3 ? +(modeWait / modeT).toFixed(2) : null,
    peds: peds.waiting(),
    topo: TOPO, lanes: LANES,
  };
  requestAnimationFrame(tick);
}

// ─────────────────────────── explainer + embed bridge ───────────────────────────
// "what am I looking at" card — the first-time-viewer legibility fix, shown once per browser.
function buildExplainer() {
  const KEY = 'relay_explainer_seen_v1';
  const wrap = document.createElement('div');
  wrap.className = 'relay-explain';
  const third = LIVE
    ? '<b>Toggle R.E.L.A.Y. off</b> <span>to watch the dumb fixed timer let the queue pile up — then flip it back on.</span>'
    : '<b>This is a plain junction.</b> <span>Open the live A/B to watch R.E.L.A.Y. take the wheel and clear the queue.</span>';
  wrap.innerHTML =
    '<div class="relay-card glass">' +
      '<h2>What am I looking at?</h2>' +
      '<div class="lede">R.E.L.A.Y. — an AI traffic signal that watches the camera and gives green to whoever actually needs it.</div>' +
      '<ul>' +
        '<li><span class="ic">📹</span><div><b>It reads the camera.</b> <span>A vision model counts the vehicles waiting on every approach, live.</span></div></li>' +
        '<li><span class="ic">🟢</span><div><b>Green follows the queue.</b> <span>Time goes where the cars are — never to an empty lane.</span></div></li>' +
        '<li><span class="ic">🚑</span><div>' + third + '</div></li>' +
      '</ul>' +
      '<button class="go">Got it — show me</button>' +
    '</div>';
  document.body.appendChild(wrap);
  const open = () => { wrap.style.display = 'flex'; requestAnimationFrame(() => wrap.classList.add('show')); };
  const close = () => { wrap.classList.remove('show'); localStorage.setItem(KEY, '1'); setTimeout(() => { wrap.style.display = 'none'; }, 260); };
  wrap.querySelector('.go').onclick = close;
  wrap.onclick = e => { if (e.target === wrap) close(); };

  const help = button('?', open);
  help.className = 'relay-btn relay-help glass';
  help.title = 'What am I looking at?';
  document.body.appendChild(help);

  let seen = false;
  try { seen = !!localStorage.getItem(KEY); } catch {}
  if (!seen) open();
}

// compare.html embeds two of these sims; bridge stats out and scenario commands in.
function startEmbedBridge() {
  const N = DIRS.includes('N') ? 'N' : DIRS[0];
  setInterval(() => {
    if (window.RELAY) { try { parent.postMessage({ who: location.search, ...window.RELAY }, '*'); } catch {} }
  }, 500);
  addEventListener('message', e => {
    const cmd = (e.data && e.data.cmd) || '';
    if (cmd === 'amb') addCar(N, -START, 'ambulance');
    else if (cmd === 'surge') { for (let i = 0; i < 10; i++) setTimeout(() => addCar(N, -START, Math.random() < 0.65 ? 'motorcycle' : 'car'), i * 120); }
    else if (cmd === 'reset') location.reload();
  });
}

let peds = { tick: () => {}, crossers: () => [], waiting: () => ({}) };
loadModels().then(async () => {
  const loadGLB = url => new Promise((res, rej) => loader.load(url, g => res(g.scene), undefined, rej));
  peds = await initPeds({ THREE, scene, DIRS, APPROACH, ROAD_HALF, ZEBRA, signalOf, loadGLB, cloneSkinned });
}).then(() => {
  injectStyles();
  for (let i = 0; i < 28; i++) addCar(DIRS[(Math.random() * DIRS.length) | 0], -START + Math.random() * (START - 6));
  if (!EMBED) scenarioPanel();
  if (LIVE) { buildLiveUI(); connectWS(); }
  if (EMBED) startEmbedBridge();
  else if (!CAP) buildExplainer();
  if (camMode === 'cctv') cctvLabels(true);
  document.getElementById('loading')?.remove();
  clock.start();
  tick();
}).catch(e => {                                    // surface startup failures instead of hanging on "loading"
  console.error('startup failed:', e);
  const el = document.getElementById('loading');
  if (el) el.textContent = '✗ startup error: ' + (e && e.message || e);
});

addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
  sizeOverlay();
});
