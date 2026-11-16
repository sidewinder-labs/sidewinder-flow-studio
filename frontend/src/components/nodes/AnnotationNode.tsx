/**
 * AnnotationNode — a resizable, colored text label for documenting flows.
 * Renders BEHIND functional nodes (negative zIndex), has no handles, and never
 * executes. Double-click to edit the text. Like NiFi's labels.
 */
import { memo, useState, useRef, useEffect } from 'react'
import { NodeResizer } from '@xyflow/react'
import type { DataNodeProps } from './nodeProps'
import { useStore } from '@/store'

export interface AnnotationData {
  text: string
  color?: string        // one of the palette keys below
  fontSize?: number
}

// Muted, translucent backgrounds so they read as "behind" annotations. Built
// from the theme's colour scales, so they suit the light theme too.
const note = (hue: string, bg = '900', alpha = 0.22) => ({
  bg: `rgb(var(--c-${hue}-${bg}) / ${alpha})`,
  border: `rgb(var(--c-${hue}-500) / 0.5)`,
  text: `rgb(var(--c-${hue}-300))`,
})
export const ANNOTATION_COLORS: Record<string, { bg: string; border: string; text: string }> = {
  slate:  note('slate', '700', 0.25),
  amber:  note('amber'),
  green:  note('emerald'),
  blue:   note('blue'),
  purple: note('violet'),
  rose:   note('rose'),
}

export const AnnotationNode = memo(({ id, data, selected }: DataNodeProps<AnnotationData>) => {
  const { activeFlowId, updateNodeData } = useStore()
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(data.text ?? 'Note')
  const areaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => { setText(data.text ?? 'Note') }, [data.text])
  useEffect(() => { if (editing) areaRef.current?.focus() }, [editing])

  const palette = ANNOTATION_COLORS[data.color ?? 'slate'] ?? ANNOTATION_COLORS.slate
  const fontSize = data.fontSize ?? 14

  const commit = () => {
    setEditing(false)
    if (activeFlowId) updateNodeData(activeFlowId, id, { text })
  }

  const cycleColor = () => {
    const keys = Object.keys(ANNOTATION_COLORS)
    const cur = keys.indexOf(data.color ?? 'slate')
    const next = keys[(cur + 1) % keys.length]
    if (activeFlowId) updateNodeData(activeFlowId, id, { color: next })
  }

  return (
    <>
      <NodeResizer
        isVisible={selected}
        minWidth={120}
        minHeight={60}
        lineClassName="!border-slate-500"
        handleClassName="!bg-slate-400 !w-2 !h-2"
      />
      <div
        className="w-full h-full rounded-lg p-2.5 overflow-hidden"
        style={{
          background: palette.bg,
          border: `1px dashed ${palette.border}`,
          minWidth: 120,
          minHeight: 60,
        }}
        onDoubleClick={() => setEditing(true)}
      >
        {/* Color swatch — click to cycle palette (only when selected) */}
        {selected && (
          <button
            onClick={cycleColor}
            title="Change color"
            className="absolute top-1 right-1 w-4 h-4 rounded-full border border-white/30"
            style={{ background: palette.border }}
          />
        )}

        {editing ? (
          <textarea
            ref={areaRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              e.stopPropagation()
              if (e.key === 'Escape') commit()
            }}
            className="w-full h-full bg-transparent resize-none outline-none nodrag"
            style={{ color: palette.text, fontSize }}
          />
        ) : (
          <div
            className="w-full h-full whitespace-pre-wrap break-words cursor-text"
            style={{ color: palette.text, fontSize }}
          >
            {data.text || <span className="opacity-40">Double-click to edit</span>}
          </div>
        )}
      </div>
    </>
  )
})
