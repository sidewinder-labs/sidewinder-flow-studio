/** Save JSON as a file in the browser (Downloads, or the desktop app's save dialog). */
export function downloadJson(filename: string, data: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2) + '\n'], { type: 'application/json' }))
  const a = Object.assign(document.createElement('a'), { href: url, download: filename })
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/** A file name from a flow or project name: `Sales load` → `Sales load.sfs.json`. */
export const bundleName = (name: string) => `${(name || 'export').replace(/[\\/:*?"<>|]+/g, '_').trim()}.sfs.json`

/** Ask for one local file and read it as JSON. */
export function pickJson(): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const input = Object.assign(document.createElement('input'), { type: 'file', accept: '.json,application/json' })
    input.onchange = async () => {
      const f = input.files?.[0]
      if (!f) return
      try { resolve(JSON.parse(await f.text())) } catch { reject(new Error(`${f.name} isn't a JSON file`)) }
    }
    input.click()
  })
}
