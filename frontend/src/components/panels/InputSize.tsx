import { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { useStore } from '@/store'
import { api } from '@/utils/api'
import { openHelp } from '@/components/help/HelpPanel'

const gb = (n: number) => n >= 2 ** 30 ? `${(n / 2 ** 30).toFixed(1)} GB` : `${Math.max(1, Math.round(n / 2 ** 20))} MB`

/** Before running: how big this node's input is and, for an engine that loads
 *  its inputs whole (pandas), how much memory that takes, with a warning when
 *  it won't fit. From the inputs' cached frames, so it appears once they ran. */
export function InputSize({ parentIds, loadsWhole }: { parentIds: string[]; loadsWhole: boolean }) {
  const { activeProject, running } = useStore()
  const [est, setEst] = useState<Awaited<ReturnType<typeof api.execute.estimate>> | null>(null)
  const key = parentIds.join(',')

  useEffect(() => {
    if (!activeProject || !parentIds.length || running) return
    let live = true
    api.execute.estimate(activeProject.id, parentIds).then((e) => { if (live) setEst(e) }).catch(() => setEst(null))
    return () => { live = false }
  }, [activeProject?.id, key, running])

  if (!est || !est.inputs.length) return null
  const tooBig = loadsWhole && est.room != null && est.load_bytes > est.room
  return (
    <div className={`rounded-lg border p-2 text-xs space-y-1 shrink-0 ${tooBig ? 'border-amber-500/40 bg-amber-950/30' : 'border-canvas-border'}`}>
      <div className="text-muted">
        Input: {est.rows.toLocaleString()} rows
        {loadsWhole && <> · about {gb(est.load_bytes)} in pandas</>}
      </div>
      {tooBig && (
        <div className="flex gap-1.5 text-amber-100">
          <AlertTriangle size={13} className="shrink-0 mt-0.5 text-amber-400" />
          <span>
            Too large for this engine: it loads the whole input, and only {gb(est.room!)} is {est.room_why}.
            The run will stop before loading. Use a <b>Polars</b> or <b>DuckDB</b> node: both work on data larger
            than memory.{' '}
            <button className="underline text-amber-200 hover:text-white" onClick={() => openHelp('engine:polars')}>How</button>
          </span>
        </div>
      )}
    </div>
  )
}
