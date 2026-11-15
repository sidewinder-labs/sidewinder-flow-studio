import type { Node, NodeProps } from '@xyflow/react'

/** Props of a custom node whose `data` is `T`. React Flow 12 types node props
 *  by the whole node, and requires `data` to be a record. */
export type DataNodeProps<T> = NodeProps<Node<T & Record<string, unknown>>>
