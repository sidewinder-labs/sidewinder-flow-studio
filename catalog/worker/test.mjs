// node test.mjs — the Worker against an in-memory R2 bucket and test keys.
import assert from "node:assert/strict";
import { generateKeyPairSync, sign } from "node:crypto";
import worker, { verifyLicence } from "./src/index.js";

const { publicKey, privateKey } = generateKeyPairSync("ed25519");
const pub = publicKey.export({ format: "der", type: "spki" }).subarray(-32).toString("base64");
const b64url = (b) => Buffer.from(b).toString("base64url");
const licence = (p) => {
  const signed = "SFS-LICENSE-1." + b64url(JSON.stringify({ v: 1, kid: "t", licensee: "Acme", ...p }));
  return signed + "." + b64url(sign(null, Buffer.from(signed), privateKey));
};

const catalog = { format: "sfs-catalog", generated_at: "2026-11-07T00:00:00Z", plugins: [
  { id: "tm1", edition: "community", versions: [{ version: "0.2.0", packs: [{ platform: "server-linux-x64", path: "packs/tm1/0.2.0/server-linux-x64.zip" }] }] },
  { id: "tm1_modeling", edition: "enterprise", licence_feature: "tm1_modeling",
    versions: [{ version: "0.1.0", packs: [{ platform: "desktop-windows-x64", path: "packs/tm1_modeling/0.1.0/desktop-windows-x64.zip" }] }] },
] };
const objects = {
  "catalog.json": JSON.stringify({ kid: "x", payload: Buffer.from(JSON.stringify(catalog)).toString("base64"), signature: "s" }),
  "packs/tm1/0.2.0/server-linux-x64.zip": "ZIP-COMMUNITY",
  "packs/tm1/0.1.0/server-linux-x64.zip": "ZIP-WITHDRAWN",
  "packs/tm1_modeling/0.1.0/desktop-windows-x64.zip": "ZIP-ENTERPRISE",
};
const env = {
  LICENCE_PUBLIC_KEYS: JSON.stringify({ t: pub }),
  PACKS: { async get(k) { return k in objects ? { body: objects[k], text: async () => objects[k] } : null; } },
};
const get = (path, headers = {}) => worker.fetch(new Request("https://plugins.example" + path, { headers }), env);

let r = await get("/catalog.json");
assert.equal(r.status, 200);
assert.equal(JSON.parse(await r.text()).kid, "x");

r = await get("/packs/tm1/0.2.0/server-linux-x64.zip");
assert.equal(r.status, 200); assert.equal(await r.text(), "ZIP-COMMUNITY");

r = await get("/packs/tm1/0.1.0/server-linux-x64.zip");                      // file exists, not in catalog
assert.equal(r.status, 404);

const ent = "/packs/tm1_modeling/0.1.0/desktop-windows-x64.zip";
assert.equal((await get(ent)).status, 401);
r = await get(ent, { Authorization: "Licence " + licence({ features: ["tm1_modeling"], expires: "2099-01-01" }) });
assert.equal(r.status, 200); assert.equal(await r.text(), "ZIP-ENTERPRISE");

r = await get(ent, { Authorization: "Licence " + licence({ features: ["powerbi_ops"], expires: "2099-01-01" }) });
assert.equal(r.status, 403); assert.match((await r.json()).error, /doesn't include tm1_modeling/);

r = await get(ent, { Authorization: "Licence " + licence({ features: ["tm1_modeling"], expires: "2026-01-01" }) });
assert.equal(r.status, 403); assert.match((await r.json()).error, /expired on 2026-01-01/);

const good = licence({ features: ["tm1_modeling"], expires: null });
const tampered = good.replace(/\.[^.]+\./, "." + b64url(JSON.stringify({ v: 1, kid: "t", features: ["tm1_modeling", "powerbi_ops"] })) + ".");
r = await get(ent, { Authorization: "Licence " + tampered });
assert.equal(r.status, 403); assert.match((await r.json()).error, /signature is invalid/);

assert.equal((await verifyLicence(good, "tm1_modeling", { t: pub })).ok, true);
assert.equal((await verifyLicence("junk", "x", { t: pub })).ok, false);
assert.equal((await get("/packs/tm1/..%2F..%2Fcatalog/server-linux-x64.zip")).status, 404);
console.log("worker: all checks passed");
