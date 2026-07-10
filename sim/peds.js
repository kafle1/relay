// Pedestrians + pedestrian signals for the R.E.L.A.Y. junction sim.
// A living sidewalk population: people stroll the kerb-side walkways, pause, drift to a zebra,
// wait for the walk window, cross (jogging if the light turns on them), and wander off. Every ped
// rolls its own height, build, pace, outfit and reaction time — no two move in step. Light
// separation steering keeps a crowd from stacking into one body.
//
// People are procedural box-figures (head/torso/arms/legs) in the sim's Kenney-box art style,
// gait-animated from actual displacement — legs swing exactly as fast as the ground goes by.
// (The bundled skinned GLB renders collapsed in three.js — its rig packs all verts in a 5cm ball —
// so boxes aren't a fallback, they're the reliable primary.)
//
// Wire-up (one line in main.js after the world is built):
//   initPeds({ THREE, scene, DIRS, APPROACH, ROAD_HALF, ZEBRA, signalOf,
//              carGap,   // (x,z) → distance to nearest vehicle body — peds yield to steel
//              rng })    // (stream, idx) → PRNG factory for the seeded paired A/B
//   → returns { tick(dt), crossers(), waiting() }.

const MAX_PEDS = 14;
const TARGET = 10;                // steady-state population the spawner drifts toward — kept small:
                                  // every crosser briefly holds a lane, and a large crowd's walk
                                  // demand starts steering the signals instead of the traffic
const ENTRY_WINDOW = 3;           // s per walk window during which crossings admit entrants —
                                  // real crosswalks stop admitting (flashing don't-walk) well before
                                  // vehicles go green, or stragglers wall the whole green shut
const WALK_PERIOD = 35;           // a long red re-opens a walk window every 35s (pelican-style):
                                  // start-of-red-only admission stranded everyone who missed the
                                  // first 3s for the WHOLE red — under adaptive control the busy
                                  // road's red can run 30-90s and the stuck backlog's aging hijacked
                                  // the controller (the live A/B inversion). 35s, not 20: a normal
                                  // adaptive minor-road service lasts ~15-25s of major-road red, so
                                  // one red admits ONE batch — at 20s a second window opened right
                                  // as the major road got green back and the straggler stream
                                  // walked all over its discharge. Fixed timer's reds are ~16s,
                                  // so its behavior is untouched by the re-window either way.
const CLEARANCE = 1.6;            // s of red before the first walk window: a vehicle that crossed
                                  // the line at yellow-end is still ON the zebra when red begins —
                                  // opening the window instantly walked people into its side
const TURN_RATE = 5;              // rad/s — people pivot fast but never snap
const STRIDE = 0.75;              // m per full step pair — sets leg swing frequency from real speed

const SKIN = [0xc68863, 0x8d5524, 0xe0ac69, 0xf1c27d, 0x6b4423];
const SHIRT = [0xb23a48, 0x2e6f95, 0x3d8361, 0xc9a227, 0x7d5ba6, 0xd67d3e, 0x4a6fa5, 0x9e2b25];
const PANTS = [0x2b3a55, 0x4a4e69, 0x3c3c3c, 0x5c4033, 0x1f3d2b, 0x6e7f80];

export async function initPeds(ctx) {
  const { THREE, scene, DIRS, APPROACH, ROAD_HALF, ZEBRA, signalOf,
          emergencyOf = () => false, turningAcross = () => false } = ctx;
  const carGap = ctx.carGap || (() => Infinity);          // point→nearest-vehicle-surface distance
  const evRng = ctx.rng || (() => Math.random);           // seeded event streams for the paired A/B
  const R = ROAD_HALF;
  const zebraMid = (ZEBRA.from + ZEBRA.to) / 2;
  const rand = (a, b, r = Math.random) => a + r() * (b - a);
  const pick = (arr, r = Math.random) => arr[(r() * arr.length) | 0];

  // shared unit-box geometry; every body part is a scaled instance of it.
  // limb geometry hangs from its pivot (top at y=0) so rotation.x swings it like a real joint.
  const boxGeo = new THREE.BoxGeometry(1, 1, 1);
  const limbGeo = boxGeo.clone().translate(0, -0.5, 0);

  function makePerson(r = Math.random) {
    const H = rand(1.5, 1.85, r);                         // full height, metres
    const legH = 0.48 * H, torsoH = 0.36 * H, headS = 0.16 * H;
    const build = rand(0.85, 1.2, r);                     // shoulder width factor
    const mats = {
      skin: new THREE.MeshStandardMaterial({ color: pick(SKIN, r), roughness: 0.9 }),
      shirt: new THREE.MeshStandardMaterial({ color: pick(SHIRT, r), roughness: 0.9 }),
      pants: new THREE.MeshStandardMaterial({ color: pick(PANTS, r), roughness: 0.9 }),
    };
    const g = new THREE.Group();
    const torso = new THREE.Mesh(boxGeo, mats.shirt);
    torso.scale.set(0.34 * build, torsoH, 0.2);
    torso.position.y = legH + torsoH / 2;
    const head = new THREE.Mesh(boxGeo, mats.skin);
    head.scale.setScalar(headS);
    head.position.y = legH + torsoH + headS * 0.62;
    const limb = (mat, w, h) => { const m = new THREE.Mesh(limbGeo, mat); m.scale.set(w, h, w); return m; };
    const hipY = legH, hipX = 0.09 * build, shY = legH + torsoH * 0.92, shX = (0.34 * build) / 2 + 0.045;
    const legL = limb(mats.pants, 0.11, legH), legR = limb(mats.pants, 0.11, legH);
    legL.position.set(-hipX, hipY, 0); legR.position.set(hipX, hipY, 0);
    const armL = limb(mats.shirt, 0.09, torsoH * 0.9), armR = limb(mats.shirt, 0.09, torsoH * 0.9);
    armL.position.set(-shX, shY, 0); armR.position.set(shX, shY, 0);
    g.add(torso, head, legL, legR, armL, armR);
    return { g, legL, legR, armL, armR, torso, head, H, hipY: legH, mats };
  }

  // gait driven by real displacement: phase advances with distance covered, so legs never
  // moonwalk — a faster ped simply strides faster. Idle blends the swing back to standing.
  function animateGait(p, moved, dt) {
    const b = p.body;
    if (moved > 1e-4) {
      p.phase += (moved / STRIDE) * Math.PI * 2;
      p.swing = Math.min(1, p.swing + dt * 4);
    } else {
      p.swing = Math.max(0, p.swing - dt * 5);
    }
    const amp = 0.55 * p.swing * p.gaitAmp;
    const s = Math.sin(p.phase);
    b.legL.rotation.x = s * amp;
    b.legR.rotation.x = -s * amp;
    b.armL.rotation.x = -s * amp * 0.7;
    b.armR.rotation.x = s * amp * 0.7;
    // support bob + idle breathing sway — tiny, but it reads as alive at any distance
    p.t += dt;
    const bob = Math.abs(Math.cos(p.phase)) * 0.035 * p.swing;
    p.mesh.position.y = 0.02 + bob;
    b.torso.rotation.z = p.swing ? 0 : Math.sin(p.t * 1.7 + p.seed) * 0.025;
    b.head.rotation.y = Math.sin(p.t * 0.6 + p.seed) * (p.swing ? 0.05 : 0.3);
  }

  // one crossing per approach arm: pedestrians cross that arm's road over its zebra.
  // safe to cross while the arm's vehicle signal is red (vehicles held at the stop line).
  const crossings = DIRS.map(dir => {
    const a = APPROACH[dir];
    const along = a.sign * -zebraMid;                     // zebra band centre on the travel axis
    const from = -R - 1.6, to = R + 1.6;                  // kerb to kerb
    const at = (perp, off = 0) => a.axis === 'z' ? [perp, along + off] : [along + off, perp];
    return { dir, a, along, from, to, at };
  });

  // proper pedestrian signals: a housing with a red STANDING man over a green WALKING man,
  // one head at EACH end of every zebra (like a real signalized crossing). White-on-transparent
  // pictogram textures tinted by material color — lit saturated, unlit near-black.
  // 128px canvas + double-weight strokes: at 64px the little man dissolved into a blob from any
  // camera distance — the figures must read as STAND vs WALK at a glance, not as two dots.
  function manTexture(walking) {
    const c = document.createElement('canvas');
    c.width = c.height = 128;
    const g = c.getContext('2d');
    g.strokeStyle = g.fillStyle = '#fff';
    g.lineCap = 'round';
    const line = (x1, y1, x2, y2, w = 13) => {
      g.lineWidth = w; g.beginPath(); g.moveTo(x1, y1); g.lineTo(x2, y2); g.stroke();
    };
    g.beginPath(); g.arc(64, 26, 14, 0, Math.PI * 2); g.fill();           // head
    if (walking) {
      line(66, 42, 58, 76);                                               // leaning torso
      line(58, 76, 38, 108); line(58, 76, 80, 104);                       // striding legs
      line(64, 50, 44, 68); line(64, 50, 82, 62, 11);                     // swinging arms
    } else {
      line(64, 42, 64, 76);                                               // upright torso
      line(64, 76, 54, 112); line(64, 76, 74, 112);                       // legs together
      line(64, 48, 52, 72, 11); line(64, 48, 76, 72, 11);                 // arms down
    }
    return new THREE.CanvasTexture(c);
  }
  const standTex = manTexture(false), walkTex = manTexture(true);
  const LIT = { red: 0xff3b30, green: 0x3dff6e }, DIM = { red: 0x220c0a, green: 0x0a1c10 };
  function pedHead() {
    const g = new THREE.Group();
    const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.09, 2.5, 6),
      new THREE.MeshStandardMaterial({ color: 0x2b2f33, roughness: 0.7, metalness: 0.4 }));
    pole.position.y = 1.25;
    // bigger housing + a matte black face plate behind the lenses: the lit man needs a dark
    // field to pop against, exactly like the vehicle heads' hoods
    const housing = new THREE.Mesh(new THREE.BoxGeometry(0.72, 1.3, 0.18),
      new THREE.MeshStandardMaterial({ color: 0x0c0e11, roughness: 0.55 }));
    housing.position.y = 2.98;
    g.add(pole, housing);
    // toneMapped:false — same trick as the vehicle heads: ACES washed lit lenses to pastel
    const lens = (tex, y) => {
      const m = new THREE.MeshBasicMaterial({ map: tex, transparent: true, side: THREE.DoubleSide, toneMapped: false });
      const p = new THREE.Mesh(new THREE.PlaneGeometry(0.58, 0.58), m);
      p.position.set(0, y, 0.095);
      const back = p.clone(); back.position.z = -0.095; back.rotation.y = Math.PI;   // reads from both sides
      g.add(p, back);
      return m;
    };
    return { g, red: lens(standTex, 3.28), green: lens(walkTex, 2.68) };
  }
  const walkBulbs = [];
  for (const c of crossings) {
    for (const end of [c.from, c.to]) {                    // a head at BOTH kerbs of the zebra
      const head = pedHead();
      const [x, z] = c.at(end + Math.sign(end) * 0.5);
      head.g.position.set(x, 0, z);
      // face along the zebra so waiting peds look straight at their signal
      head.g.rotation.y = c.a.axis === 'z' ? Math.PI / 2 : 0;
      scene.add(head.g);
      walkBulbs.push({ c, red: head.red, green: head.green });
    }
  }
  // one walk-window truth per crossing per tick: admission and the signal heads must agree.
  // Windows count from red+CLEARANCE, never raw red: the junction must drain its last legal
  // vehicle off the zebra before the green man invites anyone onto it.
  const walkState = c => {
    const t = (c.redT || 0) - CLEARANCE;
    const winT = t % WALK_PERIOD;                          // negative until clearance passes → no walk
    const walk = t > 0 && winT < ENTRY_WINDOW;
    return { winT, walk, flash: walk && winT > ENTRY_WINDOW - 1 };   // last 1s: flashing green man
  };

  // walkable ground: the kerb-side walkway band on each corner slab — between the curb lip
  // (|perp| > R+2) and the tree trunks at R+6 (band stops at R+5.2 so nobody clips a trunk).
  // Each corner is an L: a strip along the NS road + a strip along the EW road.
  const BAND = [R + 2.2, R + 5.2], REACH = R + 30;
  function wanderPoint(sx, sz, r = Math.random) {
    // pick one leg of the corner's L at random, then a point inside it
    const nearBand = rand(BAND[0], BAND[1], r), far = rand(BAND[0], REACH, r);
    return r() < 0.5 ? [sx * nearBand, sz * far] : [sx * far, sz * nearBand];
  }
  const cornerOf = (x, z) => [Math.sign(x) || 1, Math.sign(z) || 1];

  // the zebra end nearest a corner: crossing ends live at perp ±(R+1.6) beside the slabs
  function nearestCrossing(x, z) {
    let best = null, bestD = Infinity;
    for (const c of crossings) for (const end of [c.from, c.to]) {
      const [ex, ez] = c.at(end);
      const d = (ex - x) ** 2 + (ez - z) ** 2;
      if (d < bestD) { bestD = d; best = { c, end }; }
    }
    return best;
  }

  const peds = [];
  if (typeof window !== 'undefined') window.PEDS = peds;   // debug/inspection handle — read-only use
  const angleTo = (dx, dz) => Math.atan2(dx, dz);

  let pedSeq = 0;                                        // event index → per-ped seeded stream
  function spawn(instant = false) {
    if (peds.length >= MAX_PEDS || !crossings.length) return;
    const r = evRng('ped', pedSeq++);                    // this ped's OWN stream: same person,
    const sx = r() < 0.5 ? -1 : 1, sz = r() < 0.5 ? -1 : 1;   // same errand, in both A/B panels
    // enter from the far end of a walkway leg (instant prefill scatters across the whole band)
    const [wx, wz] = wanderPoint(sx, sz, r);
    const start = instant ? [wx, wz]
      : (r() < 0.5 ? [sx * rand(BAND[0], BAND[1], r), sz * REACH] : [sx * REACH, sz * rand(BAND[0], BAND[1], r)]);
    const body = makePerson(r);
    const p = {
      x: start[0], z: start[1], rotY: r() * Math.PI * 2,
      speed: rand(0.9, 1.6, r), crossSpeed: rand(1.9, 2.6, r), react: rand(0.1, 0.8, r),
      gaitAmp: rand(0.85, 1.15, r), phase: r() * 7, swing: 0, t: 0, seed: rand(0, 9, r),
      state: 'stroll', target: [wx, wz], legT: 0, pauseT: 0, waitT: 0,
      legs: rand(2, 6, r) | 0,                           // wander legs before seeking a crossing
      r, mesh: body.g, body,
    };
    body.g.position.set(p.x, 0.02, p.z);
    body.g.rotation.y = p.rotY;
    scene.add(body.g);
    peds.push(p);
  }
  for (let i = 0; i < TARGET * 0.7; i++) spawn(true);     // day starts with people already out

  // move toward (tx,tz) with smooth heading + neighbour separation. Returns remaining distance.
  function step(p, tx, tz, speed, dt) {
    let dx = tx - p.x, dz = tz - p.z;
    const dist = Math.hypot(dx, dz);
    for (const q of peds) {                               // soft shove off anyone closer than 0.7m
      if (q === p) continue;
      const ox = p.x - q.x, oz = p.z - q.z, d = Math.hypot(ox, oz);
      if (d > 1e-4 && d < 0.7) { dx += (ox / d) * (0.7 - d) * 2; dz += (oz / d) * (0.7 - d) * 2; }
    }
    const want = angleTo(dx, dz);
    let diff = want - p.rotY;
    diff = Math.atan2(Math.sin(diff), Math.cos(diff));
    p.rotY += Math.max(-TURN_RATE * dt, Math.min(TURN_RATE * dt, diff));
    const v = speed * Math.min(1, dist / 0.4);            // arrival ease-out, no overshoot jitter
    const mx = Math.sin(p.rotY) * v * dt, mz = Math.cos(p.rotY) * v * dt;
    p.x += mx; p.z += mz;
    p.mesh.position.x = p.x; p.mesh.position.z = p.z;
    p.mesh.rotation.y = p.rotY;
    animateGait(p, Math.hypot(mx, mz), dt);
    return dist;
  }

  const faceToward = (p, ang, dt) => {
    let diff = ang - p.rotY;
    diff = Math.atan2(Math.sin(diff), Math.cos(diff));
    p.rotY += Math.max(-TURN_RATE * dt, Math.min(TURN_RATE * dt, diff));
    p.mesh.rotation.y = p.rotY;
  };

  const arrRng = evRng('pedArr', 0);                     // seeded arrival clock — paired panels agree
  let spawnAcc = 0, spawnNext = rand(0.8, 2.2, arrRng);
  function tick(dt) {
    spawnAcc += dt;
    if (spawnAcc > spawnNext) {
      spawnAcc = 0; spawnNext = rand(0.8, 2.2, arrRng);
      if (peds.length < TARGET) spawn();
    }

    for (const c of crossings) {
      // walk-safe means the zebra carries NO vehicle movement: the arm's own approach is red
      // AND the opposing approach's STRAIGHT green isn't exiting over this zebra (during a
      // split-arm green, E's through platoon sweeps W's crossing — an open walk there throttled
      // the whole green road to walking pace and inverted the live A/B). Turners still cross an
      // open walk, but they yield — that is the normal permissive interaction.
      const opp = { N: 'S', S: 'N', E: 'W', W: 'E' }[c.dir];
      const oppSweeps = DIRS.includes(opp) && signalOf(opp, 'straight') === 'green';
      // an inbound siren forces don't-walk NOW, whatever the vehicle signal says: redT held at 0
      // shuts the walk window (no step-offs, waiting peds stay on the kerb, heads show the red
      // man) and flags every mid-zebra crosser `caught` below so they sprint clear. Pedestrians
      // rank below the ambulance — the junction must never hold the siren's green for a walker.
      const siren = emergencyOf(c.dir);
      c.redT = signalOf(c.dir) === 'red' && !oppSweeps && !siren ? (c.redT || 0) + dt : 0;
    }
    const blink = (performance.now() / 280) % 2 < 1;      // shared flash clock for all heads
    for (const { c, red, green } of walkBulbs) {
      const { walk, flash } = walkState(c);               // green man = ADMISSION window, not the whole red
      red.color.setHex(walk ? DIM.red : LIT.red);
      green.color.setHex(walk && (!flash || blink) ? LIT.green : DIM.green);
    }

    for (let i = peds.length - 1; i >= 0; i--) {
      const p = peds[i];
      p.legT += dt;

      if (p.state === 'stroll') {
        if (p.pauseT > 0) {                               // window-shopping / phone check
          p.pauseT -= dt;
          animateGait(p, 0, dt);
        } else if (step(p, p.target[0], p.target[1], p.speed, dt) < 0.5 || p.legT > 25) {
          p.legT = 0;
          if (--p.legs <= 0) {                            // this ped's errand is across the road
            const [sx, sz] = cornerOf(p.x, p.z);
            const cr = nearestCrossing(sx * BAND[0], sz * BAND[0]);
            p.cross = cr;
            p.state = 'toCross';
            // queue at a jittered kerb spot, not one shared point — crowds read as crowds
            p.kerbOff = rand(-0.9, 0.9, p.r);
            const kerbPerp = cr.end < 0 ? cr.c.from - 0.6 : cr.c.to + 0.6;
            p.target = cr.c.at(kerbPerp, p.kerbOff);
          } else if (p.r() < 0.35) {
            p.pauseT = rand(1, 4, p.r);
          } else {
            p.target = wanderPoint(...cornerOf(p.x, p.z), p.r);
          }
        }
      } else if (p.state === 'toCross') {
        if (step(p, p.target[0], p.target[1], p.speed, dt) < 0.4 || p.legT > 30) {
          p.state = 'waiting'; p.legT = 0; p.waitT = 0;
          // face across the road while waiting
          const [fx, fz] = p.cross.c.at(p.cross.end < 0 ? p.cross.c.to : p.cross.c.from, p.kerbOff);
          p.face = angleTo(fx - p.x, fz - p.z);
        }
      } else if (p.state === 'waiting') {
        const c = p.cross.c;
        faceToward(p, p.face, dt);
        animateGait(p, 0, dt);
        p.waitT += dt;
        // step off after a personal reaction delay, but never so late the window has shut.
        // winT (not raw redT): every WALK_PERIOD of continuous red re-opens the window, so a
        // long adaptive red clears the whole kerb backlog instead of stranding it.
        const { winT, walk } = walkState(c);
        // never step in front of a vehicle mid-turn across this zebra: the two are separated in
        // time (turningAcross), so a walker never shares the crossing with a swinging turner and
        // the mutual-yield-then-escalate clip can't arise. A green man alone is not enough.
        if (walk && winT > p.react && !turningAcross(c.dir)) {
          // steel outranks the green man: a vehicle sitting ON the zebra at the kerb lanes
          // (spillback tail, mid-turn exit) keeps this person on the kerb until it moves
          const startPerp = p.cross.end < 0 ? c.from : c.to;
          const sgn = p.cross.end < 0 ? 1 : -1;
          const [ex, ez] = c.at(startPerp + sgn * 1.7, p.kerbOff);
          if (carGap(ex, ez) > 1.3) {
            p.state = 'crossing'; p.legT = 0;
            p.perp = startPerp;
            p.dirSign = sgn;
          }
        }
      } else if (p.state === 'crossing') {
        const c = p.cross.c;
        // the light turning while mid-road breaks a walk into a jog — vehicles still hold (crossers()
        // makes every person on the zebra a hard obstacle), but nobody saunters against traffic
        const caught = c.redT === 0;
        const v = caught ? p.crossSpeed * 1.6 : p.crossSpeed;
        const nextPerp = p.perp + p.dirSign * v * dt;
        // yield to steel mid-zebra: a vehicle occupying the path ahead (an exiting turner, a
        // spillback tail) stops the walker at 1.1m — deliberately MORE daylight than the ~0.8m
        // at which vehicles yield to walkers, so exactly one side ever waits (no mutual freeze)
        const [lx, lz] = c.at(nextPerp + p.dirSign * 0.8, p.kerbOff);
        const gap = carGap(lx, lz);
        // anti-freeze: a walker stalled against a stopped car too long threads past it (people do,
        // between halted traffic). SAFE because a car never closes on a ped — pathWalkerCap + the
        // strict SEP guard in moveCars mean the gap only ever opens as the walker edges by, never
        // shuts onto them — so this breaks a rare mutual standoff in the pedestrian's favour.
        p.blockT = gap < 1.1 ? (p.blockT || 0) + dt : 0;
        const clear = p.blockT > 1.2 ? 0.55 : 1.1;
        if (gap < clear) {
          animateGait(p, 0, dt);
        } else {
          p.perp = nextPerp;
          const [x, z] = c.at(p.perp, p.kerbOff);
          faceToward(p, angleTo(x - p.x, z - p.z), dt);
          const moved = Math.hypot(x - p.x, z - p.z);
          p.x = x; p.z = z;
          p.mesh.position.x = x; p.mesh.position.z = z;
          animateGait(p, moved, dt);
        }
        const arrived = p.dirSign > 0 ? p.perp >= c.to + 0.6 : p.perp <= c.from - 0.6;
        if (arrived) {
          p.state = 'stroll'; p.legT = 0;
          p.legs = rand(1, 4, p.r) | 0;                   // a couple of legs on the far side, then repeat or leave
          p.target = wanderPoint(...cornerOf(p.x, p.z), p.r);
          if (p.r() < 0.4) { p.state = 'leaving'; p.target = leavePoint(p); }
        }
      } else {                                            // leaving: walk off along the walkway, then despawn
        if (step(p, p.target[0], p.target[1], p.speed, dt) < 0.8 || p.legT > 40) {
          scene.remove(p.mesh);
          for (const m of Object.values(p.body.mats)) m.dispose();   // peds churn — don't leak materials
          peds.splice(i, 1);
        }
      }
    }
  }

  function leavePoint(p) {
    const [sx, sz] = cornerOf(p.x, p.z);
    const r = p.r || Math.random;
    return r() < 0.5 ? [sx * rand(BAND[0], BAND[1], r), sz * REACH] : [sx * REACH, sz * rand(BAND[0], BAND[1], r)];
  }

  return {
    tick,
    // people currently ON the road — vehicles treat them as hard obstacles. R+1.6, the kerb
    // step-off point: a person entering the zebra is steel-relevant from their very first step,
    // not only once they are a metre into the carriageway.
    // R+2.6 (was R+1.6): a person is steel-relevant from the kerb through STEP-OFF at the far side —
    // dropping them at R+1.6 un-protected them exactly where a turning car's arc sweeps the far kerb,
    // which is where the turn-into-pedestrian hits happened. Cars keep yielding until they fully clear.
    crossers: () => peds.filter(p => p.state === 'crossing' && Math.abs(p.perp) <= R + 2.6)
      .map(p => ({ dir: p.cross.c.dir, x: p.x, z: p.z })),
    // per-arm pedestrian demand for the controller: waiting count, longest wait, people on the zebra.
    // Transponder-style input (like a push button / ped detector) — sent with each live frame.
    waiting: () => {
      const out = {};
      for (const p of peds) {
        if (p.state === 'waiting') {
          const d = p.cross.c.dir, o = out[d] || (out[d] = { n: 0, wait: 0, crossing: 0 });
          o.n++; o.wait = Math.max(o.wait, +p.waitT.toFixed(1));
        } else if (p.state === 'crossing') {
          const d = p.cross.c.dir, o = out[d] || (out[d] = { n: 0, wait: 0, crossing: 0 });
          o.crossing++;
        }
      }
      return out;
    },
  };
}
