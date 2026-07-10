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
//   initPeds({ THREE, scene, DIRS, APPROACH, ROAD_HALF, ZEBRA, signalOf })
//   → returns { tick(dt), crossers(), waiting() }.

const MAX_PEDS = 26;
const TARGET = 20;                // steady-state population the spawner drifts toward
const ENTRY_WINDOW = 3;           // s at the START of a red during which crossings admit entrants —
                                  // real crosswalks stop admitting (flashing don't-walk) well before
                                  // vehicles go green, or stragglers wall the whole green shut
const TURN_RATE = 5;              // rad/s — people pivot fast but never snap
const STRIDE = 0.75;              // m per full step pair — sets leg swing frequency from real speed

const SKIN = [0xc68863, 0x8d5524, 0xe0ac69, 0xf1c27d, 0x6b4423];
const SHIRT = [0xb23a48, 0x2e6f95, 0x3d8361, 0xc9a227, 0x7d5ba6, 0xd67d3e, 0x4a6fa5, 0x9e2b25];
const PANTS = [0x2b3a55, 0x4a4e69, 0x3c3c3c, 0x5c4033, 0x1f3d2b, 0x6e7f80];

export async function initPeds(ctx) {
  const { THREE, scene, DIRS, APPROACH, ROAD_HALF, ZEBRA, signalOf } = ctx;
  const R = ROAD_HALF;
  const zebraMid = (ZEBRA.from + ZEBRA.to) / 2;
  const rand = (a, b) => a + Math.random() * (b - a);
  const pick = arr => arr[(Math.random() * arr.length) | 0];

  // shared unit-box geometry; every body part is a scaled instance of it.
  // limb geometry hangs from its pivot (top at y=0) so rotation.x swings it like a real joint.
  const boxGeo = new THREE.BoxGeometry(1, 1, 1);
  const limbGeo = boxGeo.clone().translate(0, -0.5, 0);

  function makePerson() {
    const H = rand(1.5, 1.85);                            // full height, metres
    const legH = 0.48 * H, torsoH = 0.36 * H, headS = 0.16 * H;
    const build = rand(0.85, 1.2);                        // shoulder width factor
    const mats = {
      skin: new THREE.MeshStandardMaterial({ color: pick(SKIN), roughness: 0.9 }),
      shirt: new THREE.MeshStandardMaterial({ color: pick(SHIRT), roughness: 0.9 }),
      pants: new THREE.MeshStandardMaterial({ color: pick(PANTS), roughness: 0.9 }),
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

  // walk-man indicators: two stacked dots on a stub pole at one end of each zebra
  const walkBulbs = [];
  for (const c of crossings) {
    const g = new THREE.Group();
    const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.08, 2.6, 6),
      new THREE.MeshStandardMaterial({ color: 0x2b2f33 }));
    pole.position.y = 1.3;
    g.add(pole);
    const mk = (y) => {
      const m = new THREE.Mesh(new THREE.SphereGeometry(0.16, 10, 10),
        new THREE.MeshStandardMaterial({ color: 0x181b20, emissive: 0x181b20 }));
      m.position.y = y;
      g.add(m);
      return m;
    };
    const red = mk(2.75), green = mk(2.4);
    const [x, z] = c.at(c.to);
    g.position.set(x, 0, z);
    scene.add(g);
    walkBulbs.push({ c, red, green });
  }

  // walkable ground: the kerb-side walkway band on each corner slab — between the curb lip
  // (|perp| > R+2) and the tree trunks at R+6 (band stops at R+5.2 so nobody clips a trunk).
  // Each corner is an L: a strip along the NS road + a strip along the EW road.
  const BAND = [R + 2.2, R + 5.2], REACH = R + 30;
  function wanderPoint(sx, sz) {
    // pick one leg of the corner's L at random, then a point inside it
    const nearBand = rand(BAND[0], BAND[1]), far = rand(BAND[0], REACH);
    return Math.random() < 0.5 ? [sx * nearBand, sz * far] : [sx * far, sz * nearBand];
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

  function spawn(instant = false) {
    if (peds.length >= MAX_PEDS || !crossings.length) return;
    const sx = Math.random() < 0.5 ? -1 : 1, sz = Math.random() < 0.5 ? -1 : 1;
    // enter from the far end of a walkway leg (instant prefill scatters across the whole band)
    const [wx, wz] = wanderPoint(sx, sz);
    const start = instant ? [wx, wz]
      : (Math.random() < 0.5 ? [sx * rand(BAND[0], BAND[1]), sz * REACH] : [sx * REACH, sz * rand(BAND[0], BAND[1])]);
    const body = makePerson();
    const p = {
      x: start[0], z: start[1], rotY: Math.random() * Math.PI * 2,
      speed: rand(0.9, 1.6), crossSpeed: rand(1.9, 2.6), react: rand(0.1, 0.8),
      gaitAmp: rand(0.85, 1.15), phase: Math.random() * 7, swing: 0, t: 0, seed: rand(0, 9),
      state: 'stroll', target: [wx, wz], legT: 0, pauseT: 0, waitT: 0,
      legs: rand(2, 6) | 0,                              // wander legs before seeking a crossing
      mesh: body.g, body,
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

  let spawnAcc = 0, spawnNext = rand(0.8, 2.2);
  function tick(dt) {
    spawnAcc += dt;
    if (spawnAcc > spawnNext) {
      spawnAcc = 0; spawnNext = rand(0.8, 2.2);
      if (peds.length < TARGET) spawn();
    }

    for (const c of crossings) {
      c.redT = signalOf(c.dir) === 'red' ? (c.redT || 0) + dt : 0;
    }
    for (const { c, red, green } of walkBulbs) {
      const walk = c.redT > 0 && c.redT < ENTRY_WINDOW;   // walk-man shows the ADMISSION window, not the whole red
      red.material.emissive.setHex(walk ? 0x181b20 : 0xff3b30);
      red.material.color.setHex(walk ? 0x181b20 : 0xff3b30);
      green.material.emissive.setHex(walk ? 0x34c759 : 0x181b20);
      green.material.color.setHex(walk ? 0x34c759 : 0x181b20);
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
            p.kerbOff = rand(-0.9, 0.9);
            const kerbPerp = cr.end < 0 ? cr.c.from - 0.6 : cr.c.to + 0.6;
            p.target = cr.c.at(kerbPerp, p.kerbOff);
          } else if (Math.random() < 0.35) {
            p.pauseT = rand(1, 4);
          } else {
            p.target = wanderPoint(...cornerOf(p.x, p.z));
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
        // step off after a personal reaction delay, but never so late the window has shut
        if (c.redT > p.react && c.redT < ENTRY_WINDOW) {
          p.state = 'crossing'; p.legT = 0;
          p.perp = p.cross.end < 0 ? c.from : c.to;
          p.dirSign = p.cross.end < 0 ? 1 : -1;
        }
      } else if (p.state === 'crossing') {
        const c = p.cross.c;
        // the light turning while mid-road breaks a walk into a jog — vehicles still hold (crossers()
        // makes every person on the zebra a hard obstacle), but nobody saunters against traffic
        const caught = c.redT === 0;
        const v = caught ? p.crossSpeed * 1.6 : p.crossSpeed;
        p.perp += p.dirSign * v * dt;
        const [x, z] = c.at(p.perp, p.kerbOff);
        faceToward(p, angleTo(x - p.x, z - p.z), dt);
        const moved = Math.hypot(x - p.x, z - p.z);
        p.x = x; p.z = z;
        p.mesh.position.x = x; p.mesh.position.z = z;
        animateGait(p, moved, dt);
        const arrived = p.dirSign > 0 ? p.perp >= c.to + 0.6 : p.perp <= c.from - 0.6;
        if (arrived) {
          p.state = 'stroll'; p.legT = 0;
          p.legs = rand(1, 4) | 0;                        // a couple of legs on the far side, then repeat or leave
          p.target = wanderPoint(...cornerOf(p.x, p.z));
          if (Math.random() < 0.4) { p.state = 'leaving'; p.target = leavePoint(p); }
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
    return Math.random() < 0.5 ? [sx * rand(BAND[0], BAND[1]), sz * REACH] : [sx * REACH, sz * rand(BAND[0], BAND[1])];
  }

  return {
    tick,
    // people currently ON the road — vehicles treat them as hard obstacles
    crossers: () => peds.filter(p => p.state === 'crossing' && Math.abs(p.perp) <= R + 1.0)
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
