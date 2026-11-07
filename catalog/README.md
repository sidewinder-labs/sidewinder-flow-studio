# Plugin catalog

The service the **Plugins** menu talks to: a Cloudflare Worker (`worker/`)
in front of an R2 bucket holding the signed catalog and the plugin packs.
The Worker holds no secrets: packs and the catalog are signed in CI, and it
checks Enterprise licences with their public keys.

```
GET /catalog.json                          the signed catalog (cached 5 minutes)
GET /packs/<id>/<version>/<platform>.zip   a pack listed in the catalog;
                                           Enterprise packs need "Authorization: Licence <key>"
```

## One-time setup (not done yet)

1. **R2 bucket**: `npx wrangler r2 bucket create sfs-plugin-packs`
2. **Worker**: in `worker/`, set the route in `wrangler.toml`
   (`plugins.sidewinderlabs.dev` as a custom domain) and `npx wrangler deploy`.
   `LICENCE_PUBLIC_KEYS` must match `PUBLIC_KEYS` in sfs-enterprise's
   `licensing.py`.
3. **GitHub environment** `plugin-signing` in both `sidewinder-flow-studio`
   and `sfs-enterprise`, with a required reviewer and:
   - secret `PLUGIN_SIGNING_KEY`: the plugin-signing private key (PEM)
   - variable `PLUGIN_SIGNING_KID`: its key id (`2026-11`)
   - secret `CLOUDFLARE_API_TOKEN`: an API token with R2 write access to the bucket
   - variable `CLOUDFLARE_ACCOUNT_ID`
4. After pasting the key into the secret, delete the local copy
   (`~/.config/sidewinder-labs/keys/plugins-2026-11.pem`) and keep an offline backup.
5. Run **Actions → Plugin packs** with the current SFS version to publish the
   first catalog.

SFS reads the catalog from `https://plugins.sidewinderlabs.dev` by default
(`SFS_CATALOG_URL` to change it).

## Withdrawing a version

`python -m app.packs.tools catalog --previous <current> --withdraw tm1==0.1.0 …`
publishes a catalog without it. The Worker stops serving packs not in the
catalog, and SFS refuses catalogs older than the newest it has seen, so the old
catalog can't bring it back.

## Tests

`node worker/test.mjs` runs the Worker against an in-memory bucket: catalog,
community download, withdrawn pack, Enterprise download without, with a valid,
an expired, a wrong-feature and a tampered licence.
