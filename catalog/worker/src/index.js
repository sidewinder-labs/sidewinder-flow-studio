// SFS plugin catalog: serves the signed catalog and the plugin packs from R2.
//
//   GET /catalog.json                          the signed index (written by CI)
//   GET /packs/<id>/<version>/<platform>.zip   a pack listed in the catalog
//
// Community packs download freely. Enterprise packs need the header
//   Authorization: Licence SFS-LICENSE-1.<payload>.<signature>
// with a licence that verifies against LICENCE_PUBLIC_KEYS, hasn't expired
// and includes the pack's licence_feature. The plugin checks the licence
// again when it registers; this only keeps enterprise packs from being
// downloaded without one.
//
// Packs not listed in the current catalog are not served (withdrawn versions
// stay unavailable even if their files remain in the bucket).

const PREFIX = "SFS-LICENSE-1.";

function b64urlDecode(s) {
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  return Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
}

function b64Decode(s) {
  return Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
}

function json(status, body, headers = {}) {
  return new Response(JSON.stringify(body), {
    status, headers: { "content-type": "application/json", ...headers },
  });
}

export async function verifyLicence(text, feature, keys, today = new Date()) {
  text = (text || "").replace(/\s+/g, "");
  if (!text.startsWith(PREFIX)) return { ok: false, error: "not an SFS licence key" };
  const cut = text.lastIndexOf(".");
  const signed = text.slice(0, cut), sig = text.slice(cut + 1);
  let payload;
  try {
    payload = JSON.parse(new TextDecoder().decode(b64urlDecode(signed.slice(PREFIX.length))));
  } catch {
    return { ok: false, error: "the licence key is damaged" };
  }
  const pub = keys[payload.kid];
  if (!pub) return { ok: false, error: `unknown licence key id ${payload.kid}` };
  const key = await crypto.subtle.importKey("raw", b64Decode(pub), { name: "Ed25519" }, false, ["verify"]);
  const valid = await crypto.subtle.verify({ name: "Ed25519" }, key, b64urlDecode(sig), new TextEncoder().encode(signed));
  if (!valid) return { ok: false, error: "the licence signature is invalid" };
  if (payload.expires && today.toISOString().slice(0, 10) > payload.expires)
    return { ok: false, error: `the licence expired on ${payload.expires}` };
  if (feature && !(payload.features || []).includes(feature))
    return { ok: false, error: `the licence doesn't include ${feature}` };
  return { ok: true, licence: payload };
}

async function loadCatalog(env) {
  const obj = await env.PACKS.get("catalog.json");
  if (!obj) return null;
  const doc = JSON.parse(await obj.text());
  return { doc, catalog: JSON.parse(new TextDecoder().decode(b64Decode(doc.payload))) };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method !== "GET" && request.method !== "HEAD") return json(405, { error: "GET only" });

    if (url.pathname === "/" || url.pathname === "/health") return json(200, { ok: true });

    if (url.pathname === "/catalog.json") {
      const obj = await env.PACKS.get("catalog.json");
      if (!obj) return json(503, { error: "no catalog published yet" });
      return new Response(obj.body, {
        headers: { "content-type": "application/json", "cache-control": "public, max-age=300" },
      });
    }

    const m = url.pathname.match(/^\/packs\/([a-z0-9][a-z0-9_-]*)\/([^/]+)\/([a-z0-9-]+)\.zip$/);
    if (!m) return json(404, { error: "not found" });
    const [, id, version, platform] = m;
    const loaded = await loadCatalog(env);
    const plugin = loaded?.catalog.plugins?.find((p) => p.id === id);
    const listed = plugin?.versions?.find((v) => v.version === version)?.packs?.find((p) => p.platform === platform);
    if (!listed) return json(404, { error: `${id} ${version} for ${platform} isn't in the catalog` });

    if (plugin.edition === "enterprise") {
      const auth = request.headers.get("authorization") || "";
      if (!auth.startsWith("Licence ")) return json(401, { error: "a valid Enterprise licence is required" });
      const keys = JSON.parse(env.LICENCE_PUBLIC_KEYS || "{}");
      const v = await verifyLicence(auth.slice("Licence ".length), plugin.licence_feature, keys);
      if (!v.ok) return json(403, { error: v.error });
    }

    const obj = await env.PACKS.get(listed.path);
    if (!obj) return json(404, { error: "pack file missing" });
    return new Response(obj.body, {
      headers: {
        "content-type": "application/zip",
        "cache-control": plugin.edition === "enterprise" ? "private, no-store" : "public, max-age=86400, immutable",
      },
    });
  },
};
