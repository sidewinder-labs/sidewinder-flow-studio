/**
 * Monaco from the app's own bundle. @monaco-editor/react otherwise loads the
 * editor from a CDN at run time, so it never appeared offline, which broke
 * every code editor in the desktop app without internet. Languages are loaded
 * on demand from the bundle (only Python and SQL are used); syntax colouring
 * needs no worker, so the base editor worker is the only one.
 */
import { loader } from '@monaco-editor/react'
import * as monaco from 'monaco-editor'
import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'

self.MonacoEnvironment = { getWorker: () => new EditorWorker() }
loader.config({ monaco })
