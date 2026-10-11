// Exact rational arithmetic for title math. Fractions never touch floating point.
const Q = (() => {
  const abs = (a) => (a < 0n ? -a : a);
  const gcd = (a, b) => { a = abs(a); b = abs(b); while (b) { [a, b] = [b, a % b]; } return a || 1n; };
  const make = (n, d = 1n) => {
    if (d === 0n) throw new Error("Division by zero");
    if (d < 0n) { n = -n; d = -d; }
    const g = gcd(n, d);
    return { n: n / g, d: d / g };
  };
  const mul = (a, b) => make(a.n * b.n, a.d * b.d);
  const div = (a, b) => make(a.n * b.d, a.d * b.n);
  const add = (a, b) => make(a.n * b.d + b.n * a.d, a.d * b.d);
  const sub = (a, b) => make(a.n * b.d - b.n * a.d, a.d * b.d);
  const one = make(1n), zero = make(0n);
  // Parses "3/16", "0.1875", "18.75%", "1/2 of 1/8", "1/2 x 1/8", "640", "159.86".
  function parse(text) {
    const s = String(text || "").replace(/,/g, "").trim().toLowerCase();
    if (!s) throw new Error("Enter a value");
    const parts = s.split(/\s*(?:\bof\b|\*|×|\bx\b)\s*/).filter(Boolean);
    return parts.map(parseOne).reduce(mul, one);
  }
  function parseOne(p) {
    let pct = false;
    if (p.endsWith("%")) { pct = true; p = p.slice(0, -1).trim(); }
    let q;
    const frac = p.match(/^(\d+(?:\.\d+)?)\s*\/\s*(\d+(?:\.\d+)?)$/);
    if (frac) q = div(dec(frac[1]), dec(frac[2]));
    else if (/^\d+(?:\.\d+)?$|^\.\d+$/.test(p)) q = dec(p);
    else throw new Error(`Can't read "${p}" — use 3/16, 0.1875 or 18.75%`);
    return pct ? div(q, make(100n)) : q;
  }
  function dec(str) {
    if (str.startsWith(".")) str = "0" + str;
    const [i, f = ""] = str.split(".");
    return make(BigInt(i + f), 10n ** BigInt(f.length));
  }
  // Round half-up to `places` decimals, the way most pay decks carry interests.
  function fixed(q, places = 8) {
    const scale = 10n ** BigInt(places);
    const neg = q.n < 0n;
    const n = abs(q.n);
    let v = (n * scale * 2n + q.d) / (q.d * 2n);
    const s = v.toString().padStart(places + 1, "0");
    return (neg ? "-" : "") + s.slice(0, -places) + "." + s.slice(-places);
  }
  const str = (q) => (q.d === 1n ? q.n.toString() : `${q.n}/${q.d}`);
  const gt = (a, b) => a.n * b.d > b.n * a.d;
  return { make, mul, div, add, sub, parse, fixed, str, gt, one, zero };
})();

// type: mineral | npri_fixed | npri_float | orri | wi
function titleCalc(input) {
  const acres = Q.parse(input.acres);
  const owned = Q.parse(input.owned);         // undivided share of minerals (or of the lease, for ORRI/WI: share of minerals the lease covers)
  const royalty = Q.parse(input.royalty);     // lessor's royalty in the lease
  let part = Q.one, partNote = "Tract-level (no unit) — participation 1";
  if (input.method === "acreage") {
    const inUnit = Q.parse(input.tractInUnit), unit = Q.parse(input.unitAcres);
    if (Q.gt(inUnit, unit)) throw new Error("Tract acres in the unit can't exceed unit acres");
    part = Q.div(inUnit, unit);
    partNote = `Tract participation = ${Q.str(inUnit)} ÷ ${Q.str(unit)} ac`;
  } else if (input.method === "lateral") {
    const tl = Q.parse(input.tractLateral), all = Q.parse(input.totalLateral);
    if (Q.gt(tl, all)) throw new Error("Tract lateral feet can't exceed total lateral feet");
    part = Q.div(tl, all);
    partNote = `Tract participation = ${Q.str(tl)} ÷ ${Q.str(all)} ft of completed lateral`;
  }
  const nma = Q.mul(acres, owned);
  const out = { nma, part, partNote, rows: [] };
  const eighth = Q.make(1n, 8n);
  switch (input.type) {
    case "mineral": {
      const d = Q.mul(Q.mul(owned, royalty), part);
      out.decimal = d;
      out.math = `${Q.str(owned)} minerals × ${Q.str(royalty)} royalty × participation ${Q.str(part)}`;
      out.nra = Q.div(Q.mul(nma, royalty), eighth);
      break;
    }
    case "npri_fixed": {
      const npri = Q.parse(input.burden);
      const d = Q.mul(npri, part);
      out.decimal = d;
      out.math = `Fixed NPRI ${Q.str(npri)} of production × participation ${Q.str(part)} (lease royalty doesn't change it)`;
      if (Q.gt(npri, royalty)) out.warn = "Fixed NPRI is larger than the lease royalty — the mineral owner's royalty can't carry it. Check the reservation language and Texas double-fraction case law before you book this.";
      break;
    }
    case "npri_float": {
      const npri = Q.parse(input.burden);
      const d = Q.mul(Q.mul(npri, royalty), part);
      out.decimal = d;
      out.math = `Floating NPRI ${Q.str(npri)} of royalty × ${Q.str(royalty)} lease royalty × participation ${Q.str(part)}`;
      break;
    }
    case "orri": {
      const orri = Q.parse(input.burden);
      const d = Q.mul(Q.mul(orri, owned), part);
      out.decimal = d;
      out.math = `ORRI ${Q.str(orri)} × ${Q.str(owned)} of minerals the lease covers (proportionate reduction) × participation ${Q.str(part)}`;
      break;
    }
    case "wi": {
      const wi = Q.parse(input.burden);
      const orri = input.orriBurden ? Q.parse(input.orriBurden) : Q.zero;
      const wiDec = Q.mul(Q.mul(wi, owned), part);
      const nriFactor = Q.sub(Q.sub(Q.one, royalty), orri);
      if (!Q.gt(nriFactor, Q.zero)) throw new Error("Royalty plus ORRI burdens use up the whole lease");
      out.wi = wiDec;
      out.decimal = Q.mul(wiDec, nriFactor);
      out.math = `WI ${Q.str(wi)} × ${Q.str(owned)} leased × participation ${Q.str(part)} = ${Q.fixed(wiDec)} WI; × (1 − ${Q.str(royalty)} − ${Q.str(orri)}) = NRI`;
      break;
    }
    default: throw new Error("Unknown interest type");
  }
  return out;
}

if (typeof module !== "undefined") module.exports = { Q, titleCalc };
