// Pedestrians + pedestrian signals for the R.E.L.A.Y. junction sim.
// People walk the sidewalks, wait at the zebra, and cross while their arm's vehicle signal is red.
// A small green/red "walk man" indicator sits at each crossing.
//
// Wire-up (one line in main.js after the world is built):
//   initPeds({ THREE, scene, DIRS, APPROACH, ROAD_HALF, ZEBRA, signalOf, loadGLB, cloneSkinned })
//   → returns tick(dt); call it every frame.

const WALK = 1.3;                 // m/s stroll
const CROSS = 2.2;                // m/s crossing pace — brisk urban scurry
const MAX_PEDS = 14;
const ENTRY_WINDOW = 3;           // s at the START of a red during which crossings admit entrants —
                                  // real crosswalks stop admitting (flashing don't-walk) well before
                                  // vehicles go green, or stragglers wall the whole green shut

export async function initPeds(ctx) {
  const { THREE, scene, DIRS, APPROACH, ROAD_HALF, ZEBRA, signalOf, loadGLB, cloneSkinned } = ctx;
  const R = ROAD_HALF;
  const zebraMid = (ZEBRA.from + ZEBRA.to) / 2;

  let template = null;
  try {
    const man = await loadGLB('assets/models/polypizza_pedestrian_man.glb');
    const bb = new THREE.Box3().setFromObject(man), size = new THREE.Vector3();
    bb.getSize(size);
    man.scale.setScalar(1.7 / Math.max(size.y, 0.01));
    const ctr = new THREE.Vector3();
    bb.getCenter(ctr);
    man.position.set(-ctr.x * man.scale.x, 0, -ctr.z * man.scale.z);
    template = man;
  } catch {
    return { tick: () => {}, crossers: () => [], waiting: () => ({}) };   // no model, no pedestrians — sim stays fine
  }

  // one crossing per approach arm: pedestrians cross that arm's road over its zebra.
  // safe to cross while the arm's vehicle signal is red (vehicles held at the stop line).
  const crossings = DIRS.map(dir => {
    const a = APPROACH[dir];
    const along = a.sign * -zebraMid;                     // zebra band centre on the travel axis
    const from = -R - 1.6, to = R + 1.6;                  // kerb to kerb
    const at = (perp) => a.axis === 'z' ? [perp, along] : [along, perp];
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

  const peds = [];
  function spawn() {
    if (peds.length >= MAX_PEDS || !crossings.length) return;
    const c = crossings[(Math.random() * crossings.length) | 0];
    const fromEnd = Math.random() < 0.5;
    const mesh = cloneSkinned(template);
    mesh.rotation.y = Math.random() * Math.PI * 2;
    const p = {
      c,
      perp: fromEnd ? c.from : c.to,                      // start on a kerb end of the crossing
      dirSign: fromEnd ? 1 : -1,                          // walk toward the other kerb
      state: 'waiting',                                   // waiting → crossing → done
      waitT: 0,                                           // seconds spent waiting for a walk window
      mesh,
    };
    const [x, z] = c.at(p.perp);
    mesh.position.set(x, 0.26, z);
    scene.add(mesh);
    peds.push(p);
  }

  let spawnAcc = 0;
  function tick(dt) {
    spawnAcc += dt;
    if (spawnAcc > 1.4) { spawnAcc = 0; spawn(); }        // lively sidewalks — people are part of the scene

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
      const p = peds[i], walk = p.c.redT > 0 && p.c.redT < ENTRY_WINDOW;
      if (p.state === 'waiting') {
        if (walk) {
          p.state = 'crossing';
          const [tx, tz] = p.c.at(p.perp + p.dirSign);    // face along the crossing
          p.mesh.lookAt(tx, 0.26, tz);
        } else {
          p.waitT += dt;
        }
      } else if (p.state === 'crossing') {
        p.perp += p.dirSign * CROSS * dt;
        const [x, z] = p.c.at(p.perp);
        p.mesh.position.set(x, 0.26, z);
        const arrived = p.dirSign > 0 ? p.perp >= p.c.to : p.perp <= p.c.from;
        if (arrived) p.state = 'done';
      } else {
        // stroll off the kerb and away, then leave
        p.perp += p.dirSign * WALK * dt;
        const [x, z] = p.c.at(p.perp);
        p.mesh.position.set(x, 0.26, z);
        p.ttl = (p.ttl ?? 3) - dt;
        if (p.ttl <= 0) { scene.remove(p.mesh); peds.splice(i, 1); }
      }
    }
  }

  return {
    tick,
    // people currently ON the road — vehicles treat them as hard obstacles
    crossers: () => peds.filter(p => p.state === 'crossing')
      .map(p => { const [x, z] = p.c.at(p.perp); return { dir: p.c.dir, x, z }; }),
    // per-arm pedestrian demand for the controller: waiting count, longest wait, people on the zebra.
    // Transponder-style input (like a push button / ped detector) — sent with each live frame.
    waiting: () => {
      const out = {};
      for (const p of peds) {
        if (p.state === 'done') continue;
        const d = p.c.dir, o = out[d] || (out[d] = { n: 0, wait: 0, crossing: 0 });
        if (p.state === 'waiting') { o.n++; o.wait = Math.max(o.wait, +p.waitT.toFixed(1)); }
        else o.crossing++;
      }
      return out;
    },
  };
}
