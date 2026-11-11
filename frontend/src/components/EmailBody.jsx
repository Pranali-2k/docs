import { useMemo } from 'react'
import { ChevronRight } from 'lucide-react'
import { cn } from '@/lib/cn'

const HTML_HINT = /<\s*(html|body|div|p|br|span|table|style|head|meta)\b[^>]*>/i
// Where quoted history starts: Outlook "From: … Sent:" headers, "-----Original Message-----", Gmail "On … wrote:"
const QUOTE_START = /^(?:-{2,}\s*Original Message\s*-{2,}|_{8,}|From:\s.+\n\s*(?:Sent|Date):\s.+|On .{6,200}wrote:\s*$)/im

/** Safety net for bodies stored before intake cleaned HTML: readable text via the browser's parser. */
function toText(raw) {
  if (!raw || !HTML_HINT.test(raw.slice(0, 20000))) return raw || ''
  try {
    const doc = new DOMParser().parseFromString(raw, 'text/html')
    doc.querySelectorAll('style,script,head,title').forEach((n) => n.remove())
    doc.querySelectorAll('br').forEach((n) => n.replaceWith('\n'))
    doc.querySelectorAll('p,div,li,tr,h1,h2,h3,h4,h5,h6').forEach((n) => n.append('\n'))
    doc.querySelectorAll('li').forEach((n) => n.prepend('• '))
    return (doc.body?.textContent || '')
      .replace(/ /g, ' ')
      .split('\n')
      .map((l) => l.replace(/[ \t]+/g, ' ').trim())
      .join('\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim()
  } catch {
    return raw
  }
}

function splitThread(text) {
  const m = QUOTE_START.exec(text)
  if (!m || m.index < 1) return { latest: text, history: '' }
  return { latest: text.slice(0, m.index).trim(), history: text.slice(m.index).trim() }
}

/** Email body as clean text: the newest message first, earlier messages in the thread folded away. */
export function EmailBody({ text, className }) {
  const { latest, history } = useMemo(() => splitThread(toText(text)), [text])
  const earlier = history ? (history.match(/^From:\s/gim) || []).length || 1 : 0
  return (
    <div className={cn('flex flex-col gap-2', className)}>
      <pre className="font-body text-sm leading-relaxed whitespace-pre-wrap break-words text-body">{latest || '(empty message)'}</pre>
      {history && (
        <details className="group border-t border-hairline/70 pt-2">
          <summary className="flex cursor-pointer list-none items-center gap-1 font-body text-xs font-medium text-muted hover:text-ink">
            <ChevronRight size={13} className="transition-transform group-open:rotate-90" />
            Earlier messages in this thread ({earlier})
          </summary>
          <pre className="mt-2 border-l-2 border-hairline pl-3 font-body text-xs leading-relaxed whitespace-pre-wrap break-words text-muted">{history}</pre>
        </details>
      )}
    </div>
  )
}
